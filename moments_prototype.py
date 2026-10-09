#!/usr/bin/env python3
"""
Prototype: find topics and "magical moments" in a few calls.

For each call it combines two kinds of signal:
  * timing (from the saved BDA data): long silences and people talking over each other
  * the words (read by a model): emotion, disagreement, laughter, breakthroughs, humor, tension

and writes, into moments_proto/:
  moments.md  - the strongest moments across the calls, then per call
  topics.md   - each topic with where it came up
  moments.csv - every moment, for sorting and filtering

    python moments_prototype.py STEM [STEM ...]

Needs AWS sign-in (S3 for the saved BDA data, Bedrock for the model).
"""
import csv
import json
import os
import sys

import boto3
from botocore.config import Config

import moments as mo
from transcript_checks import collect_stream, quote_in_transcript

REGION = "us-east-2"
BUCKET = "sandgarden-zoom-uploads"
MODEL_ID = os.environ.get("MOMENTS_MODEL_ID") or "us.anthropic.claude-sonnet-4-6"
OUT = "moments_proto"
MIN_PAUSE_S = 4.0
MIN_OVERLAP_MS = 800

PROMPT = """You are helping a documentary editor find the best material in a recorded Zoom call. Below is the transcript.
Lines look like "[hh:mm:ss] spk_N: words" (the speaker labels are NOT names). A line like "[— Ns of silence —]"
is a gap of N seconds before the next line, taken from the audio timing. Use it as context only: a silence right
after something heavy can mark an important moment, but in working meetings most silences are just people on
mute, sharing a screen or typing.

Return ONE JSON object, nothing else, with exactly these keys.

"topics": a list of the distinct topics discussed, in order. Each item:
  {"topic": short name of 1-4 words, reused consistently for the same subject,
   "start": "hh:mm:ss", "end": "hh:mm:ss", "summary": one sentence}

"moments": the genuinely striking moments (usually 3 to 12; fewer if the call has little). Each item:
  {"type": one of "emotional", "disagreement", "laughter", "breakthrough", "humor", "tension",
   "timestamp": "hh:mm:ss" copied from the line where it happens,
   "quote": the key words, copied EXACTLY from the transcript (no changes, at most about 40 words),
   "why": one sentence on why it is striking,
   "intensity": a whole number 1 (mild) to 5 (unmissable)}

Definitions: emotional = vulnerability, grief, joy, frustration; disagreement = real pushback or an argument;
laughter = shared laughter or banter; breakthrough = an insight, a decision, an "aha"; humor = a funny line;
tension = awkwardness or strain. Do not invent anything. If nothing stands out, return an empty "moments" list.
Never guess people's names; refer to speakers by their labels.

TRANSCRIPT:
"""


def log(msg):
    print(msg, flush=True)


