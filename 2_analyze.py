#!/usr/bin/env python3
"""
Step 2: Read each transcript from S3 and have Claude (on Amazon Bedrock) write a
documentary research report: who's in it, what was discussed with timestamps,
themes, mood, great quotes. Then a master index across all videos.

Run in AWS CloudShell after step 1:
    python3 2_analyze.py YOUR-BUCKET-NAME [ONLY-TRANSCRIPTS-CONTAINING-THIS-TEXT]
Then download reports.zip (CloudShell: Actions > Download file > reports.zip).
"""
import os, re, sys, time, zipfile
import boto3
from botocore.config import Config
from transcript_checks import (batch_reports, cached_call, collect_stream, cutoff_note, has_speech,
                               index_model_note, is_daily_cap_error, model_note, no_speech_report, quote_note,
                               tag_model, tagged_models)

REGION = "us-east-2"
# Set the MODEL_ID environment variable to use a different model, e.g. "us.anthropic.claude-opus-5" once the account
# has access to it (today it returns "not available for this account").
MODEL_ID = os.environ.get("MODEL_ID") or "us.anthropic.claude-opus-4-6-v1"

if len(sys.argv) < 2:
    sys.exit("Usage: python3 2_analyze.py YOUR-BUCKET-NAME")
BUCKET = sys.argv[1]
FILTER = sys.argv[2] if len(sys.argv) > 2 else None   # optional: only analyze transcripts whose key contains this text

s3 = boto3.client("s3", region_name=REGION)
# Long reports from a large model can take minutes; botocore's default 60s read timeout is too short.
llm = boto3.client("bedrock-runtime", region_name=REGION, config=Config(read_timeout=1800, retries={"max_attempts": 2}))

REPORT_PROMPT = """You are a documentary story researcher. Below is an auto-generated transcript of a recorded Zoom call
(speakers are labeled spk_0, spk_1, etc. — the labels are NOT names). If a Zoom transcript with real participant
names is also provided, use it to figure out who each speaker label is.

Write a research report in Markdown with exactly these sections:

## Participants
Who was on the call. Map each speaker label to a real name if you can determine it (from the Zoom transcript,
introductions, or people addressing each other by name). If you can't, say "unknown" — never guess.

## Timeline of what was discussed
A chronological list. Each entry: timestamp (hh:mm:ss), then 1-2 sentences on what was discussed. Aim for one entry
every few minutes of conversation; more when the topic shifts quickly.

## Themes
The 3-7 big themes of the conversation, each with a one-paragraph explanation and pointers to timestamps.

## Mood and tone
How the conversation felt, and how it shifted over time (with timestamps). Note tension, humor, emotion, energy.

## Great quotes
10-25 verbatim quotes that would work on screen or in an edit, each with speaker and timestamp. Favor lines that are
vivid, emotional, funny, or crystallize a theme. Do not paraphrase — copy the words exactly from the transcript.

## Notes for the editor
Anything else useful: moments that would make good scenes, unresolved questions, references to other people/events,
audio problems or gaps in the transcript.

Be concrete and specific. Use timestamps everywhere."""

INDEX_PROMPT = """Below are research reports for a set of recorded Zoom calls for a documentary. Write a master index in Markdown:

## The calls
One line per call: filename, who was in it, one-sentence summary.

## Themes across all calls
The recurring themes, with which calls (and rough timestamps) best illustrate each.

## People
Every named person who appears, with which calls they're in and a one-line description of their role/perspective.

## Strongest material
The 20-30 best quotes or moments across everything, with call filename, speaker, and timestamp.

## Suggested story threads
3-5 possible narrative throughlines the footage could support, each pointing to specific calls and moments."""


MERGE_PROMPT = """Below are partial master indexes for a documentary, each written from a different batch of recorded Zoom
call reports. Merge them into ONE master index in Markdown with exactly these sections:

## The calls
One line per call across all batches: filename, who was in it, one-sentence summary. Keep every call.

## Themes across all calls
The recurring themes across the whole set, merging themes that appear in several batches, with which calls
(and rough timestamps) best illustrate each.

## People
Every named person who appears in any batch, with which calls they're in and a one-line description of their
role/perspective. Merge entries for the same person.

## Strongest material
The 20-30 best quotes or moments across everything, with call filename, speaker, and timestamp. Choose only from
quotes that appear in the partial indexes and copy them exactly as written there; do not reword or invent any.

## Suggested story threads
3-5 possible narrative throughlines the footage could support, each pointing to specific calls and moments.

Use only information in the partial indexes."""

# Max characters of reports sent in one index request (about 125k tokens). Sending hundreds of KB at once
# exceeds Bedrock's rate limit and the request is throttled; bigger sets are indexed in batches then merged.
INDEX_BATCH_CHARS = 500_000

# The master index can use a different model than the per-call reports (set INDEX_MODEL_ID), e.g. when the
# report model has hit its daily token quota ("Too many tokens per day").
INDEX_MODEL_ID = os.environ.get("INDEX_MODEL_ID") or MODEL_ID
# The final merge of partial indexes can use yet another model (set MERGE_MODEL_ID); saved batch results are reused.
MERGE_MODEL_ID = os.environ.get("MERGE_MODEL_ID") or INDEX_MODEL_ID


# When a model reports its daily token cap, later calls for it go to this fallback model for the rest of the run.
FALLBACK_MODEL_ID = os.environ.get("FALLBACK_MODEL_ID") or "us.anthropic.claude-opus-4-5-20251101-v1:0"
capped_models = set()   # models that have hit their daily cap during this run
last_model_used = None  # which model wrote the most recent reply (so reports can say when a fallback did)


