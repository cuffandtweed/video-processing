# Zoom recording downloader: design

Date: 2026-09-29
Status: draft, awaiting user review

## Purpose

Download Zoom cloud recordings (MP4 video only) one at a time and upload each to the S3 bucket that `1_process_videos.py` reads from, so the existing two-step pipeline can process them. M4A audio files are ignored.

## Scope

- Source: Zoom cloud recordings for the account, via the Zoom API.
- Files kept: `file_type == "MP4"` and `status == "completed"`. Everything else (M4A, transcripts, chat, timeline files) is ignored.
- Date range: 2026-07-01 through the present (today's date at run time). Overridable with `--from` and `--to`.
- Destination: `s3://sandgarden-zoom-uploads/` in `us-east-2`, at the bucket root, where step 1 looks for videos. Overridable with `--bucket` and `--region`.

Out of scope: transcripts, audio, processing the videos (steps 1 and 2 do that), deleting anything from Zoom.

## Authentication

Zoom Server-to-Server OAuth. The script reads these environment variables and never accepts them as arguments or stores them in code:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`

It requests an access token with the `account_credentials` grant, caches it, and requests a new one shortly before expiry or on a 401. The Zoom app needs the cloud recording read scope for the account.

AWS access uses the standard boto3 credential chain, the same as the other scripts.

## Listing and pagination

Two nested loops.

1. **Outer loop: date windows.** Zoom limits each list request to a bounded date range, so the script splits `--from`..`--to` into windows of at most 30 days.
2. **Inner loop: pages.** For each window, request pages with the maximum page size and follow `next_page_token` until it is empty.

Each meeting in a page has a `recording_files` list. The script yields one item per file that passes the filter above, carrying meeting id, topic, start time, download URL and file size.

Listing is a generator, so the download loop starts on the first item without waiting for the full listing.

## Per-file processing (one at a time)

For each MP4:

1. Compute the S3 key: `<start date>_<topic slug>_<meeting id>.mp4`, at the bucket root. The topic is lowercased, non-alphanumerics become `_`, and the slug is capped in length. The start date and meeting id keep names unique when a topic repeats. Multiple MP4s on one meeting get a `_<n>` suffix.
2. Skip if the key already exists in S3 (`head_object`). This makes reruns resume where they stopped.
3. Stream the download to a temp file in chunks, authenticating with the access token. Nothing is held whole in memory.
4. Upload the temp file to S3 with `upload_file` (multipart handled by boto3).
5. Delete the temp file, whether or not the upload succeeded.

## Errors

- A failed download or upload is logged with meeting id and reason, then skipped. The script keeps going.
- At the end it prints the list of failures and exits non-zero if there were any. Rerunning retries them, since completed files are already in S3.
- HTTP 429 from Zoom: wait for `Retry-After` (or back off) and retry a few times.
- HTTP 401: refresh the token once and retry.
- Expired AWS credentials abort the run immediately with a clear message, since every later file would fail the same way.

## Testing

- Unit tests with fake Zoom responses cover: multi-page listing, empty pages, multiple date windows, file-type filtering, key naming and collisions, and skip-if-exists.
- Token refresh and 429 handling are tested with a fake HTTP layer.
- An end-to-end run needs real Zoom credentials and working AWS credentials. It is not possible until both are available.

## Files

- `zoom_download.py`: the script.
- `tests/test_zoom_download.py`: the unit tests.
- `requirements.txt`: add `requests` (boto3 is already there).

## Open items

- Zoom API credentials: to be provided by the user as environment variables.
- AWS credentials: expired; on hold until refreshed.