def fetch_json(s3, stem):
    """The saved BDA result for a call, cached under moments_proto/json/."""
    path = os.path.join(OUT, "json", f"{stem}.json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = s3.get_object(Bucket=BUCKET, Key=f"transcripts/{stem}.json")["Body"].read()
        with open(path, "wb") as f:
            f.write(body)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ask_model(llm, text):
    r = llm.converse_stream(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": PROMPT + text}]}],
        inferenceConfig={"maxTokens": 8000, "temperature": 0.2},
    )
    reply, stop = collect_stream(r["stream"])
    if stop == "max_tokens":
        log("  WARNING: the reply was cut off at the output limit")
    return reply


def analyze_call(llm, s3, stem):
    result = fetch_json(s3, stem)
    segments = mo.load_segments(result)
    if not segments:
        log(f"  no spoken content: {stem}")
        return None
    pauses = mo.find_pauses(segments, MIN_PAUSE_S)
    # Talking-over is not used: BDA's speaker segments never overlap, so interruptions are invisible in this data.
    transcript = mo.format_transcript(segments, pauses, [])
    log(f"  {len(segments)} segments, {len(pauses)} silences >= {MIN_PAUSE_S:g}s; asking {MODEL_ID}...")
    try:
        data = mo.parse_model_json(ask_model(llm, transcript))
    except ValueError as e:
        log(f"  could not read the model's answer ({e}); keeping the timing signals only")
        data = {"topics": [], "moments": []}

    found = []
    for item in data.get("moments", []):
        quote = str(item.get("quote", "")).strip()
        found.append({
            "call": stem, "timestamp": str(item.get("timestamp", "")), "type": str(item.get("type", "other")),
            "score": max(1, min(5, int(item.get("intensity", 3) or 3))), "quote": quote,
            "why": str(item.get("why", "")), "source": "model",
            "verbatim": "yes" if quote_in_transcript(quote, transcript) else "NO",
        })
    # A silence counts for more when it sits next to a moment the model flagged as at least moderately strong.
    flagged = [mo.parse_clock(m["timestamp"]) for m in found if m["score"] >= 3]
    for s in mo.silence_moments(pauses, flagged, stem):
        found.append({**s, "verbatim": "n/a"})
    topics = [{**t, "call": stem} for t in data.get("topics", [])]
    return {"moments": found, "topics": topics, "segments": len(segments)}


CLUSTER_PROMPT = """Below are topic names taken from many recorded work conversations at one company. The same subject often
appears under different names. Group the names into canonical topics so that the same subject gets ONE name.

Rules:
- Aim for roughly 1 canonical topic per 5 to 15 names; do not leave most names alone.
- A canonical topic is a short, clear name of 1 to 4 words (a product, project, person-neutral subject or activity).
- Every name must appear in exactly one group, copied exactly as given.
- Names that are one-off chit-chat can go in a group called "Small talk".
- Reuse these existing canonical topics when they fit: {existing}

Return ONE JSON object, nothing else: {{"topics": [{{"canonical": "name", "names": ["...", "..."]}}]}}

TOPIC NAMES:
{names}
"""


def cluster_topics(llm, topics, batch_size=400):
    """Merge per-call topic names into shared canonical topics. Returns {lowercased name: canonical}."""
    mapping, canonical = {}, []
    for batch in mo.batch_names([t.get("topic", "") for t in topics], batch_size):
        log(f"  clustering {len(batch)} topic names...")
        prompt = CLUSTER_PROMPT.format(existing=", ".join(canonical) or "(none yet)", names="\n".join(batch))
        r = llm.converse_stream(modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": prompt}]}],
                                inferenceConfig={"maxTokens": 16000, "temperature": 0.0})
        reply, stop = collect_stream(r["stream"])
        if stop == "max_tokens":
            log("  WARNING: the clustering reply was cut off; unplaced names keep their own name")
        try:
            part = mo.build_topic_map(mo.parse_model_json(reply))
        except ValueError as e:
            log(f"  could not read the clustering answer ({e}); this batch keeps its own names")
            continue
        mapping.update(part)
        canonical = sorted(set(canonical) | set(part.values()))
    return mapping


def write_outputs(results, topic_map):
    os.makedirs(OUT, exist_ok=True)
    all_moments = [m for r in results.values() for m in r["moments"]]
    all_topics = mo.apply_topic_map([t for r in results.values() for t in r["topics"]], topic_map)

    with open(os.path.join(OUT, "moments.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["call", "timestamp", "type", "score", "source", "verbatim", "quote", "why"])
        w.writeheader()
        for m in mo.rank_moments(all_moments):
            w.writerow({k: m.get(k, "") for k in w.fieldnames})

    def line(m):
        flag = "" if m["verbatim"] in ("yes", "n/a") else "  **(quote not found verbatim)**"
        return f'- **{m["call"][:40]}** @ `{m["timestamp"]}` · {m["type"]} · score {m["score"]}{flag}\n  > {m["quote"]}\n  {m["why"]}'

    with open(os.path.join(OUT, "moments.md"), "w", encoding="utf-8") as f:
        f.write("# Moments (prototype)\n\nTiming signals (silence, talking-over) come from the audio timestamps; the rest "
                "are the model's reading of the words. Score 1 = mild, 5 = unmissable.\n\n## Strongest across all calls\n\n")
        for m in mo.rank_moments(all_moments, top_n=30):
            f.write(line(m) + "\n")
        for stem, r in results.items():
            f.write(f"\n## {stem}\n\n")
            for m in mo.rank_moments(r["moments"], top_n=15):
                f.write(line(m) + "\n")

    by_topic = {}
    for t in all_topics:
        by_topic.setdefault(t["canonical"], []).append(t)
    with open(os.path.join(OUT, "topics.md"), "w", encoding="utf-8") as f:
        f.write("# Topics (prototype)\n\nEach topic with where it came up. Names are merged across calls by the model, "
                "so the same subject appears in one place; the original name is shown for each mention.\n")
        for name, items in sorted(by_topic.items(), key=lambda kv: (-len({t["call"] for t in kv[1]}), kv[0].lower())):
            calls = len({t["call"] for t in items})
            f.write(f"\n## {name or '(unnamed)'}  ({len(items)} mention{'s' if len(items) != 1 else ''} in "
                    f"{calls} call{'s' if calls != 1 else ''})\n\n")
            for t in sorted(items, key=lambda x: (x["call"], str(x.get("start", "")))):
                orig = str(t.get("topic", "")).strip()
                tag = "" if orig.lower() == name.lower() else f' _(as "{orig}")_'
                f.write(f'- **{t["call"][:40]}** `{t.get("start", "?")}`–`{t.get("end", "?")}`{tag}: {t.get("summary", "")}\n')


def main(stems):
    s3 = boto3.client("s3", region_name=REGION)
    llm = boto3.client("bedrock-runtime", region_name=REGION, config=Config(read_timeout=600, retries={"max_attempts": 2}))
    results = {}
    for stem in stems:
        log(f"analyzing {stem}")
        r = analyze_call(llm, s3, stem)
        if r:
            results[stem] = r
    topic_map = cluster_topics(llm, [t for r in results.values() for t in r["topics"]])
    write_outputs(results, topic_map)
    total = sum(len(r["moments"]) for r in results.values())
    bad = sum(1 for r in results.values() for m in r["moments"] if m["verbatim"] == "NO")
    log(f"\nDone: {len(results)} calls, {total} moments ({bad} model quotes not found verbatim). Files are in {OUT}/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python moments_prototype.py STEM [STEM ...]")
    main(sys.argv[1:])
