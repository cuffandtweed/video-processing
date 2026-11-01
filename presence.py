"""
Is Liz (Elizabeth Zalman) in a call? Decided from the transcript alone: no model, no AWS.

  in         the video summary names her on screen (or the file title names her)
  mentioned  someone says "Liz" or "Elizabeth" aloud, but nothing shows her there: talked about, probably not present
  no sign    plenty of speech, and no trace of her at all
  unknown    too little speech to tell

"Out" (the page's "meetings Liz isn't in") is mentioned + no sign. Absence of evidence is not proof: someone with
camera and mic off would be missed. Checked against calls that are certainly hers, the "in" rule caught 60 of 61.
"""
import re

MIN_SPEECH_CHARS = 200


def title_part(stem):
    """The words of a recording's file name, without the date, start time and trailing meeting id."""
    parts = stem.split("_")
    if parts and re.fullmatch(r"\d{4}-\d\d-\d\d", parts[0]):
        parts = parts[1:]
    if parts and re.fullmatch(r"\d{6}", parts[0]):
        parts = parts[1:]
    if parts and re.fullmatch(r"\d{8,}", parts[-1]):
        parts = parts[:-1]
    return "_".join(parts)


def classify_presence(parsed, stem, screen_rx, spoken_rx, title_rx):
    if any(screen_rx.search(c["text"]) for c in parsed["chapters"]) or title_rx.search(title_part(stem)):
        return "in"
    if sum(len(s["text"]) for s in parsed["spoken"]) < MIN_SPEECH_CHARS:
        return "unknown"
    if any(spoken_rx.search(s["text"]) for s in parsed["spoken"]):
        return "mentioned"
    return "no sign"


def is_out(status):
    return status in ("mentioned", "no sign")


def summarize(statuses):
    counts = {"in": 0, "mentioned": 0, "no sign": 0, "unknown": 0}
    for s in statuses.values():
        counts[s] += 1
    counts["out"] = counts["mentioned"] + counts["no sign"]
    return counts
