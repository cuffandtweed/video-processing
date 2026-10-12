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
import time

from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config

import moments as mo
from moments_html import write_html
from transcript_checks import cached_call, collect_stream, is_daily_cap_error, quote_in_transcript

REGION = "us-east-2"
BUCKET = "sandgarden-zoom-uploads"
MODEL_ID = os.environ.get("MOMENTS_MODEL_ID") or "us.anthropic.claude-sonnet-4-6"
OUT = "moments_proto"
WORKERS = int(os.environ.get("MOMENTS_WORKERS") or 4)   # how many calls to analyze at the same time
MIN_PAUSE_S = 4.0
MIN_SPEECH_CHARS = 200   # a call with less spoken text than this is treated as having no usable speech
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

Vocabulary this company uses (use it to understand the conversation; do not repeat it back):
GLOSSARY_HERE

TRANSCRIPT:
"""


def log(msg):
    print(msg, flush=True)


def load_glossary(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.txt")):
    """The company's own names for its products and projects, so the model does not guess what they mean."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "(none provided)"


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


# Preferred model first. If one reports "Too many tokens per day", the next is used for the rest of the run.
MODEL_CHAIN = [MODEL_ID, "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us.anthropic.claude-haiku-4-5-20251001-v1:0"]
capped_models = set()


def ask_model(llm, text):
    """Returns (the model's reply, which model wrote it)."""
    for attempt in range(6):
        model = mo.first_available_model(MODEL_CHAIN, capped_models)
        if model is None:
            raise RuntimeError("Every model in the fallback chain has reached its daily token cap; try again tomorrow.")
        try:
            r = llm.converse_stream(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": PROMPT.replace("GLOSSARY_HERE", load_glossary()) + text}]}],
                inferenceConfig={"maxTokens": 8000, "temperature": 0.2},
            )
            reply, stop = collect_stream(r["stream"])
            if stop == "max_tokens":
                log("  WARNING: the reply was cut off at the output limit")
            return reply, model
        except Exception as e:
            if is_daily_cap_error(e):
                capped_models.add(model)
                log(f"  {model} has reached its daily token cap; switching to the next model in the chain")
                continue
            if "Throttl" in str(e):
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise RuntimeError("Gave up after repeated throttling")


def analyze_call(llm, s3, stem):
    result = fetch_json(s3, stem)
    segments = mo.load_segments(result)
    if not mo.has_enough_speech(segments, MIN_SPEECH_CHARS):
        log(f"  no usable speech ({len(segments)} segment(s)): {stem}")
        return None
    pauses = mo.find_pauses(segments, MIN_PAUSE_S)
    # Talking-over is not used: BDA's speaker segments never overlap, so interruptions are invisible in this data.
    transcript = mo.format_transcript(segments, pauses, [])
    log(f"  {len(segments)} segments, {len(pauses)} silences >= {MIN_PAUSE_S:g}s; asking the model...")
    degraded = False
    model_used = None
    try:
        reply, model_used = ask_model(llm, transcript)
        data = mo.parse_model_json(reply)
    except ValueError as e:
        log(f"  could not read the model's answer ({e}); keeping the timing signals only")
        data = {"topics": [], "moments": []}
        degraded = True

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
    return {"moments": found, "topics": topics, "segments": len(segments), "model": model_used, "degraded": degraded}


def cache_dir():
    """Saved per-call results live in a folder named for the model, prompt and glossary, so editing any of them
    starts fresh instead of reusing results made under the old instructions."""
    return os.path.join(OUT, "calls", mo.cache_key(MODEL_ID, PROMPT, load_glossary()))


def analyze_call_saved(llm, s3, stem):
    """analyze_call, but a finished call is saved and reused, so an interrupted run resumes where it stopped."""
    path = os.path.join(cache_dir(), f"{stem}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        return None if saved.get("skip") else saved
    result = analyze_call(llm, s3, stem)
    if result is None or not result.get("degraded"):   # a call whose answer could not be read is retried next time
        os.makedirs(cache_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result if result else {"skip": True}, f, ensure_ascii=False)
    return result


def list_all_stems(s3):
    """Every call that has a saved BDA result in the bucket."""
    stems = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="transcripts/"):
        for o in page.get("Contents", []):
            name = o["Key"].rsplit("/", 1)[-1]
            if name.endswith(".json") and name.startswith("2026-"):
                stems.append(name[:-5])
    return sorted(stems)