def ask(prompt, text, max_tokens=16000, model_id=None):
    global last_model_used
    wanted = model_id or MODEL_ID
    for attempt in range(6):
        model = FALLBACK_MODEL_ID if wanted in capped_models else wanted
        try:
            # Stream the reply: a long non-streaming request sits silent and can be dropped by the network.
            r = llm.converse_stream(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": prompt + "\n\n---\n\n" + text}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3},
            )
            reply, stop_reason = collect_stream(r["stream"])
            last_model_used = model
            if stop_reason == "max_tokens":
                print(f"  WARNING: a reply from {model} was cut off at the {max_tokens}-token output limit.")
            return reply + cutoff_note(stop_reason)
        except Exception as e:
            if is_daily_cap_error(e):
                if model == FALLBACK_MODEL_ID:
                    raise RuntimeError(f"Daily token cap reached on both {wanted} and the fallback {FALLBACK_MODEL_ID}; "
                                       "try again tomorrow.") from e
                print(f"  {model} has reached its daily token cap; using {FALLBACK_MODEL_ID} for the rest of this run.")
                capped_models.add(wanted)
                continue
            if "Throttl" in str(e) or "TooMany" in str(e):
                time.sleep(15 * (attempt + 1)); continue
            raise
    raise RuntimeError("Gave up after repeated throttling")


def s3_text(key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", "replace")


def list_keys(prefix, suffix):
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        out += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(suffix)]
    return out


def find_zoom_vtt(stem):
    """If a Zoom .vtt transcript with the same name was uploaded next to the video, use it for names."""
    for key in list_keys("", ".vtt"):
        if key.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == stem.lower():
            return s3_text(key)
    return None


os.makedirs("reports", exist_ok=True)
transcripts = list_keys("transcripts/", ".txt")
if FILTER:
    transcripts = [k for k in transcripts if FILTER in k]
print(f"Found {len(transcripts)} transcript(s)")

reports = []   # (stem, report) for calls that have spoken content
silent = []    # stems whose transcript has no speech, so no model report is written for them
texts = {}
for key in transcripts:
    stem = key.rsplit("/", 1)[-1][:-4]
    local = f"reports/{stem}.md"
    transcript = s3_text(key)
    texts[stem] = transcript
    spoken = has_speech(transcript)
    if os.path.exists(local):
        print(f"skip (already done): {stem}")
        report = open(local, encoding="utf-8").read()
    else:
        if spoken:
            text = f"FILENAME: {stem}\n\n{transcript}"
            vtt = find_zoom_vtt(stem)
            if vtt:
                text += "\n\n=== ZOOM'S OWN TRANSCRIPT (has participant names) ===\n" + vtt
            print(f"analyzing: {stem} ...")
            body = ask(REPORT_PROMPT, text)
            report = f"# {stem}\n\n" + model_note(last_model_used, MODEL_ID) + body
            report += quote_note(report, transcript)
        else:
            print(f"no spoken content, not sending to the model: {stem}")
            report = no_speech_report(stem)
        open(local, "w", encoding="utf-8").write(report)
        s3.put_object(Bucket=BUCKET, Key=f"reports/{stem}.md", Body=report.encode())
        print(f"DONE {stem}")
    (reports if spoken else silent).append((stem, report))

if reports or silent:
    print("Building master index across all calls...")
    index = "# Master index — all calls\n\n"
    if reports:
        batches = batch_reports(reports, INDEX_BATCH_CHARS)
        models_used = set()   # every model that wrote any part of the index, including cached batches from earlier runs
        if len(batches) == 1:
            body = ask(INDEX_PROMPT, "\n\n\n".join(batches[0]), max_tokens=24000, model_id=INDEX_MODEL_ID)
            models_used.add(last_model_used)
        else:
            partials = []
            for i, batch in enumerate(batches, 1):
                print(f"  index batch {i} of {len(batches)} ({len(batch)} calls)...")
                batch_text = "\n\n\n".join(batch)
                partials.append(cached_call(
                    "reports/.index_partials", (INDEX_MODEL_ID, INDEX_PROMPT, batch_text),
                    lambda: tag_model(ask(INDEX_PROMPT, batch_text, max_tokens=24000, model_id=INDEX_MODEL_ID),
                                      last_model_used)))
            models_used |= tagged_models(partials)
            print("  merging the partial indexes...")
            merged = "\n\n\n".join(f"===== PARTIAL INDEX {i} of {len(partials)} =====\n{p}" for i, p in enumerate(partials, 1))
            # The merged index lists every call, so it needs room; the reply is streamed, so length is no timeout risk.
            body = ask(MERGE_PROMPT, merged, max_tokens=40000, model_id=MERGE_MODEL_ID)
            models_used.add(last_model_used)
        index += index_model_note(models_used, MODEL_ID)
        index += body + quote_note(body, "\n".join(texts[s] for s, _ in reports))
    if silent:
        index += "\n\n## Calls with no spoken content\n" + "\n".join(f"- `{s}`" for s, _ in silent) + "\n"
    open("reports/00_MASTER_INDEX.md", "w", encoding="utf-8").write(index)
    s3.put_object(Bucket=BUCKET, Key="reports/00_MASTER_INDEX.md", Body=index.encode())

    # Also copy the transcripts in, and zip everything for easy download.
    os.makedirs("reports/transcripts", exist_ok=True)
    for key in transcripts:
        open("reports/transcripts/" + key.rsplit("/", 1)[-1], "w", encoding="utf-8").write(s3_text(key))
    with zipfile.ZipFile("reports.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk("reports"):
            for f in files:
                z.write(os.path.join(root, f))
    print("\nAll done. Download reports.zip: CloudShell menu Actions > Download file > type  reports.zip")
