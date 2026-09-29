# Zoom Downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A script that lists Zoom cloud-recording MP4s through the Zoom API (with pagination), then downloads each one locally and uploads it to S3, one file at a time.

**Architecture:** One module, `zoom_download.py`, built from small functions: date windowing, a token-caching auth class, a client that handles 401 and 429, a paginated generator, key naming, and a sequential download-upload loop. All network access goes through injectable session, client and S3 objects, so tests use fakes and need no credentials.

**Tech Stack:** Python 3, `requests`, `boto3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-29-zoom-downloader-design.md`

## Global Constraints

- Only files with `file_type == "MP4"` and `status == "completed"` are kept. Everything else is ignored.
- Default date range: `2026-07-01` through today (run date). Overridable with `--from` and `--to`.
- Date requests are split into windows of at most 30 days; each window is paged with `next_page_token` until it is empty. Page size 300.
- Default destination: bucket `sandgarden-zoom-uploads`, region `us-east-2`, at the bucket root. Overridable with `--bucket` and `--region`.
- S3 key: `<YYYY-MM-DD>_<topic slug>_<meeting id>.mp4`. A second MP4 on the same meeting gets `_1`, a third `_2`, before `.mp4`.
- Credentials come only from environment variables `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`. Never accept them as arguments or write them anywhere.
- One file at a time: download to a temp file, upload, delete the temp file (always, even on failure).
- Skip a file whose S3 key already exists.
- Per-file failures are logged and skipped; the run ends by listing them and exiting non-zero. Expired or missing AWS credentials abort the run immediately.
- HTTP 401: refresh the token once and retry. HTTP 429: wait `Retry-After` seconds (or back off) and retry, up to 5 attempts.
- Additions beyond the spec, both to support a safe first real run: `--list-only` (list what would be downloaded, download nothing) and `--limit N` (stop after N files).
- The working folder is not a git repository, and pushing to GitHub is blocked on authentication. The commit step is done in the local clone at `C:\Users\lizsq\AppData\Local\Temp\claude\C--Users-lizsq-OneDrive-Desktop-Liznyc\46457e9a-07cb-4c1b-b679-af16103d8272\scratchpad\video-processing`. It is a local commit only.

## File Structure

- `zoom_download.py`: the whole script.
- `tests/test_zoom_download.py`: unit tests with fakes.
- `requirements.txt`: add `requests`.
- `requirements-dev.txt`: `pytest`.

---

### Task 1: Scaffolding and date windows

**Files:**
- Create: `zoom_download.py`
- Create: `tests/test_zoom_download.py`
- Create: `requirements-dev.txt`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `date_windows(start: date, end: date, days: int = 30) -> list[tuple[date, date]]`, inclusive windows covering `start..end` with no gaps or overlap.

- [ ] **Step 1: Add dependencies and install**

`requirements.txt` becomes:

```
boto3>=1.37.0
requests>=2.31.0
```

`requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.0.0
```

Run: `python -m pip install -r requirements-dev.txt`
Expected: pytest and requests install (boto3 already present).

- [ ] **Step 2: Write the failing test**

`tests/test_zoom_download.py`:

```python
import os
import sys
from datetime import date

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zoom_download as zd


# ---- fakes shared by all tests -------------------------------------------
class FakeResp:
    def __init__(self, status=200, body=None, headers=None, chunks=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size=1):
        return iter(self._chunks)

    def close(self):
        pass


class FakeSession:
    """Serves queued responses in call order, for both post() and request()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.responses.pop(0)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


# ---- Task 1: date windows -------------------------------------------------
def test_date_windows_covers_range_without_gaps():
    windows = zd.date_windows(date(2026, 7, 1), date(2026, 9, 29))
    assert windows[0] == (date(2026, 7, 1), date(2026, 7, 30))
    assert windows[1] == (date(2026, 7, 31), date(2026, 8, 29))
    assert windows[-1] == (date(2026, 9, 29), date(2026, 9, 29))
    assert len(windows) == 4


def test_date_windows_single_day():
    assert zd.date_windows(date(2026, 7, 1), date(2026, 7, 1)) == [(date(2026, 7, 1), date(2026, 7, 1))]


def test_date_windows_empty_when_end_before_start():
    assert zd.date_windows(date(2026, 7, 2), date(2026, 7, 1)) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: FAIL (ModuleNotFoundError: zoom_download).

- [ ] **Step 4: Write minimal implementation**

`zoom_download.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 3 passed.

