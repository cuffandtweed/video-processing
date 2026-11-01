#!/usr/bin/env python3
"""
Find frustration with Liz in the calls she is NOT in, with exact quotes and timestamps.

    python frustrations.py                       # every call classified "mentioned" or "no sign" by presence.py
    python frustrations.py --limit 3             # try a few first

Reads the transcript .txt files (reports/transcripts), asks a model to pick out spoken lines where someone is frustrated:
with Liz directly ("Liz isn't giving me direction", "Liz sucks"), or with the lack of direction, decisions or clarity
that comes from leadership. Every quote is checked word for word against the transcript; one that is not there is dropped.
Saves mentions/topic_tags.json-style rows to mentions/liz_frustrations.json (merged into the page by moments_html.py).
Needs AWS sign-in (Bedrock only; the transcripts are read from disk).
"""
import argparse
import glob
import json
import os
import re
import time

import mentions as mn
import moments as mo
import presence as pr
from moments_html import call_label
from transcript_checks import cached_call, collect_stream, is_daily_cap_error, quote_in_transcript

REGION = "us-east-2"
NAME = "Frustration with Liz"
MODEL_ID = os.environ.get("MOMENTS_MODEL_ID") or "us.anthropic.claude-sonnet-4-6"
MODEL_CHAIN = [MODEL_ID, "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us.anthropic.claude-haiku-4-5-20251001-v1:0"]
capped_models = set()

PROMPT = """Below is the transcript of a recorded work call at a company. Lines look like "[hh:mm:ss] spk_N: words"
(the speaker labels are NOT names). Liz (Elizabeth Zalman) is the CEO and is NOT in this call.

Find every place where someone voices frustration, a complaint or resentment about Liz, or about the lack of direction,
decisions, communication, priorities or clarity from the leadership or from "management". Include:
  - directly about Liz ("Liz isn't giving me direction", "Liz sucks", "I never know what Liz wants")
  - indirectly about her or leadership ("nobody tells us what the plan is", "the direction keeps changing")
  - venting that is sarcastic or joking but really a complaint.
Do NOT include ordinary disagreement about technical choices, or neutral statements that Liz decided something.

Return ONE JSON object, nothing else: {"frustrations": [ ... ]}, each item
  {"timestamp": "hh:mm:ss" copied from the line,
   "quote": the key words copied EXACTLY from the transcript (at most about 40 words),
   "about": "Liz" or "leadership" or "direction" or "other",
   "why": one short sentence saying what the frustration is}
If there is none, return {"frustrations": []}. Do not invent anything; never guess names.

TRANSCRIPT:
"""

_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s*(?:(\S+?):\s+)?")


def log(msg):
    print(msg, flush=True)


def calls_to_search(statuses):
    return sorted(s for s, v in statuses.items() if pr.is_out(v))


def transcript_text(parsed):
    return "\n".join(f"[{s['clock']}] {s['speaker'] + ': ' if s['speaker'] else ''}{s['text']}" for s in parsed["spoken"])


def build_rows(stem, reply, transcript):
    """(rows, dropped): the reply's frustrations as page rows, keeping only quotes that appear in the transcript."""
    items = mo.parse_model_json(reply).get("frustrations", [])
    speaker_at = {}
    for line in transcript.splitlines():
        m = _LINE.match(line)
        if m:
            speaker_at[m.group(1)] = m.group(2) or ""
    label = call_label(stem)
    rows, dropped = [], 0
    for it in items:
        quote = str(it.get("quote", "")).strip()
        if not quote or not quote_in_transcript(quote, transcript):
            dropped += 1
            continue
        t = str(it.get("timestamp", ""))
        rows.append({"call": label["pretty"], "file": f"{stem}.mp4", "date": label["date"], "time": t, "end": "",
                     "kind": "spoken", "speaker": speaker_at.get(t, ""), "matched": "", "snippet": quote,
                     "note": str(it.get("why", "")).strip(), "about": str(it.get("about", "")).strip()})
    return rows, dropped


def ask_model(llm, text):
    for attempt in range(6):
        model = mo.first_available_model(MODEL_CHAIN, capped_models)
        if model is None:
            raise RuntimeError("Every model in the fallback chain has reached its daily token cap; try again tomorrow.")
        try:
            r = llm.converse_stream(modelId=model, messages=[{"role": "user", "content": [{"text": PROMPT + text}]}],
                                    inferenceConfig={"maxTokens": 6000, "temperature": 0.1})
            reply, stop = collect_stream(r["stream"])
            if stop == "max_tokens":
                log("  WARNING: the reply was cut off at the output limit")
            return reply
        except Exception as e:
            if is_daily_cap_error(e):
                capped_models.add(model)
                log(f"  {model} has reached its daily token cap; switching to the next model")
                continue
            if "Throttl" in str(e):
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise RuntimeError("Gave up after repeated throttling")


def statuses_for(transcripts_dir):
    import tag_presence
    return tag_presence.statuses(transcripts_dir)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcripts", default=os.path.join("reports", "transcripts"))
    ap.add_argument("--out", default="mentions")
    ap.add_argument("--limit", type=int, default=0, help="only the first N calls (to try it out)")
    a = ap.parse_args()
    import boto3
    from botocore.config import Config
    llm = boto3.client("bedrock-runtime", region_name=REGION, config=Config(read_timeout=600, retries={"max_attempts": 3}))
    stems = calls_to_search(statuses_for(a.transcripts))
    if a.limit:
        stems = stems[:a.limit]
    cache = os.path.join(a.out, "frustrations_calls", mo.cache_key(MODEL_ID, PROMPT))
    rows, dropped = [], 0
    for i, stem in enumerate(stems, 1):
        with open(os.path.join(a.transcripts, stem + ".txt"), encoding="utf-8") as f:
            text = transcript_text(mn.parse_transcript(f.read()))
        try:
            reply = cached_call(cache, [stem], lambda: ask_model(llm, text))
            got, d = build_rows(stem, reply, text)
        except ValueError as e:
            log(f"[{i}/{len(stems)}] {stem}: unreadable answer ({e}); skipped")
            continue
        rows += got
        dropped += d
        log(f"[{i}/{len(stems)}] {stem}: {len(got)} found" + (f", {d} dropped (quote not in transcript)" if d else ""))
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "liz_frustrations.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({NAME: rows}, f, ensure_ascii=False)
    log(f"{len(rows)} quotes in {len({r['file'] for r in rows})} calls ({dropped} dropped). Saved {path}")


if __name__ == "__main__":
    main()
