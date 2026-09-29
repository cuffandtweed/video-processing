#!/usr/bin/env python3
"""
Download Zoom cloud-recording MP4s one at a time and upload each to S3.

Auth: Zoom Server-to-Server OAuth. Set these environment variables first:
    ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET
AWS access uses the normal boto3 credential chain (e.g. AWS_PROFILE).

    python zoom_download.py --list-only --limit 3     # look, download nothing
    python zoom_download.py --limit 1                 # first real file
    python zoom_download.py                           # everything since 2026-07-01
"""
import argparse
import itertools
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta

import boto3
import requests
from botocore.exceptions import ClientError, NoCredentialsError

TOKEN_URL = "https://zoom.us/oauth/token"
API = "https://api.zoom.us/v2"
DEFAULT_FROM = date(2026, 7, 1)
DEFAULT_BUCKET = "sandgarden-zoom-uploads"
DEFAULT_REGION = "us-east-2"


def date_windows(start, end, days=30):
    """Split start..end (inclusive) into consecutive windows of at most `days` days."""
    windows = []
    cur = start
    while cur <= end:
        w_end = min(cur + timedelta(days=days - 1), end)
        windows.append((cur, w_end))
        cur = w_end + timedelta(days=1)
    return windows


class ZoomAuth:
    """Server-to-Server OAuth access token, cached until shortly before it expires."""

    def __init__(self, account_id, client_id, client_secret, session=None, clock=time.time):
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._session = session or requests.Session()
        self._clock = clock
        self._token = None
        self._expires_at = 0

    def token(self):
        if self._token is None or self._clock() >= self._expires_at - 60:
            r = self._session.post(
                TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": self.account_id},
                auth=(self.client_id, self.client_secret),
                timeout=30,
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._expires_at = self._clock() + body.get("expires_in", 3600)
        return self._token

    def invalidate(self):
        self._token = None


class ZoomClient:
    """Authenticated requests with one token refresh on 401 and backoff on 429."""

    def __init__(self, auth, session=None, sleep=time.sleep, max_retries=5):
        self.auth = auth
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries

    def request(self, method, url, **kw):
        refreshed = False
        for attempt in range(self.max_retries):
            headers = {"Authorization": f"Bearer {self.auth.token()}"}
            r = self.session.request(method, url, headers=headers, timeout=60, **kw)
            if r.status_code == 401 and not refreshed:
                self.auth.invalidate()
                refreshed = True
                continue
            if r.status_code == 429:
                self.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"Gave up after {self.max_retries} attempts: {url}")


def iter_recordings(client, start, end, user="me"):
    """Yield one dict per completed MP4, walking date windows and result pages."""
    for w_start, w_end in date_windows(start, end):
        token = None
        while True:
            params = {"from": w_start.isoformat(), "to": w_end.isoformat(), "page_size": 300}
            if token:
                params["next_page_token"] = token
            body = client.request("GET", f"{API}/users/{user}/recordings", params=params).json()
            for m in body.get("meetings", []):
                for f in m.get("recording_files", []):
                    if f.get("file_type") == "MP4" and f.get("status") == "completed":
                        yield {
                            "meeting_id": m["id"],
                            "topic": m.get("topic", ""),
                            "start_time": m["start_time"],
                            "file_id": f["id"],
                            "download_url": f["download_url"],
                            "size": f.get("file_size", 0),
                        }
            token = body.get("next_page_token") or None
            if not token:
                break


def slugify(text, max_len=60):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:max_len].rstrip("_") or "untitled"


def make_key(item, part=0):
    """`<date>_<topic>_<meeting id>.mp4`; extra MP4s on one meeting get `_1`, `_2`, ..."""
    suffix = f"_{part}" if part else ""
    return f"{item['start_time'][:10]}_{slugify(item['topic'])}_{item['meeting_id']}{suffix}.mp4"


def key_exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


class AwsCredentialsError(Exception):
    """AWS credentials are missing or expired; every later file would fail the same way."""


def _is_aws_auth_error(e):
    if isinstance(e, NoCredentialsError):
        return True
    text = str(e)
    return any(s in text for s in ("ExpiredToken", "InvalidToken", "TokenRefreshRequired", "InvalidAccessKeyId"))


def download_to(client, url, path, chunk=1 << 20):
    """Stream a Zoom file to `path` without holding it in memory."""
    r = client.request("GET", url, stream=True)
    try:
        with open(path, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                f.write(part)
    finally:
        r.close()


def process_all(items, client, s3, bucket, tmpdir=None, log=print):
    """One file at a time: skip if in S3, else download, upload, delete the temp file."""
    uploaded = skipped = 0
    failures = []
    parts = {}
    for it in items:
        n = parts.get(it["meeting_id"], 0)
        parts[it["meeting_id"]] = n + 1
        key = make_key(it, n)
        try:
            if key_exists(s3, bucket, key):
                log(f"skip (already in S3): {key}")
                skipped += 1
                continue
            fd, path = tempfile.mkstemp(suffix=".mp4", dir=tmpdir)
            os.close(fd)
            try:
                log(f"downloading: {key} ({it['size'] / 1e6:.1f} MB)")
                download_to(client, it["download_url"], path)
                s3.upload_file(path, bucket, key)
            finally:
                os.remove(path)
            log(f"uploaded: {key}")
            uploaded += 1
        except Exception as e:
            if _is_aws_auth_error(e):
                raise AwsCredentialsError(f"AWS credentials missing or expired: {e}") from e
            log(f"FAILED: {key}: {e}")
            failures.append((key, str(e)))
    return {"uploaded": uploaded, "skipped": skipped, "failures": failures}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Download Zoom MP4 recordings one at a time and upload them to S3.")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--from", dest="start", type=date.fromisoformat, default=DEFAULT_FROM,
                   help="first day to include, YYYY-MM-DD (default 2026-07-01)")
    p.add_argument("--to", dest="end", type=date.fromisoformat, default=None,
                   help="last day to include, YYYY-MM-DD (default today)")
    p.add_argument("--user", default="me", help="Zoom user id or email whose recordings to list (default: me)")
    p.add_argument("--limit", type=int, default=None, help="stop after N files (for testing)")
    p.add_argument("--list-only", action="store_true", help="list matching recordings, download nothing")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    missing = [v for v in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET") if not os.environ.get(v)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))

    end = args.end or date.today()
    auth = ZoomAuth(os.environ["ZOOM_ACCOUNT_ID"], os.environ["ZOOM_CLIENT_ID"], os.environ["ZOOM_CLIENT_SECRET"])
    client = ZoomClient(auth)
    items = iter_recordings(client, args.start, end, user=args.user)
    if args.limit is not None:
        items = itertools.islice(items, args.limit)

    if args.list_only:
        count = 0
        for it in items:
            print(f"{make_key(it)}  ({it['size'] / 1e6:.1f} MB)")
            count += 1
        print(f"{count} recording(s) match {args.start} .. {end}")
        return 0

    s3 = boto3.client("s3", region_name=args.region)
    try:
        result = process_all(items, client, s3, args.bucket)
    except AwsCredentialsError as e:
        print(f"\nStopped: {e}\nRefresh with:  aws sso login --profile <your profile>")
        return 1
    print(f"\nDone: {result['uploaded']} uploaded, {result['skipped']} skipped, {len(result['failures'])} failed")
    for key, err in result["failures"]:
        print(f"  FAILED {key}: {err}")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