---

### Task 2: Zoom auth and request client

**Files:**
- Modify: `zoom_download.py`
- Modify: `tests/test_zoom_download.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (uses `TOKEN_URL`).
- Produces:
  - `ZoomAuth(account_id, client_id, client_secret, session=None, clock=time.time)` with `.token() -> str` (cached, refreshed 60 s before expiry) and `.invalidate() -> None`.
  - `ZoomClient(auth, session=None, sleep=time.sleep, max_retries=5)` with `.request(method, url, **kw) -> response`. It adds the Bearer header, refreshes once on 401, waits and retries on 429, and calls `raise_for_status()` before returning.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_zoom_download.py`:

```python
# ---- Task 2: auth and client ----------------------------------------------
def token_resp(token, expires_in=3600):
    return FakeResp(200, {"access_token": token, "expires_in": expires_in})


def test_token_is_cached_between_calls():
    sess = FakeSession([token_resp("t1")])
    auth = zd.ZoomAuth("acct", "id", "secret", session=sess, clock=lambda: 1000)
    assert auth.token() == "t1"
    assert auth.token() == "t1"
    assert len(sess.calls) == 1
    method, url, kw = sess.calls[0]
    assert url == zd.TOKEN_URL
    assert kw["params"] == {"grant_type": "account_credentials", "account_id": "acct"}
    assert kw["auth"] == ("id", "secret")


def test_token_refreshes_shortly_before_expiry():
    now = [1000]
    sess = FakeSession([token_resp("t1", 3600), token_resp("t2", 3600)])
    auth = zd.ZoomAuth("a", "i", "s", session=sess, clock=lambda: now[0])
    assert auth.token() == "t1"
    now[0] = 1000 + 3600 - 30  # inside the 60 s safety margin
    assert auth.token() == "t2"


def test_client_refreshes_token_once_on_401():
    sess = FakeSession([token_resp("t1"), FakeResp(401), token_resp("t2"), FakeResp(200, {"ok": 1})])
    auth = zd.ZoomAuth("a", "i", "s", session=sess, clock=lambda: 0)
    client = zd.ZoomClient(auth, session=sess, sleep=lambda s: None)
    r = client.request("GET", "https://api.zoom.us/v2/x")
    assert r.json() == {"ok": 1}
    bearers = [c[2]["headers"]["Authorization"] for c in sess.calls if c[0] == "GET"]
    assert bearers == ["Bearer t1", "Bearer t2"]


def test_client_gives_up_after_second_401():
    sess = FakeSession([token_resp("t1"), FakeResp(401), token_resp("t2"), FakeResp(401)])
    auth = zd.ZoomAuth("a", "i", "s", session=sess, clock=lambda: 0)
    client = zd.ZoomClient(auth, session=sess, sleep=lambda s: None)
    with pytest.raises(requests.HTTPError):
        client.request("GET", "https://api.zoom.us/v2/x")


def test_client_waits_retry_after_on_429():
    sleeps = []
    sess = FakeSession([token_resp("t1"), FakeResp(429, headers={"Retry-After": "7"}), FakeResp(200, {"ok": 1})])
    auth = zd.ZoomAuth("a", "i", "s", session=sess, clock=lambda: 0)
    client = zd.ZoomClient(auth, session=sess, sleep=sleeps.append)
    assert client.request("GET", "https://api.zoom.us/v2/x").json() == {"ok": 1}
    assert sleeps == [7.0]


def test_client_raises_after_max_429_retries():
    sess = FakeSession([token_resp("t1")] + [FakeResp(429)] * 3)
    auth = zd.ZoomAuth("a", "i", "s", session=sess, clock=lambda: 0)
    client = zd.ZoomClient(auth, session=sess, sleep=lambda s: None, max_retries=3)
    with pytest.raises(RuntimeError):
        client.request("GET", "https://api.zoom.us/v2/x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: the six new tests FAIL (AttributeError: module has no attribute ZoomAuth).

- [ ] **Step 3: Write minimal implementation**

Append to `zoom_download.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 9 passed.

