"""Guards for 2_analyze.py: detect transcripts with no speech, and verify that quotes in a report are verbatim."""
import re

# A spoken line looks like "[00:01:31] spk_0: some words" (video) or "[00:00:00] some words" (audio-only).
# Chapter lines look like "[00:00:00:00 - 00:00:07:23] Chapter 1: ..." and do not match.
_SPOKEN_LINE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] (.*)$", re.M)
_SPEAKER_LABEL = re.compile(r"^\S+:(?:\s+|$)")
_LINE_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*(?:\S+:(?:\s+|$))?", re.M)
_QUOTE_HEADING = re.compile(r"great quotes|strongest material", re.I)
_QUOTE = re.compile(r'["“]([^"”\n]{15,}?)["”]')
_ELLIPSIS = re.compile(r"\.\.\.|…")
_MIN_FRAGMENT = 15  # characters; shorter quoted bits (names, single words) are not checked


def has_speech(transcript):
    """True if any timed line has actual words after the optional speaker label."""
    for body in _SPOKEN_LINE.findall(transcript):
        if _SPEAKER_LABEL.sub("", body.strip(), count=1).strip():
            return True
    return False


def _norm(text):
    text = text.lower().replace("‑", "-").replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9$%']+", " ", text)
    return " ".join(text.split())


def _quote_sections(markdown):
    """Text of the sections that are supposed to hold verbatim quotes, or the whole text if none is found.

    Prose elsewhere quotes people loosely and has stray quotation marks that would be mis-paired."""
    kept, on = [], False
    for line in markdown.splitlines():
        if line.startswith("## "):
            on = bool(_QUOTE_HEADING.search(line))
            continue
        if on:
            kept.append(line)
    return "\n".join(kept) if kept else markdown


def check_quotes(markdown, transcript):
    """Return (number of quotes checked, quotes not found verbatim in the transcript).

    Only the quote sections are checked when the text has them. Case, punctuation and quote style are
    ignored. A quote containing an ellipsis is checked piece by piece, since the parts either side of
    it are separate stretches of speech."""
    markdown = _quote_sections(markdown)
    # Drop "[hh:mm:ss] spk_0:" prefixes so a quote that runs across consecutive lines still matches.
    haystack = _norm(_LINE_PREFIX.sub("", transcript))
    total = 0
    missing = []
    for line in markdown.splitlines():
        for quote in _QUOTE.findall(line):
            fragments = [_norm(f) for f in _ELLIPSIS.split(quote)]
            fragments = [f for f in fragments if len(f) >= _MIN_FRAGMENT]
            if not fragments:
                continue
            total += 1
            if any(f not in haystack for f in fragments):
                missing.append(quote)
    return total, missing


def quote_note(markdown, transcript):
    """Markdown to append to a report: a quote-check result, or "" when it has no checkable quotes."""
    total, missing = check_quotes(markdown, transcript)
    if total == 0:
        return ""
    if not missing:
        return f"\n\n## Quote check\nAll {total} quote(s) were found verbatim in the transcript."
    lines = [f"\n\n## Quote check\nWARNING: {len(missing)} of {total} quote(s) were NOT found verbatim in the "
             "transcript and may be paraphrased or invented. Do not use them without checking the audio:"]
    lines += [f'- "{q}"' for q in missing]
    return "\n".join(lines)


def no_speech_report(stem):
    return (f"# {stem}\n\n"
            "## No spoken content\n"
            "The transcript for this recording contains no spoken words, only an automatic description of what is on "
            "screen. No participants, timeline, themes or quotes were generated, because doing so would mean "
            "inventing them. Check the recording itself if you expected conversation.\n")


def collect_stream(events):
    """Read a Bedrock converse_stream event stream; return (the reply text, the stop reason or None).

    Streaming keeps data flowing during a long reply; one silent non-streaming request can be dropped by the
    network (idle connection) and then hang until the read timeout. The stop reason is "max_tokens" when the
    model ran out of output space and the reply is cut off."""
    parts, stop_reason = [], None
    for ev in events:
        if "contentBlockDelta" in ev:
            parts.append(ev["contentBlockDelta"]["delta"].get("text", ""))
        elif "messageStop" in ev:
            stop_reason = ev["messageStop"].get("stopReason")
    return "".join(parts), stop_reason


def collect_stream_text(events):
    """Just the text of a converse_stream event stream (see collect_stream)."""
    return collect_stream(events)[0]


def cutoff_note(stop_reason):
    """A visible warning to append to a reply that was cut off at the output limit; "" otherwise."""
    if stop_reason != "max_tokens":
        return ""
    return ("\n\n> **WARNING: this reply was cut off because the model hit its output limit, so sections at the end "
            "may be missing or incomplete.**")


def is_daily_cap_error(error):
    """True if a Bedrock error says the model's daily token quota is used up (waiting a few minutes won't help)."""
    return "tokens per day" in str(error).lower()


def model_note(used_model, primary_model):
    """A line for the top of a report when a fallback model wrote it; "" when the primary model did."""
    if used_model == primary_model:
        return ""
    return f"*Written by `{used_model}` (the primary model, `{primary_model}`, had reached its daily token limit).*\n\n"


def cached_call(cache_dir, key_parts, fn):
    """Return fn()'s text result, saving it under cache_dir keyed by a hash of key_parts.

    If the same key_parts were saved by an earlier run, return that instead of calling fn() again. Nothing is
    saved when fn() raises. Used so an interrupted master-index build repeats only the step that did not finish."""
    import hashlib
    import os
    digest = hashlib.sha256("\x00".join(key_parts).encode("utf-8")).hexdigest()[:20]
    path = os.path.join(cache_dir, digest + ".md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    result = fn()
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)
    return result


def batch_reports(reports, max_chars):
    """Group (stem, report) pairs, in order, into batches of report blocks no larger than max_chars.

    A report bigger than max_chars on its own still gets a batch of its own. Used to keep each master-index
    request small enough for the model's rate limit when there are many calls."""
    batches, current, size = [], [], 0
    for stem, report in reports:
        block = f"===== REPORT: {stem} =====\n{report}"
        if current and size + len(block) > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        batches.append(current)
    return batches
