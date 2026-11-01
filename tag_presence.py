#!/usr/bin/env python3
"""
Work out which calls Liz (Elizabeth Zalman) is in, from the transcripts alone (see presence.py). No model, no AWS.

    python tag_presence.py                 # writes mentions/liz_presence.json: {call stem: status}
"""
import argparse
import glob
import json
import os
import re
from collections import Counter

import mentions as mn
import presence as pr

SCREEN = re.compile(r"\bElizabeth\s+Zalman\b|\bLiz\s+Zalman\b", re.I)
SPOKEN = re.compile(r"\bLiz\b|\bElizabeth\b", re.I)
TITLE = re.compile(r"(?:^|_)(?:liz|elizabeth|zalman)(?:_|$)", re.I)


def statuses(transcripts_dir):
    out = {}
    for path in sorted(glob.glob(os.path.join(transcripts_dir, "2026-*.txt"))):
        stem = os.path.basename(path)[:-4]
        with open(path, encoding="utf-8") as f:
            out[stem] = pr.classify_presence(mn.parse_transcript(f.read()), stem, SCREEN, SPOKEN, TITLE)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcripts", default=os.path.join("reports", "transcripts"))
    ap.add_argument("--out", default="mentions")
    a = ap.parse_args()
    s = statuses(a.transcripts)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "liz_presence.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    print(dict(Counter(s.values())), pr.summarize(s))