---

### Task 3: Paginated recording listing

**Files:**
- Modify: `zoom_download.py`
- Modify: `tests/test_zoom_download.py`

**Interfaces:**
- Consumes: `date_windows`, `API`, and any object with `.request(method, url, params=...)` returning something with `.json()` (a `ZoomClient`).
- Produces: `iter_recordings(client, start: date, end: date, user: str = "me")`, a generator of dicts with keys `meeting_id`, `topic`, `start_time` (ISO string), `file_id`, `download_url`, `size` (bytes). Only completed MP4s.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_zoom_download.py`:

```python
# ---- Task 3: listing --------------------------------------------------------
class FakeClient:
    """Stands in for ZoomClient: serves queued responses and records params."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw.get("params")))
        return self.responses.pop(0)


def mp4(fid, status="completed", url=None):
    return {"id": fid, "file_type": "MP4", "status": status,
            "download_url": url or f"https://zoom.us/rec/download/{fid}", "file_size": 1000}


def m4a(fid):
    return {"id": fid, "file_type": "M4A", "status": "completed",
            "download_url": f"https://zoom.us/rec/download/{fid}", "file_size": 10}


def meeting(mid, topic, files, start="2026-07-05T15:00:00Z"):
    return {"id": mid, "topic": topic, "start_time": start, "recording_files": files}


def test_iter_recordings_pages_through_windows_and_filters():
    client = FakeClient([
        FakeResp(200, {"next_page_token": "abc", "meetings": [meeting(1, "Kickoff", [mp4("f1"), m4a("f2")])]}),
        FakeResp(200, {"next_page_token": "", "meetings": [meeting(2, "Weekly", [mp4("f3", status="processing"), mp4("f4")])]}),
        FakeResp(200, {"meetings": []}),  # second date window
    ])
    items = list(zd.iter_recordings(client, date(2026, 7, 1), date(2026, 7, 31)))
    assert [i["file_id"] for i in items] == ["f1", "f4"]
    assert items[0] == {"meeting_id": 1, "topic": "Kickoff", "start_time": "2026-07-05T15:00:00Z",
                        "file_id": "f1", "download_url": "https://zoom.us/rec/download/f1", "size": 1000}
    # three requests: window 1 page 1, window 1 page 2 (with token), window 2 page 1
    assert len(client.requests) == 3
    assert client.requests[0][1] == "https://api.zoom.us/v2/users/me/recordings"
    assert client.requests[0][2] == {"from": "2026-07-01", "to": "2026-07-30", "page_size": 300}
    assert client.requests[1][2] == {"from": "2026-07-01", "to": "2026-07-30", "page_size": 300, "next_page_token": "abc"}
    assert client.requests[2][2] == {"from": "2026-07-31", "to": "2026-07-31", "page_size": 300}


def test_iter_recordings_uses_given_user():
    client = FakeClient([FakeResp(200, {"meetings": []})])
    list(zd.iter_recordings(client, date(2026, 7, 1), date(2026, 7, 2), user="liz@example.com"))
    assert client.requests[0][1] == "https://api.zoom.us/v2/users/liz@example.com/recordings"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 2 new tests FAIL (no attribute iter_recordings).

- [ ] **Step 3: Write minimal implementation**

Append to `zoom_download.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 11 passed.

---

### Task 4: S3 key naming and existence check

**Files:**
- Modify: `zoom_download.py`
- Modify: `tests/test_zoom_download.py`

**Interfaces:**
- Consumes: item dicts from `iter_recordings`.
- Produces:
  - `slugify(text: str, max_len: int = 60) -> str`
  - `make_key(item: dict, part: int = 0) -> str`
  - `key_exists(s3, bucket: str, key: str) -> bool` (`s3` is a boto3 S3 client; a 404 means False, any other error is re-raised).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_zoom_download.py`:

```python
# ---- Task 4: naming and existence -----------------------------------------
from botocore.exceptions import ClientError


def test_slugify():
    assert zd.slugify("Weekly Sync: Q3 / Plans!") == "weekly_sync_q3_plans"
    assert zd.slugify("   ") == "untitled"
    assert zd.slugify("") == "untitled"
    assert len(zd.slugify("x" * 200)) == 60


def test_make_key_first_and_additional_parts():
    item = {"meeting_id": 123456789, "topic": "Kickoff Call", "start_time": "2026-07-05T15:00:00Z"}
    assert zd.make_key(item) == "2026-07-05_kickoff_call_123456789.mp4"
    assert zd.make_key(item, 1) == "2026-07-05_kickoff_call_123456789_1.mp4"


class FakeS3:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.uploads = []

    def head_object(self, Bucket, Key):
        if Key in self.existing:
            return {}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def upload_file(self, path, bucket, key):
        with open(path, "rb") as f:
            self.uploads.append((key, f.read()))
        self.existing.add(key)


def test_key_exists_true_false_and_reraises_other_errors():
    s3 = FakeS3(existing={"a.mp4"})
    assert zd.key_exists(s3, "b", "a.mp4") is True
    assert zd.key_exists(s3, "b", "missing.mp4") is False

    class Denied:
        def head_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    with pytest.raises(ClientError):
        zd.key_exists(Denied(), "b", "a.mp4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 3: Write minimal implementation**

Append to `zoom_download.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 14 passed.

---

### Task 5: Download, upload and cleanup loop

**Files:**
- Modify: `zoom_download.py`
- Modify: `tests/test_zoom_download.py`

**Interfaces:**
- Consumes: `make_key`, `key_exists`, and a client with `.request(method, url, stream=True)` returning an object with `iter_content(chunk_size)` and `close()`.
- Produces:
  - `download_to(client, url: str, path: str, chunk: int = 1 << 20) -> None`
  - `class AwsCredentialsError(Exception)`
  - `process_all(items, client, s3, bucket: str, tmpdir: str | None = None, log=print) -> dict` returning `{"uploaded": int, "skipped": int, "failures": list[tuple[str, str]]}`. It raises `AwsCredentialsError` if AWS credentials are missing or expired.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_zoom_download.py`:

```python
# ---- Task 5: process loop -----------------------------------------------------
import boto3.exceptions


def item(mid, topic="Call", start="2026-07-05T15:00:00Z", fid="f", size=3):
    return {"meeting_id": mid, "topic": topic, "start_time": start, "file_id": fid,
            "download_url": f"https://zoom.us/rec/download/{fid}", "size": size}


class DownloadClient:
    """Serves file bytes by URL; a URL mapped to an Exception raises it."""

    def __init__(self, files):
        self.files = files
        self.seen = []

    def request(self, method, url, **kw):
        self.seen.append((method, url, kw))
        data = self.files[url]
        if isinstance(data, Exception):
            raise data
        return FakeResp(200, chunks=[data[:2], data[2:]])


def test_process_all_downloads_uploads_and_cleans_up(tmp_path):
    s3 = FakeS3()
    client = DownloadClient({"https://zoom.us/rec/download/f1": b"abc"})
    result = zd.process_all([item(1, fid="f1")], client, s3, "bkt", tmpdir=str(tmp_path), log=lambda m: None)
    assert result == {"uploaded": 1, "skipped": 0, "failures": []}
    assert s3.uploads == [("2026-07-05_call_1.mp4", b"abc")]
    assert list(tmp_path.iterdir()) == []
    assert client.seen[0][2] == {"stream": True}


def test_process_all_skips_existing_without_downloading(tmp_path):
    s3 = FakeS3(existing={"2026-07-05_call_1.mp4"})
    client = DownloadClient({})
    result = zd.process_all([item(1, fid="f1")], client, s3, "bkt", tmpdir=str(tmp_path), log=lambda m: None)
    assert result == {"uploaded": 0, "skipped": 1, "failures": []}
    assert client.seen == []


def test_process_all_records_failure_and_continues(tmp_path):
    s3 = FakeS3()
    client = DownloadClient({
        "https://zoom.us/rec/download/f1": requests.HTTPError("500"),
        "https://zoom.us/rec/download/f2": b"xyz",
    })
    result = zd.process_all([item(1, fid="f1"), item(2, fid="f2")], client, s3, "bkt",
                            tmpdir=str(tmp_path), log=lambda m: None)
    assert result["uploaded"] == 1
    assert [k for k, _ in result["failures"]] == ["2026-07-05_call_1.mp4"]
    assert list(tmp_path.iterdir()) == []


def test_process_all_numbers_second_mp4_on_same_meeting(tmp_path):
    s3 = FakeS3()
    client = DownloadClient({"https://zoom.us/rec/download/a": b"aaa", "https://zoom.us/rec/download/b": b"bbb"})
    zd.process_all([item(1, fid="a"), item(1, fid="b")], client, s3, "bkt", tmpdir=str(tmp_path), log=lambda m: None)
    assert [k for k, _ in s3.uploads] == ["2026-07-05_call_1.mp4", "2026-07-05_call_1_1.mp4"]


def test_process_all_aborts_on_expired_aws_credentials(tmp_path):
    class ExpiredS3(FakeS3):
        def upload_file(self, path, bucket, key):
            raise boto3.exceptions.S3UploadFailedError("An error occurred (ExpiredToken) when calling PutObject")

    client = DownloadClient({"https://zoom.us/rec/download/f1": b"abc", "https://zoom.us/rec/download/f2": b"abc"})
    with pytest.raises(zd.AwsCredentialsError):
        zd.process_all([item(1, fid="f1"), item(2, fid="f2")], client, ExpiredS3(), "bkt",
                       tmpdir=str(tmp_path), log=lambda m: None)
    assert list(tmp_path.iterdir()) == []
    assert len(client.seen) == 1  # stopped after the first file
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 5 new tests FAIL.

- [ ] **Step 3: Write minimal implementation**

Append to `zoom_download.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 19 passed.

---

### Task 6: Command line, environment variables and local commit

**Files:**
- Modify: `zoom_download.py`
- Modify: `tests/test_zoom_download.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `parse_args(argv=None) -> argparse.Namespace` with `bucket`, `region`, `start`, `end`, `user`, `limit`, `list_only`; and `main(argv=None) -> int` returning the exit code (0 success, 1 if any file failed or AWS credentials failed).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_zoom_download.py`:

```python
# ---- Task 6: CLI ------------------------------------------------------------
def test_parse_args_defaults():
    a = zd.parse_args([])
    assert a.bucket == "sandgarden-zoom-uploads"
    assert a.region == "us-east-2"
    assert a.start == date(2026, 7, 1)
    assert a.end is None
    assert a.user == "me"
    assert a.limit is None
    assert a.list_only is False


def test_parse_args_overrides():
    a = zd.parse_args(["--from", "2026-08-01", "--to", "2026-08-31", "--limit", "2", "--list-only", "--bucket", "x"])
    assert (a.start, a.end, a.limit, a.list_only, a.bucket) == (date(2026, 8, 1), date(2026, 8, 31), 2, True, "x")


def test_main_exits_when_zoom_env_vars_missing(monkeypatch):
    for v in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(SystemExit) as e:
        zd.main([])
    assert "ZOOM_ACCOUNT_ID" in str(e.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 3: Write minimal implementation**

Append to `zoom_download.py`:

```python
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
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python -m pytest tests/test_zoom_download.py -v`
Expected: 22 passed.

- [ ] **Step 5: Commit locally**

```powershell
$src="C:\Users\lizsq\OneDrive\Desktop\Liznyc"
$clone="C:\Users\lizsq\AppData\Local\Temp\claude\C--Users-lizsq-OneDrive-Desktop-Liznyc\46457e9a-07cb-4c1b-b679-af16103d8272\scratchpad\video-processing"
New-Item -ItemType Directory -Force "$clone\tests" | Out-Null
Copy-Item "$src\zoom_download.py","$src\requirements.txt","$src\requirements-dev.txt" $clone
Copy-Item "$src\tests\test_zoom_download.py" "$clone\tests"
git -C $clone add zoom_download.py requirements.txt requirements-dev.txt tests/test_zoom_download.py
git -C $clone commit -m "Add Zoom recording downloader"
```

Expected: a new local commit. Pushing stays blocked until GitHub authentication exists.

---

### Task 7: First real run against Zoom (done by the user, with Claude reading output)

This task has no code. It proves auth and pagination against the real Zoom account. The user sets credentials; Claude never sees them.

- [ ] **Step 1: Create the Zoom app.** Zoom App Marketplace, then Develop, then Build App, then Server-to-Server OAuth. Add the cloud recording read scopes, fill in the required app info, and activate the app. Note the Account ID, Client ID and Client Secret.

- [ ] **Step 2: Set the variables in the user's own terminal** (values typed by the user, not pasted into chat):

```powershell
$env:ZOOM_ACCOUNT_ID = "<account id>"
$env:ZOOM_CLIENT_ID = "<client id>"
$env:ZOOM_CLIENT_SECRET = "<client secret>"
$env:AWS_PROFILE = "AWSPowerUserAccess-567097740953"
```

- [ ] **Step 3: Refresh AWS access.** Run `aws sso login --profile AWSPowerUserAccess-567097740953`.

- [ ] **Step 4: List without downloading.** Run `python zoom_download.py --list-only --limit 5`.
Expected: up to 5 lines of `<date>_<topic>_<id>.mp4  (<size> MB)` and a count. An auth error here means a wrong credential or missing scope. Fix and rerun.

- [ ] **Step 5: Download one file.** Run `python zoom_download.py --limit 1`.
Expected: `downloading: ...` then `uploaded: ...`, then `Done: 1 uploaded, 0 skipped, 0 failed`. Verify with `aws s3 ls s3://sandgarden-zoom-uploads/ --region us-east-2 --profile AWSPowerUserAccess-567097740953`.

- [ ] **Step 6: Prove resume.** Run `python zoom_download.py --limit 1` again.
Expected: `skip (already in S3): ...` and `Done: 0 uploaded, 1 skipped, 0 failed`.

- [ ] **Step 7: Full run.** Run `python zoom_download.py` for everything from 2026-07-01 to today. Then run `python 1_process_videos.py sandgarden-zoom-uploads` to process the new videos.

---

## Self-Review

**Spec coverage:** authentication via three env vars (Task 2, Task 6); date windows of at most 30 days and `next_page_token` pagination (Tasks 1 and 3); MP4-and-completed filter (Task 3); key naming with meeting id and multi-part suffix (Task 4); skip-if-exists (Tasks 4 and 5); one at a time with temp file always deleted (Task 5); failure list and non-zero exit (Tasks 5 and 6); 401 refresh and 429 retry (Task 2); expired AWS credentials abort (Tasks 5 and 6); defaults for range, bucket and region (Task 6); `requests` in requirements (Task 1). Real end-to-end run is Task 7, as the spec says it depends on credentials.

**Placeholder scan:** none. The `<account id>` values in Task 7 are values the user must supply, not plan gaps.

**Type consistency:** `iter_recordings` items carry `meeting_id, topic, start_time, file_id, download_url, size`, and `make_key`, `process_all`, the tests' `item()` helper and `--list-only` all use exactly those keys. `ZoomClient.request` is the single call shape used by `iter_recordings` (`params=`) and `download_to` (`stream=True`).
