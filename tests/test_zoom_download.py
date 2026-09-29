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
