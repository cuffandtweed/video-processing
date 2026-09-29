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