CLUSTER_PROMPT = """Below are topic names taken from many recorded work conversations at one company. The same subject often
appears under different names. Group the names into canonical topics so that the same subject gets ONE name.

Rules:
- Aim for roughly 1 canonical topic per 3 to 10 names; do not leave most names alone, but NEVER merge unrelated
  subjects just to reduce the count. When in doubt, keep them separate.
- A canonical topic is a short, clear name of 1 to 4 words (a product, project, subject or activity).
- Topics about one of the company's products or projects (see the vocabulary below) get that product's name.
- Personal and emotional subjects (family, health, grief, pets, life events) must NOT be hidden in a generic
  bucket: give them their own specific canonical topics, such as "Grief and loss" or "Family and parenting".
  Light banter and jokes can go in "Humor and banter". Do not use a catch-all like "Small talk".
- Every name must appear in exactly one group, copied exactly as given.
- Reuse these existing canonical topics when they fit: {existing}

Vocabulary this company uses (names may be spelled or phrased differently from these):
{glossary}

Answer in plain text only, in exactly this format and nothing else: for each group, a line "## Canonical topic name",
then one line per topic name in that group, written "- name" (copied exactly as given). Example:

## Miranda
- miranda homepage
- miranda cost tracking demo

TOPIC NAMES:
{names}
"""


def _cluster_batch(llm, names, existing):
    """One grouping request for a batch of topic names; the answer is saved, so a rerun does not repeat it."""
    prompt = CLUSTER_PROMPT.format(existing=", ".join(existing) or "(none yet)", names="\n".join(names),
                                   glossary=load_glossary())

    def ask():
        r = llm.converse_stream(modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": prompt}]}],
                                inferenceConfig={"maxTokens": 16000, "temperature": 0.0})
        reply, stop = collect_stream(r["stream"])
        if stop == "max_tokens":
            raise RuntimeError("the grouping reply was cut off at the output limit")
        return reply

    return mo.parse_cluster_text(cached_call(os.path.join(OUT, "clusters"), (MODEL_ID, prompt), ask))


def cluster_topics(llm, topics, batch_size=150):
    """Merge per-call topic names into shared canonical topics. Returns {lowercased name: canonical}.

    Names the model skips get a second pass; any still left over keep their own name."""
    all_names = [n for batch in mo.batch_names([t.get("topic", "") for t in topics], 10 ** 9) for n in batch]
    mapping, canonical = {}, []

    def run(names):
        nonlocal canonical
        for i in range(0, len(names), batch_size):
            batch = names[i:i + batch_size]
            log(f"  grouping {len(batch)} topic names ({i + len(batch)} of {len(names)})...")
            part = _cluster_batch(llm, batch, canonical)
            for name, topic in part.items():
                mapping.setdefault(name, topic)
            canonical = sorted(set(canonical) | set(part.values()))

    run(all_names)
    left = mo.unplaced_names(all_names, mapping)
    if left:
        log(f"  {len(left)} names were not placed in a group; asking again for those")
        run(left)
    still = mo.unplaced_names(all_names, mapping)
    log(f"  topic names grouped: {len(all_names) - len(still)} of {len(all_names)}, into {len(set(mapping.values()))} topics; "
        f"{len(still)} keep their own name")
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

    # The page for skimming: moments and topics only, with readable call names and the video file to look in.
    page = write_html(OUT, mo.rank_moments(all_moments), all_topics)
    log(f"  skimmable page: {page}")

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
    if stems == ["--all"]:
        stems = list_all_stems(s3)
        log(f"{len(stems)} calls have saved BDA data")
    results, failed = {}, []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:   # a few calls at a time; each saves its own result
        futures = {pool.submit(analyze_call_saved, llm, s3, stem): stem for stem in stems}
        for fut in as_completed(futures):
            stem = futures[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:   # one bad call must not stop the other hundreds
                log(f"[{done}/{len(stems)}] FAILED {stem}: {type(e).__name__}: {str(e)[:200]}")
                failed.append(stem)
                continue
            log(f"[{done}/{len(stems)}] done {stem}")
            if r:
                results[stem] = r
    results = {s: results[s] for s in stems if s in results}   # keep the outputs in a stable order
    topic_map = cluster_topics(llm, [t for r in results.values() for t in r["topics"]])
    write_outputs(results, topic_map)
    total = sum(len(r["moments"]) for r in results.values())
    bad = sum(1 for r in results.values() for m in r["moments"] if m["verbatim"] == "NO")
    degraded = sum(1 for r in results.values() if r.get("degraded"))
    models = sorted({r.get("model") for r in results.values() if r.get("model")})
    log(f"\nDone: {len(results)} calls with speech, {total} moments ({bad} model quotes not found verbatim), "
        f"{degraded} calls where the model's answer could not be read, models used: {', '.join(models) or 'none'}. "
        f"Files are in {OUT}/")
    if failed:
        log(f"{len(failed)} call(s) FAILED and are missing; rerun to retry them:")
        for s in failed:
            log(f"  {s}")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python moments_prototype.py --all   |   python moments_prototype.py STEM [STEM ...]")
    sys.exit(main(sys.argv[1:]))
