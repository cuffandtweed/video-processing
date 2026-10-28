import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mentions as mn

SAMPLE = """=== OVERALL SUMMARY (auto-generated) ===
Ben Sweeney appears from a music room.

=== CHAPTERS ===
[00:00:00:00 - 00:00:24:17] Chapter 1: A title card introduces Sebastian. Ben Sweeney appears in a music room with a keyboard. Elizabeth waves.
[00:03:01:21 - 00:03:49:01] Chapter 2: Gavyn updates the team on onboarding. Nobody else is named.
[00:07:01:11 - 00:07:31:01] Chapter 3: Sweeney is shown again, briefly.

=== TRANSCRIPT (timestamps are hh:mm:ss; speakers are auto-labeled) ===
[00:00:06] spk_0: Good morning. Good
[00:01:22] spk_3: I can go first. Ben Sweeney should have the keys.
[00:02:10] spk_1: Ben, can you hear me?
[00:02:46] spk_2: Sweeney said he'd look at it. Ben Shamash will too.
[01:05:09] spk_0: Ben Shamash joined late, and Sweeney's PR is merged.
"""


def test_person_pattern_matches_full_name_and_last_name_but_never_the_first_name_alone():
    pat = mn.person_pattern("Ben Sweeney")
    assert pat.search("I spoke to Ben Sweeney today")
    assert pat.search("Sweeney said so")
    assert pat.search("Sweeney's PR")
    assert pat.search("ben   sweeney")            # case and spacing
    assert not pat.search("Ben Shamash is here")
    assert not pat.search("Ben, can you hear me?")
    assert not pat.search("Sweeneyville")          # whole words only


def test_person_pattern_can_require_the_full_name():
    pat = mn.person_pattern("Ben Sweeney", last_name_alone=False)
    assert pat.search("Ben Sweeney is here")
    assert not pat.search("Sweeney said so")


def test_parse_transcript_reads_chapters_and_spoken_lines():
    p = mn.parse_transcript(SAMPLE)
    assert [c["start"] for c in p["chapters"]] == ["00:00:00", "00:03:01", "00:07:01"]
    assert p["chapters"][0]["end"] == "00:00:24"
    assert p["chapters"][0]["text"].startswith("A title card")
    assert p["spoken"][0] == {"clock": "00:00:06", "speaker": "spk_0", "text": "Good morning. Good"}
    assert p["spoken"][-1]["clock"] == "01:05:09"


def test_parse_transcript_handles_lines_without_a_speaker_label():
    p = mn.parse_transcript("=== TRANSCRIPT ===\n[00:00:00] Well, whatever. Marty, tell us.\n")
    assert p["spoken"] == [{"clock": "00:00:00", "speaker": None, "text": "Well, whatever. Marty, tell us."}]


def test_find_mentions_returns_spoken_hits_with_timestamps_and_the_matched_text():
    hits = mn.find_mentions(mn.parse_transcript(SAMPLE), mn.person_pattern("Ben Sweeney"))
    spoken = [h for h in hits if h["kind"] == "spoken"]
    assert [(h["time"], h["matched"]) for h in spoken] == [
        ("00:01:22", "Ben Sweeney"), ("00:02:46", "Sweeney"), ("01:05:09", "Sweeney")]
    assert spoken[0]["speaker"] == "spk_3" and "should have the keys" in spoken[0]["snippet"]


def test_find_mentions_returns_video_summary_hits_with_the_chapter_range_and_just_the_sentence():
    hits = mn.find_mentions(mn.parse_transcript(SAMPLE), mn.person_pattern("Ben Sweeney"))
    shown = [h for h in hits if h["kind"] == "video summary"]
    assert [(h["time"], h["end"]) for h in shown] == [("00:00:00", "00:00:24"), ("00:07:01", "00:07:31")]
    assert shown[0]["snippet"] == "Ben Sweeney appears in a music room with a keyboard."
    assert "title card" not in shown[0]["snippet"]


def test_find_mentions_ignores_the_overall_summary_and_other_bens():
    hits = mn.find_mentions(mn.parse_transcript(SAMPLE), mn.person_pattern("Ben Sweeney"))
    assert all(h["time"] != "" for h in hits)
    assert not any("Shamash" in h["matched"] for h in hits)
    assert len(hits) == 5      # 3 spoken + 2 video summary; the summary line and "Ben, can you hear me?" are not hits


def test_find_mentions_none_for_a_transcript_without_the_name():
    parsed = mn.parse_transcript("=== TRANSCRIPT ===\n[00:00:01] spk_0: hello\n")
    assert mn.find_mentions(parsed, mn.person_pattern("Ben Sweeney")) == []


def test_hits_are_sorted_by_time():
    hits = mn.find_mentions(mn.parse_transcript(SAMPLE), mn.person_pattern("Ben Sweeney"))
    assert [h["time"] for h in hits] == sorted(h["time"] for h in hits)
