#!/usr/bin/env python3
"""
Find every place a person is named across the call transcripts, with timestamps. Exact text search: no model, no AWS.

    python tag_mentions.py "Ben Sweeney"
    python tag_mentions.py "Ben Sweeney" --full-name-only      # skip hits that say just "Sweeney"
    python tag_mentions.py "Marty McCall" --transcripts reports/transcripts --out mentions

Reads the transcript .txt files (reports/transcripts by default) and writes, under mentions/<name>/:
  mentions.md   readable, grouped by call, earliest first
  mentions.csv  one row per hit, for sorting and filtering
  mentions.json the same data, for other tools

The first name alone is never matched (it is often shared by other people).
"""
import argparse
import collections
import csv
import glob
import json
import os
import re

import mentions as mn
from moments_html import call_label, humanize_speakers


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def shorten(text, limit=240):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def bold_match(snippet, pattern):
    return pattern.sub(lambda m: f"**{m.group(0)}**", snippet)


def run(name, transcripts_dir, out_dir, last_name_alone=True, aliases=None):
    aliases = aliases or {}
    pattern = mn.pattern_for(name, aliases, last_name_alone)
    listed = aliases.get(name.strip().lower())
    by_call = {}
    for path in sorted(glob.glob(os.path.join(transcripts_dir, "2026-*.txt"))):
        stem = os.path.basename(path)[:-4]
        with open(path, encoding="utf-8") as f:
            hits = mn.find_mentions(mn.parse_transcript(f.read()), pattern)
        if hits:
            by_call[stem] = hits

    rows = [{"call": call_label(stem)["pretty"], "file": f"{stem}.mp4", "date": call_label(stem)["date"],
             "time": h["time"], "end": h["end"], "kind": h["kind"],
             "speaker": humanize_speakers(h["speaker"] or ""), "matched": h["matched"], "snippet": h["snippet"]}
            for stem, hits in by_call.items() for h in hits]

    folder = os.path.join(out_dir, slug(name))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "mentions.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "mentions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["call", "file", "date", "time", "end", "kind", "speaker", "matched", "snippet"])
        w.writeheader()
        w.writerows(rows)

    spoken = [r for r in rows if r["kind"] == "spoken"]
    shown = [r for r in rows if r["kind"] == "video summary"]
    calls_spoken = {r["file"] for r in spoken}
    calls_shown = {r["file"] for r in shown}
    spellings = collections.Counter(r["matched"].lower() for r in rows)
    if listed:
        rule = "these spellings (how speech-to-text wrote the name): " + ", ".join(f'"{s}"' for s in [name] + listed)
        note = "Edit `name_aliases.txt` to add or remove a spelling."
    elif len(name.split()) == 1:
        rule, note = f'the word "{name}"', ""
    else:
        rule = f'"{name}" or the last name alone ("{name.split()[-1]}")' if last_name_alone else f'"{name}"'
        note = "The first name alone is never matched."
    with open(os.path.join(folder, "mentions.md"), "w", encoding="utf-8") as f:
        f.write(f"# Mentions of {name}\n\n")
        f.write(f"Matched exactly: {rule}, in any capitalization. {note}\n\n")
        f.write("Spellings found: " + ", ".join(f'"{s}" ×{n}' for s, n in spellings.most_common()) + "\n\n")
        f.write(f"- **{len(by_call)} calls** mention {name} at least once.\n")
        f.write(f"- **Spoken:** {len(spoken)} times in {len(calls_spoken)} calls (someone says the name; the time is that line).\n")
        f.write(f"- **In the video summary:** {len(shown)} stretches in {len(calls_shown)} calls (the AI-written description of "
                f"that stretch of video names them: their caption is on screen, or people are talking about them; the "
                f"time range is that stretch).\n")
        f.write(f"- In the video summary **and** spoken in the same call: {len(calls_spoken & calls_shown)} calls.\n\n---\n")
        for stem in sorted(by_call):
            label = call_label(stem)
            f.write(f"\n### {label['pretty']}\n`{stem}.mp4`\n\n")
            for h in by_call[stem]:
                when = f"`{h['time']}`" if not h["end"] else f"`{h['time']}`–`{h['end']}`"
                who = f" ({humanize_speakers(h['speaker'])})" if h["speaker"] else ""
                f.write(f"- {when} **{h['kind']}**{who}: {bold_match(shorten(humanize_speakers(h['snippet'])), pattern)}\n")
    return {"calls": len(by_call), "spoken": len(spoken), "on_screen": len(shown), "folder": folder,
            "calls_spoken": len(calls_spoken), "calls_shown": len(calls_shown), "rows": rows}


def save_people(path, new_people):
    """Write {name: rows} to people.json, keeping anyone already saved who is not in new_people, so rerunning one
    person never erases another. The filmmaker's page reads this file for its People tab and tags."""
    people = {}
    try:
        with open(path, encoding="utf-8") as f:
            people = json.load(f)
    except (FileNotFoundError, ValueError):
        pass
    people.update(new_people)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(people, f, ensure_ascii=False)
    return people


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="+", help='one or more names to find, e.g. "Ben Sweeney" "Ulderico"')
    ap.add_argument("--transcripts", default=os.path.join("reports", "transcripts"))
    ap.add_argument("--out", default="mentions")
    ap.add_argument("--full-name-only", action="store_true", help="do not count hits that use only the last name")
    ap.add_argument("--aliases", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "name_aliases.txt"),
                    help="file listing how speech-to-text spelled a name (default: name_aliases.txt)")
    a = ap.parse_args()
    aliases = mn.load_aliases(a.aliases)
    found = {}
    for name in a.names:
        r = run(name, a.transcripts, a.out, last_name_alone=not a.full_name_only, aliases=aliases)
        found[name] = r["rows"]
        print(f'{r["calls"]} calls mention {name}: {r["spoken"]} spoken hits in {r["calls_spoken"]} calls, '
              f'{r["on_screen"]} video-summary stretches in {r["calls_shown"]} calls. Files are in {r["folder"]}')
    save_people(os.path.join(a.out, "people.json"), found)
    print(f'Saved {os.path.join(a.out, "people.json")} for the filmmaker page (moments_html.py picks it up).')
