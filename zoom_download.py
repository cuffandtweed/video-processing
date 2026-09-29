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
