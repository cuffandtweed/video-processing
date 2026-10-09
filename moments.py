"""Objective conversation signals from a Bedrock Data Automation result: long pauses and talking-over.

These come straight from the segment timestamps, so they need no model. They feed the "moments" file alongside
the model's reading of the words (emotion, disagreement, laughter, breakthroughs, humor)."""


def clock(ms):
    s = int(ms) // 1000
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def load_segments(result):
    """Spoken segments as dicts {start_ms, end_ms, speaker, text}, in time order.

    A video result keeps them inside chapters; an audio-only result keeps them at the top level (no speakers)."""
    raw = []
    if result.get("chapters"):
        for chapter in result["chapters"]:
            raw += chapter.get("audio_segments", [])
    else:
        raw += result.get("audio_segments", [])
    segs = []
    for s in raw:
        if s.get("type") not in (None, "TRANSCRIPT"):
            continue
        text = (s.get("text") or "").strip()
        if not text:
            continue
        segs.append({
            "start_ms": int(s.get("start_timestamp_millis", 0)),
            "end_ms": int(s.get("end_timestamp_millis", s.get("start_timestamp_millis", 0))),
            "speaker": (s.get("speaker") or {}).get("speaker_label"),
            "text": text,
        })
    return sorted(segs, key=lambda s: s["start_ms"])


def find_pauses(segments, min_gap_s=4.0):
    """Silences of at least min_gap_s between one segment's end and the next one's start, longest first."""
    pauses = []
    for prev, nxt in zip(segments, segments[1:]):
        gap_s = (nxt["start_ms"] - prev["end_ms"]) / 1000
        if gap_s >= min_gap_s:
            pauses.append({
                "start_ms": prev["end_ms"], "end_ms": nxt["start_ms"], "gap_s": round(gap_s, 1),
                "before_speaker": prev["speaker"], "after_speaker": nxt["speaker"],
                "before_text": prev["text"], "after_text": nxt["text"],
            })
    return sorted(pauses, key=lambda p: p["gap_s"], reverse=True)


def format_transcript(segments, pauses, overlaps):
    """The transcript as "[hh:mm:ss] spk_N: text" lines, with silences and talking-over marked inline,
    so a model reading the words also sees the timing signals."""
    silence_before = {p["end_ms"]: p for p in pauses}   # a pause ends where the next segment starts
    talked_over = {o["start_ms"] for o in overlaps}
    lines = []
    for s in segments:
        p = silence_before.get(s["start_ms"])
        if p:
            lines.append(f"[— {round(p['gap_s'])}s of silence —]")
        label = f"{s['speaker']}: " if s["speaker"] else ""
        mark = " (talking over the previous speaker)" if s["start_ms"] in talked_over else ""
        lines.append(f"[{clock(s['start_ms'])}] {label}{s['text']}{mark}")
    return "\n".join(lines)


def parse_model_json(text):
    """The first JSON object in a model reply, tolerating ```json fences and chatter around it."""
    import json
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in the reply")
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"could not parse the JSON in the reply: {e}") from e
    return obj


def pause_moment(pause, call):
    """A silence as a moment: longer silences score higher (2 to 5)."""
    gap = pause["gap_s"]
    score = 5 if gap >= 12 else 4 if gap >= 8 else 3 if gap >= 6 else 2
    quote = f'{pause["before_text"]}  [silence]  {pause["after_text"]}'
    return {"call": call, "timestamp": clock(pause["start_ms"]), "type": "silence", "score": score,
            "quote": quote, "why": f"{gap:g} seconds of silence between speakers", "source": "timing"}


def overlap_moment(overlap, call):
    """Talking over someone as a moment: longer overlaps score higher (1 to 4)."""
    ms = overlap["overlap_ms"]
    score = 4 if ms >= 3000 else 3 if ms >= 1500 else 2 if ms >= 800 else 1
    quote = f'{overlap["first_text"]}  [overlapping]  {overlap["second_text"]}'
    return {"call": call, "timestamp": clock(overlap["start_ms"]), "type": "talking-over", "score": score,
            "quote": quote, "why": f"{ms / 1000:.1f} seconds of overlapping speech", "source": "timing"}


def parse_clock(text):
    """"hh:mm:ss" into milliseconds; None if it isn't one."""
    parts = str(text).strip().split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    h, mnt, s = (int(p) for p in parts)
    return ((h * 60 + mnt) * 60 + s) * 1000


def silence_moments(pauses, flagged_ms, call, min_start_ms=45_000, away_s=30.0, near_ms=20_000):
    """Silences worth keeping, with honest scores.

    A bare silence says little: in a working meeting it is usually someone on mute, sharing a screen or away.
    So: gaps in the first 45 seconds (waiting for people to join) are dropped; gaps of 30+ seconds are labeled
    "away" and score 1; other gaps score 1 on their own and are promoted (3, or 4 for 10+ seconds) only when a
    flagged moment (the model's) falls within 20 seconds of the pause."""
    out = []
    for p in pauses:
        if p["start_ms"] < min_start_ms:
            continue
        moment = pause_moment(p, call)
        gap = p["gap_s"]
        if gap >= away_s:
            moment.update(type="away", score=1,
                          why=f"{gap:g} seconds with no one speaking (away, sharing a screen or working?)")
        else:
            near = any(abs(t - p["start_ms"]) <= near_ms or abs(t - p["end_ms"]) <= near_ms
                       for t in flagged_ms if t is not None)
            if near:
                moment.update(score=4 if gap >= 10 else 3,
                              why=f"{gap:g} seconds of silence next to a flagged moment")
            else:
                moment.update(score=1, why=f"{gap:g} seconds of silence")
        out.append(moment)
    return out


def build_topic_map(reply):
    """{lowercased original topic name: canonical name} from the model's clustering reply."""
    mapping = {}
    for cluster in reply.get("topics", []):
        canonical = str(cluster.get("canonical", "")).strip()
        for name in cluster.get("names", []):
            key = str(name).strip().lower()
            if canonical and key:
                mapping[key] = canonical
    return mapping


def apply_topic_map(topics, mapping):
    """Add a "canonical" name to each topic; a topic the model did not place keeps its own name."""
    out = []
    for t in topics:
        key = str(t.get("topic", "")).strip().lower()
        out.append({**t, "canonical": mapping.get(key, key)})
    return out


def batch_names(names, size):
    """Distinct names in first-seen order, in chunks of at most `size`."""
    seen, unique = set(), []
    for n in names:
        key = str(n).strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return [unique[i:i + size] for i in range(0, len(unique), size)]


def rank_moments(moments, top_n=None):
    """Highest score first; ties by call then time so the order is stable."""
    ranked = sorted(moments, key=lambda x: (-x["score"], x["call"], x["timestamp"]))
    return ranked[:top_n] if top_n else ranked


def find_overlaps(segments, min_overlap_ms=500):
    """Places where a different speaker starts talking before the previous speaker has finished.

    Needs speaker labels, so it finds nothing in audio-only results."""
    overlaps = []
    for prev, nxt in zip(segments, segments[1:]):
        if not prev["speaker"] or not nxt["speaker"] or prev["speaker"] == nxt["speaker"]:
            continue
        overlap = prev["end_ms"] - nxt["start_ms"]
        if overlap >= min_overlap_ms:
            overlaps.append({
                "start_ms": nxt["start_ms"], "overlap_ms": overlap,
                "first_speaker": prev["speaker"], "second_speaker": nxt["speaker"],
                "first_text": prev["text"], "second_text": nxt["text"],
            })
    return sorted(overlaps, key=lambda o: o["overlap_ms"], reverse=True)
