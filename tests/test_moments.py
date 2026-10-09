import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moments as m


def seg(start, end, spk, text="words"):
    return {"start_timestamp_millis": start, "end_timestamp_millis": end, "type": "TRANSCRIPT",
            "speaker": {"speaker_label": spk}, "text": text}


VIDEO_RESULT = {
    "chapters": [
        {"audio_segments": [seg(0, 2000, "spk_0", "Hello there"), seg(2500, 4000, "spk_1", "Hi")]},
        {"audio_segments": [seg(9000, 11000, "spk_0", "After a long pause")]},
    ]
}

AUDIO_RESULT = {
    "audio_segments": [
        {"start_timestamp_millis": 100, "end_timestamp_millis": 900, "type": "TRANSCRIPT", "text": "Well, okay."},
        {"start_timestamp_millis": 1500, "end_timestamp_millis": 2500, "type": "TRANSCRIPT", "text": "Sure."},
    ]
}


def test_load_segments_from_video_result_flattens_chapters_in_time_order():
    segs = m.load_segments(VIDEO_RESULT)
    assert [s["text"] for s in segs] == ["Hello there", "Hi", "After a long pause"]
    assert segs[0] == {"start_ms": 0, "end_ms": 2000, "speaker": "spk_0", "text": "Hello there"}


def test_load_segments_from_audio_result_has_no_speaker():
    segs = m.load_segments(AUDIO_RESULT)
    assert [s["speaker"] for s in segs] == [None, None]
    assert segs[1]["start_ms"] == 1500


def test_load_segments_skips_non_transcript_and_empty_text():
    result = {"audio_segments": [
        {"start_timestamp_millis": 0, "end_timestamp_millis": 5, "type": "MUSIC", "text": "la la"},
        {"start_timestamp_millis": 10, "end_timestamp_millis": 20, "type": "TRANSCRIPT", "text": "   "},
        {"start_timestamp_millis": 30, "end_timestamp_millis": 40, "type": "TRANSCRIPT", "text": "keep me"},
    ]}
    assert [s["text"] for s in m.load_segments(result)] == ["keep me"]


def test_find_pauses_reports_gaps_at_or_over_the_threshold():
    pauses = m.find_pauses(m.load_segments(VIDEO_RESULT), min_gap_s=4.0)
    assert len(pauses) == 1
    p = pauses[0]
    assert p["start_ms"] == 4000 and p["end_ms"] == 9000 and p["gap_s"] == 5.0
    assert p["before_speaker"] == "spk_1" and p["after_speaker"] == "spk_0"
    assert p["before_text"] == "Hi" and p["after_text"] == "After a long pause"


def test_find_pauses_ignores_short_gaps():
    assert m.find_pauses(m.load_segments(AUDIO_RESULT), min_gap_s=4.0) == []


def test_find_pauses_sorted_longest_first():
    segs = [{"start_ms": 0, "end_ms": 1000, "speaker": "a", "text": "x"},
            {"start_ms": 6000, "end_ms": 7000, "speaker": "b", "text": "y"},    # 5 s gap
            {"start_ms": 20000, "end_ms": 21000, "speaker": "a", "text": "z"}]  # 13 s gap
    gaps = [p["gap_s"] for p in m.find_pauses(segs, min_gap_s=4.0)]
    assert gaps == [13.0, 5.0]


def test_find_overlaps_between_different_speakers_only():
    segs = [{"start_ms": 0, "end_ms": 5000, "speaker": "spk_0", "text": "I was saying"},
            {"start_ms": 3500, "end_ms": 6000, "speaker": "spk_1", "text": "But no"},        # 1.5 s over spk_0
            {"start_ms": 5800, "end_ms": 7000, "speaker": "spk_1", "text": "listen"}]        # same speaker: ignore
    ov = m.find_overlaps(segs, min_overlap_ms=500)
    assert len(ov) == 1
    assert ov[0]["overlap_ms"] == 1500
    assert ov[0]["first_speaker"] == "spk_0" and ov[0]["second_speaker"] == "spk_1"


