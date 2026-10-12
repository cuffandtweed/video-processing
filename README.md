# Documentary Zoom videos: transcripts, reports, and "where to look"

Turns a pile of Zoom cloud recordings into things a filmmaker can use:

1. **Transcripts** of every call (with speaker labels, timestamps, chapter summaries).
2. **A research report per call** (participants, timeline, themes, mood, great quotes) and **a master index** across all calls.
3. **A "moments and topics" page**: the strongest moments (emotional, disagreement, laughter, breakthroughs, humor,
   tension, silences) and the topics discussed, with the video file and time to look at.

Everything runs against one AWS account (region `us-east-2`) and one S3 bucket (`sandgarden-zoom-uploads`).

## How it fits together

```
Zoom cloud recordings
   |  zoom_download.py            download each MP4, one at a time, upload to S3
   v
S3 bucket (MP4s at the root)
   |  1_process_videos.py         Amazon Bedrock Data Automation (BDA) -> transcripts/<call>.json + .txt
   v
transcripts/ in S3
   |  2_analyze.py                Claude on Bedrock -> reports/<call>.md, reports/00_MASTER_INDEX.md, reports.zip
   |  moments_prototype.py        Claude on Bedrock -> moments_proto/ (moments.html, topics, CSV, JSON)
   v
moments_html.py                   rebuilds the page with links for Google Drive or a local folder (no model needed)
```

A "call" is identified by its file name without `.mp4`, e.g. `2026-07-22_152953_eng_standup_95311646981`
(date, start time in UTC, topic, Zoom meeting id).

## Setup

- Python 3 and `pip install -r requirements.txt` (boto3, requests). For tests: `pip install -r requirements-dev.txt`.
- **AWS sign-in** (IAM Identity Center): `aws sso login --use-device-code --profile AWSPowerUserAccess-567097740953`,
  then `set AWS_PROFILE=AWSPowerUserAccess-567097740953`. The device-code form shows a short code to type at the
  address it prints; it avoids the localhost redirect that sometimes fails.
- **Zoom**: a Server-to-Server OAuth app with the cloud-recording read scopes. Put the three values in environment
  variables, never in files or chat: `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`.
- Bedrock models used: Claude Opus 4.6 (reports), Opus 4.5 (fallback), Sonnet 4.6 (moments). Newer models
  (Opus 4.7+, Sonnet 5+) were refused for this account ("not available for this account").

## Running it

```
# 1. Recordings -> S3 (MP4 only; the date range defaults to 2026-07-01 through today)
python zoom_download.py --account --from 2026-08-01 --to 2026-08-15            # --list-only to preview, --limit N to test
# 2. Transcripts (3 BDA jobs at a time; resumable; reuses finished results)
python 1_process_videos.py sandgarden-zoom-uploads
# 3. Reports and one master index. The 2nd argument limits which calls (a substring of the file name)
python 2_analyze.py sandgarden-zoom-uploads 2026-08-
# 4. Moments and topics
python moments_prototype.py --all
# 5. The page, for wherever the videos live
python moments_html.py moments_proto --links drive --name moments_drive.html
python moments_html.py moments_proto --links local --base . --name moments_local.html
```

Every stage is **resumable**: files already in S3 are skipped, finished BDA jobs are recovered, existing reports are
kept, and each call's moments result is saved. If a run stops for any reason, run the same command again.

Tests: `python -m pytest tests -q`.

## What each file is

| File | What it does |
|---|---|
| `zoom_download.py` | Lists recordings through the Zoom API (30-day windows, paged), downloads each MP4 to a temp file, uploads it to S3, deletes the temp file. Keys are `<date>_<HHMMSS>_<topic>_<meeting id>.mp4`. `--account` lists the whole account. |
| `1_process_videos.py` | Sends each video through Bedrock Data Automation. Caps itself at 3 concurrent jobs (the account limit), waits for a slot, and never resubmits a job whose output already exists. |
| `2_analyze.py` | Writes a report per call and a master index. Skips silent calls, checks every quote against the transcript, streams model replies, falls back to Opus 4.5 when Opus 4.6 hits its daily token cap, builds the index in batches then merges. Env vars: `MODEL_ID`, `INDEX_MODEL_ID`, `MERGE_MODEL_ID`, `FALLBACK_MODEL_ID`. |
| `transcript_checks.py` | Helpers: is there any speech, is a quote word for word in the transcript, stream reading, batching, saved results. |
| `moments_prototype.py` | One model pass per call for moments and topics, plus a timing pass for silences; then groups topic names across calls. |
| `moments.py` | The timing logic (silences) and the pure helpers used above. |
| `moments_html.py` | Builds the single-file page (`Moments` and `Topics` tabs, filters, starring) from saved JSON. |
| `glossary.txt` | The company's own names for its products. Read by the moments step so, for example, "Doc" is understood as the Doc Holiday product and not the documentary. **Edit this to improve topics.** |

## Where things end up

- **S3** `sandgarden-zoom-uploads`: MP4s at the root; `transcripts/` (`.json` from BDA, `.txt` readable); `reports/`;
  `bda-output/` (raw BDA output, also used to recover jobs).
- **Local, per run** (git-ignored): `reports/` (one `.md` per call, `00_MASTER_INDEX.md`, `transcripts/`), `reports.zip`,
  `moments_proto/` (`moments.html`, `topics.md`, `moments.csv`, `moments.json`, `topics.json`, saved per-call results).

## Things we learned the hard way

- **AWS sign-ins expire** after roughly 2 to 8 hours, and a script already running cannot pick up a new login. If you
  need to sign in again mid-run, stop the run first, sign in, then restart it.
- **BDA allows 3 concurrent jobs.** Its speed varies a lot (a few minutes per job some days, about an hour on others).
  The scripts queue and resume, so just let them run.
- **Bedrock has per-model daily token caps** ("Too many tokens per day"). Opus 4.6 ran out after roughly 40 reports
  plus an index in a day. The scripts fall back automatically, and say so in the output.
- **Long silent model requests get dropped by the network.** All model calls stream their replies, which fixed
  requests that used to hang for 30+ minutes.
- **Keep the computer awake** during long runs; standby freezes a running script.
- On Windows, if `python` suddenly says it is not found, call the interpreter by its full path.

## Quality: what to trust

- **Quotes** in reports are checked against the transcript: about 87 to 90 percent are word for word; the rest are
  real statements with fillers removed or lightly merged. The page flags any quote it could not match.
- **The master indexes are the weakest on quotes** (a quarter or more not word for word). Use them as a map; take
  exact wording from the per-call reports or transcripts.
- **Speaker names are inference.** The audio has no speaker identities. Names come from on-screen captions and what
  people say, and can be wrong. The moments page shows "Speaker 1, 2..." for that reason. Zoom's own `.vtt`
  transcripts, if available, would fix this (step 3 uses a matching `.vtt` next to the video).
- **Silences** only count when next to a strong moment; long gaps in working meetings are usually people muted or away.
  Interruptions (talking over) cannot be detected from BDA's data.

## Privacy

The calls contain personal and company-sensitive material (health, family, personnel, finances, strategy). The
reports and the moments page quote it word for word. Decide who gets them. Nothing here sends data anywhere except
to AWS (S3 and Bedrock) and, if you choose, to Google Drive. Credentials are never stored in the repo.
