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