def test_find_overlaps_ignores_tiny_overlaps_and_missing_speakers():
    segs = [{"start_ms": 0, "end_ms": 1000, "speaker": "a", "text": "x"},
            {"start_ms": 900, "end_ms": 2000, "speaker": "b", "text": "y"}]   # 100 ms
    assert m.find_overlaps(segs, min_overlap_ms=500) == []
    nospk = [{"start_ms": 0, "end_ms": 5000, "speaker": None, "text": "x"},
             {"start_ms": 1000, "end_ms": 6000, "speaker": None, "text": "y"}]
    assert m.find_overlaps(nospk, min_overlap_ms=500) == []


SEGS = [
    {"start_ms": 0, "end_ms": 2000, "speaker": "spk_0", "text": "So how did it go?"},
    {"start_ms": 9000, "end_ms": 11000, "speaker": "spk_1", "text": "Not great, honestly."},
    {"start_ms": 10200, "end_ms": 12000, "speaker": "spk_0", "text": "Wait, what?"},
]


def test_format_transcript_marks_silences_and_talking_over():
    pauses = m.find_pauses(SEGS, min_gap_s=4.0)
    overlaps = m.find_overlaps(SEGS, min_overlap_ms=500)
    lines = m.format_transcript(SEGS, pauses, overlaps).splitlines()
    assert lines[0] == "[00:00:00] spk_0: So how did it go?"
    assert lines[1] == "[— 7s of silence —]"
    assert lines[2] == "[00:00:09] spk_1: Not great, honestly."
    assert lines[3].startswith("[00:00:10] spk_0: Wait, what?")
    assert "talking over" in lines[3]


def test_format_transcript_without_markers_is_plain_lines():
    out = m.format_transcript(SEGS[:1], [], [])
    assert out == "[00:00:00] spk_0: So how did it go?"


def test_parse_model_json_handles_fences_and_surrounding_text():
    raw = 'Here you go:\n```json\n{"topics": [], "moments": [{"type": "humor"}]}\n```\nHope that helps.'
    assert m.parse_model_json(raw) == {"topics": [], "moments": [{"type": "humor"}]}
    assert m.parse_model_json('{"a": 1}') == {"a": 1}


def test_parse_model_json_raises_when_there_is_no_json():
    import pytest
    with pytest.raises(ValueError):
        m.parse_model_json("I could not produce any output.")


def test_pause_moment_scores_by_length_and_carries_context():
    p = {"start_ms": 4000, "end_ms": 16000, "gap_s": 12.0, "before_speaker": "spk_1", "after_speaker": "spk_0",
         "before_text": "I don't know what to say.", "after_text": "Okay."}
    mo = m.pause_moment(p, "call_a")
    assert mo["type"] == "silence" and mo["score"] == 5 and mo["call"] == "call_a"
    assert mo["timestamp"] == "00:00:04" and mo["source"] == "timing"
    assert "12" in mo["why"] and "I don't know what to say." in mo["quote"]
    assert m.pause_moment({**p, "gap_s": 4.5}, "c")["score"] == 2
    assert m.pause_moment({**p, "gap_s": 7.0}, "c")["score"] == 3
    assert m.pause_moment({**p, "gap_s": 9.0}, "c")["score"] == 4


def test_overlap_moment_scores_by_length():
    o = {"start_ms": 10200, "overlap_ms": 1800, "first_speaker": "spk_1", "second_speaker": "spk_0",
         "first_text": "Not great.", "second_text": "Wait, what?"}
    mo = m.overlap_moment(o, "call_a")
    assert mo["type"] == "talking-over" and mo["score"] == 3 and mo["timestamp"] == "00:00:10"
    assert m.overlap_moment({**o, "overlap_ms": 3500}, "c")["score"] == 4
    assert m.overlap_moment({**o, "overlap_ms": 600}, "c")["score"] == 1


