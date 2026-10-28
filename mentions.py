"""Exact-match tagging: where a person is named in a call, with timestamps. No model, so it is free, repeatable,
and every hit can be checked. Works on the readable transcript files (transcripts/<call>.txt) the pipeline writes.

Two kinds of hit:
  * "spoken"          - someone says the name; the time is that line's timestamp. These are the exact words.
  * "video summary"   - the name appears in BDA's AI-written description of a stretch of video (a "chapter"). That
                        happens when the person's caption is on screen, and also when people on the call talk about
                        them, so it means "this stretch involves them", not strictly "they are visible". The time is
                        the chapter's start and end.
"""
import re

_CHAPTER = re.compile(r"^\[(\d\d):(\d\d):(\d\d):\d\d - (\d\d):(\d\d):(\d\d):\d\d\]\s*Chapter \d+:\s*(.*)$")
_SPOKEN = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]\s*(?:(\S+?):\s+)?(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def person_pattern(full_name, last_name_alone=True):
    """A compiled pattern for a person. Matches "First Last" (any case or spacing) and, by default, the last name
    on its own. It NEVER matches the first name alone, because a first name is often shared by other people."""
    parts = full_name.split()
    first, last = parts[0], parts[-1]
    if last_name_alone:
        return re.compile(rf"\b(?:{re.escape(first)}\s+)?{re.escape(last)}\b", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(first)}\s+{re.escape(last)}\b", re.IGNORECASE)


def variants_pattern(variants):
    """A compiled pattern for a list of spellings, each matched as whole words in any case.

    Speech-to-text often garbles an unusual name ("Ulderico" comes out as "old Derico", "Olderrico"...), so a person
    can be given as a list of the spellings actually found. Longer spellings win, so "old Derico" is reported whole."""
    parts = sorted({" ".join(v.split()) for v in variants if v.strip()}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(r"\s+".join(re.escape(w) for w in v.split()) for v in parts) + r")\b",
                      re.IGNORECASE)


def load_aliases(path):
    """{lowercased name: [spellings]} from a text file of lines like "Name | spelling; spelling". "#" starts a comment."""
    aliases = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                name, spellings = line.split("|", 1)
                aliases[name.strip().lower()] = [s.strip() for s in spellings.split(";") if s.strip()]
    except FileNotFoundError:
        pass
    return aliases


def pattern_for(name, aliases=None, last_name_alone=True):
    """The pattern to use for a name: its listed spellings if it has them; otherwise the single word as is, or
    for a full name "First Last" (or just "Last") but never the first name alone."""
    listed = (aliases or {}).get(name.strip().lower())
    if listed:
        return variants_pattern([name] + listed)
    if len(name.split()) == 1:
        return variants_pattern([name])
    return person_pattern(name, last_name_alone)


def parse_transcript(text):
    """The chapters (with start and end) and the spoken lines of a transcript .txt file."""
    chapters, spoken, section = [], [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("=== OVERALL SUMMARY"):
            section = "summary"
        elif line.startswith("=== CHAPTERS"):
            section = "chapters"
        elif line.startswith("=== TRANSCRIPT"):
            section = "spoken"
        elif section == "chapters":
            m = _CHAPTER.match(line)
            if m:
                sh, sm, ss, eh, em, es, body = m.groups()
                chapters.append({"start": f"{sh}:{sm}:{ss}", "end": f"{eh}:{em}:{es}", "text": body})
        elif section == "spoken":
            m = _SPOKEN.match(line)
            if m:
                h, mnt, s, speaker, body = m.groups()
                spoken.append({"clock": f"{h}:{mnt}:{s}", "speaker": speaker, "text": body})
    return {"chapters": chapters, "spoken": spoken}


def _sentence_with(text, pattern):
    """Just the sentence that contains the match, so a long chapter description is not repeated for every hit."""
    for sentence in _SENTENCE_END.split(text):
        if pattern.search(sentence):
            return sentence.strip()
    return text.strip()


def find_mentions(parsed, pattern):
    """Every place the person is named, earliest first. The overall summary is not searched: it has no timestamps."""
    hits = []
    for line in parsed["spoken"]:
        found, seen = [], set()                     # one hit per spoken line, listing each different spelling in it
        for m in pattern.finditer(line["text"]):
            text = re.sub(r"\s+", " ", m.group(0))
            if text.lower() not in seen:
                seen.add(text.lower())
                found.append(text)
        if found:
            hits.append({"kind": "spoken", "time": line["clock"], "end": "", "speaker": line["speaker"],
                         "matched": " / ".join(found), "snippet": line["text"]})
    for chapter in parsed["chapters"]:
        m = pattern.search(chapter["text"])
        if m:
            hits.append({"kind": "video summary", "time": chapter["start"], "end": chapter["end"], "speaker": None,
                         "matched": re.sub(r"\s+", " ", m.group(0)), "snippet": _sentence_with(chapter["text"], pattern)})
    return sorted(hits, key=lambda h: (h["time"], h["kind"] != "spoken"))