def test_rank_moments_orders_by_score_then_time_and_keeps_the_top_n():
    ms_ = [{"call": "a", "timestamp": "00:05:00", "score": 3}, {"call": "a", "timestamp": "00:01:00", "score": 3},
           {"call": "b", "timestamp": "00:00:10", "score": 5}, {"call": "b", "timestamp": "00:09:00", "score": 1}]
    ranked = m.rank_moments(ms_, top_n=3)
    assert [(x["call"], x["timestamp"]) for x in ranked] == [("b", "00:00:10"), ("a", "00:01:00"), ("a", "00:05:00")]


def pause(start_s, gap_s, before="before words", after="after words"):
    return {"start_ms": int(start_s * 1000), "end_ms": int((start_s + gap_s) * 1000), "gap_s": float(gap_s),
            "before_speaker": "spk_0", "after_speaker": "spk_1", "before_text": before, "after_text": after}


def test_parse_clock_reads_hh_mm_ss_into_milliseconds():
    assert m.parse_clock("00:00:00") == 0
    assert m.parse_clock("01:02:05") == 3_725_000
    assert m.parse_clock("00:07:46") == 466_000
    assert m.parse_clock("garbage") is None
    assert m.parse_clock("") is None


def test_silence_moments_skips_the_start_of_the_call():
    out = m.silence_moments([pause(12, 13.2)], [], "c")
    assert out == []


def test_silence_moments_labels_very_long_gaps_as_away_and_scores_them_low():
    out = m.silence_moments([pause(600, 156)], [], "c")
    assert len(out) == 1 and out[0]["type"] == "away" and out[0]["score"] == 1
    assert "away" in out[0]["why"].lower()


def test_silence_moments_keeps_an_isolated_normal_pause_but_scores_it_low():
    out = m.silence_moments([pause(300, 8)], [], "c")
    assert out[0]["type"] == "silence" and out[0]["score"] == 1


def test_silence_moments_promotes_a_pause_next_to_a_flagged_moment():
    near = m.parse_clock("00:05:10")   # 10 s after the pause starts
    out = m.silence_moments([pause(300, 8)], [near], "c")
    assert out[0]["type"] == "silence" and out[0]["score"] == 3
    assert "next to" in out[0]["why"]
    longer = m.silence_moments([pause(300, 12)], [near], "c")
    assert longer[0]["score"] == 4


def test_silence_moments_does_not_promote_when_the_flagged_moment_is_far_away():
    far = m.parse_clock("00:20:00")
    assert m.silence_moments([pause(300, 8)], [far], "c")[0]["score"] == 1


def test_build_topic_map_inverts_the_models_clusters_ignoring_case_and_spacing():
    reply = {"topics": [{"canonical": "Miranda", "names": ["miranda homepage", " Miranda installs ", "MIRANDA"]},
                        {"canonical": "AWS credits", "names": ["aws credits expiring"]}]}
    mp = m.build_topic_map(reply)
    assert mp["miranda homepage"] == "Miranda" and mp["miranda installs"] == "Miranda" and mp["miranda"] == "Miranda"
    assert mp["aws credits expiring"] == "AWS credits"


def test_apply_topic_map_adds_canonical_names_and_falls_back_to_the_original():
    topics = [{"topic": "Miranda Homepage", "call": "a"}, {"topic": "Something unmapped", "call": "b"}]
    out = m.apply_topic_map(topics, {"miranda homepage": "Miranda"})
    assert out[0]["canonical"] == "Miranda"
    assert out[1]["canonical"] == "something unmapped"


def test_batch_names_splits_into_chunks_keeping_order_and_dropping_duplicates():
    names = ["a", "b", "a", "c", "d", "b", "e"]
    assert m.batch_names(names, size=2) == [["a", "b"], ["c", "d"], ["e"]]
    assert m.batch_names([], size=5) == []


def test_clock_formats_milliseconds_as_hh_mm_ss():
    assert m.clock(0) == "00:00:00"
    assert m.clock(3_725_000) == "01:02:05"
