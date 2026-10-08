import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcript_checks as tc

SPOKEN = """=== OVERALL SUMMARY (auto-generated) ===
A meeting.

=== CHAPTERS ===
[00:00:00:00 - 00:00:07:23] Chapter 1: Intro.

=== TRANSCRIPT (timestamps are hh:mm:ss; speakers are auto-labeled) ===
[00:01:31] spk_0: Um, I was just offering this to everybody. We have $64,000 in AWS credits that are expiring in two days.
[00:01:40] spk_1: OK, sounds good.
"""

SILENT = """=== OVERALL SUMMARY (auto-generated) ===
A man sits silently with earbuds.

=== CHAPTERS ===
[00:00:00:00 - 00:00:07:23] Chapter 1: A title card.
[00:00:07:24 - 00:03:57:00] Chapter 2: The scene contains no dialogue or audio transcript.

=== TRANSCRIPT (timestamps are hh:mm:ss; speakers are auto-labeled) ===
"""


def test_has_speech_true_for_timed_spoken_lines():
    assert tc.has_speech(SPOKEN) is True


def test_has_speech_false_when_only_summary_and_chapters():
    assert tc.has_speech(SILENT) is False


def test_has_speech_false_for_empty_text_lines_and_no_transcript_marker():
    assert tc.has_speech("[00:00:05] spk_0: \n") is False
    assert tc.has_speech("=== TRANSCRIPT ===\n(no transcript found)") is False


def test_has_speech_true_for_audio_style_lines_without_speaker():
    assert tc.has_speech("[00:00:00] Well, whatever. Marty, why don't you tell us about this idea?") is True


def test_check_quotes_accepts_verbatim_quote_ignoring_case_and_punctuation():
    md = '- **[00:01:31]** "We have $64,000 in AWS credits that are expiring in two days."'
    total, bad = tc.check_quotes(md, SPOKEN)
    assert (total, bad) == (1, [])


def test_check_quotes_flags_invented_quote():
    md = '- **[00:01:15]** "Mindfulness isn\'t just a practice; it\'s a way of being."'
    total, bad = tc.check_quotes(md, SPOKEN)
    assert total == 1
    assert len(bad) == 1 and "Mindfulness" in bad[0]


def test_check_quotes_accepts_quote_spanning_consecutive_transcript_lines():
    transcript = ("[00:15:46] spk_1: Origin policy disallows reading the remote source.\n"
                  "[00:15:50] spk_1: That's just a post hog issue.\n")
    md = '"Origin policy disallows reading the remote source. That\'s just a post hog issue."'
    assert tc.check_quotes(md, transcript) == (1, [])


def test_check_quotes_handles_curly_quotes_and_special_hyphen():
    transcript = "[00:00:01] spk_0: This is a well‑known problem that keeps coming back."
    md = "“This is a well-known problem that keeps coming back.”"
    assert tc.check_quotes(md, transcript) == (1, [])


def test_check_quotes_checks_each_part_of_an_ellipsis_quote():
    transcript = "[00:00:01] spk_0: We have credits that are expiring in two days and nobody wants them at all."
    ok = '"We have credits that are expiring... nobody wants them at all."'
    bad = '"We have credits that are expiring... the moon is made of cheese."'
    assert tc.check_quotes(ok, transcript) == (1, [])
    total, missing = tc.check_quotes(bad, transcript)
    assert total == 1 and len(missing) == 1


def test_check_quotes_ignores_short_quoted_names():
    total, bad = tc.check_quotes('The card says "Joel Solymosi" in white.', SPOKEN)
    assert (total, bad) == (0, [])


def test_check_quotes_only_looks_at_the_quote_sections_when_present():
    md = ('## Themes\nHe said "an invented remark in the prose text" somewhere.\n\n'
          '## Great quotes\n- "We have $64,000 in AWS credits that are expiring in two days."\n\n'
          '## Notes for the editor\nSee "another invented remark in the notes" too.\n')
    assert tc.check_quotes(md, SPOKEN) == (1, [])


def test_check_quotes_checks_the_whole_text_when_there_is_no_quote_section():
    md = 'Someone said "an invented remark in the prose text" here.'
    total, bad = tc.check_quotes(md, SPOKEN)
    assert total == 1 and len(bad) == 1


def test_quote_note_reports_counts_and_lists_unverified():
    md = '"We have $64,000 in AWS credits that are expiring in two days." and "This sentence was never said aloud."'
    note = tc.quote_note(md, SPOKEN)
    assert "## Quote check" in note
    assert "1 of 2" in note
    assert "This sentence was never said aloud." in note


def test_quote_note_all_verified_message():
    md = '"We have $64,000 in AWS credits that are expiring in two days."'
    note = tc.quote_note(md, SPOKEN)
    assert "All 1 quote(s)" in note


def test_quote_note_empty_when_no_quotes():
    assert tc.quote_note("No quotes here.", SPOKEN) == ""


def test_batch_reports_keeps_order_and_respects_the_size_limit():
    reports = [(f"call_{i}", "x" * 100) for i in range(7)]
    batches = tc.batch_reports(reports, max_chars=450)
    assert [len(b) for b in batches] == [3, 3, 1]  # each block is ~118 chars, so 3 fit under 450
    flat = [block for b in batches for block in b]
    assert [blk.splitlines()[0] for blk in flat] == [f"===== REPORT: call_{i} =====" for i in range(7)]
    assert all(sum(len(blk) for blk in b) <= 450 for b in batches)


def test_batch_reports_single_batch_when_everything_fits():
    batches = tc.batch_reports([("a", "text"), ("b", "text")], max_chars=10_000)
    assert len(batches) == 1 and len(batches[0]) == 2


def test_batch_reports_oversize_report_gets_its_own_batch():
    batches = tc.batch_reports([("a", "x" * 50), ("big", "y" * 1000), ("c", "z" * 50)], max_chars=300)
    assert [len(b) for b in batches] == [1, 1, 1]


def test_batch_reports_empty_input():
    assert tc.batch_reports([], max_chars=100) == []


def test_cached_call_runs_once_then_reuses_the_saved_result(tmp_path):
    calls = []

    def fn():
        calls.append(1)
        return "partial index text"

    first = tc.cached_call(str(tmp_path), ("model-a", "prompt", "batch text"), fn)
    second = tc.cached_call(str(tmp_path), ("model-a", "prompt", "batch text"), fn)
    assert first == second == "partial index text"
    assert len(calls) == 1


def test_cached_call_recomputes_when_any_input_changes(tmp_path):
    calls = []

    def fn():
        calls.append(1)
        return f"result {len(calls)}"

    a = tc.cached_call(str(tmp_path), ("model-a", "prompt", "batch text"), fn)
    b = tc.cached_call(str(tmp_path), ("model-b", "prompt", "batch text"), fn)   # different model
    c = tc.cached_call(str(tmp_path), ("model-a", "prompt", "other batch"), fn)  # different batch
    assert [a, b, c] == ["result 1", "result 2", "result 3"]


def test_cached_call_does_not_save_when_the_call_fails(tmp_path):
    def boom():
        raise RuntimeError("throttled")

    try:
        tc.cached_call(str(tmp_path), ("m", "p", "t"), boom)
    except RuntimeError:
        pass
    assert tc.cached_call(str(tmp_path), ("m", "p", "t"), lambda: "ok") == "ok"


def test_collect_stream_text_joins_text_deltas_and_ignores_other_events():
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": "Hello"}, "contentBlockIndex": 0}},
        {"contentBlockDelta": {"delta": {"text": ", "}, "contentBlockIndex": 0}},
        {"contentBlockDelta": {"delta": {"text": "world"}, "contentBlockIndex": 0}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 3}}},
    ]
    assert tc.collect_stream_text(iter(events)) == "Hello, world"


def test_collect_stream_reports_the_stop_reason():
    done = [{"contentBlockDelta": {"delta": {"text": "a"}}}, {"messageStop": {"stopReason": "end_turn"}}]
    cut = [{"contentBlockDelta": {"delta": {"text": "b"}}}, {"messageStop": {"stopReason": "max_tokens"}}]
    assert tc.collect_stream(iter(done)) == ("a", "end_turn")
    assert tc.collect_stream(iter(cut)) == ("b", "max_tokens")


def test_collect_stream_without_a_stop_event_has_no_stop_reason():
    assert tc.collect_stream(iter([{"contentBlockDelta": {"delta": {"text": "x"}}}])) == ("x", None)


def test_cutoff_note_only_when_the_reply_hit_the_output_limit():
    assert tc.cutoff_note("end_turn") == ""
    assert tc.cutoff_note(None) == ""
    note = tc.cutoff_note("max_tokens")
    assert "cut off" in note.lower() and note.startswith("\n\n")


def test_collect_stream_text_empty_stream_gives_empty_text():
    assert tc.collect_stream_text(iter([])) == ""


def test_collect_stream_text_skips_deltas_without_text():
    events = [{"contentBlockDelta": {"delta": {"toolUse": {}}}}, {"contentBlockDelta": {"delta": {"text": "ok"}}}]
    assert tc.collect_stream_text(events) == "ok"


def test_tag_model_then_tagged_models_round_trips():
    a = tc.tag_model("partial index A", "model-a")
    b = tc.tag_model("partial index B", "model-b")
    assert "partial index A" in a and a != "partial index A"
    assert tc.tagged_models([a, b, "untagged text"]) == {"model-a", "model-b"}


def test_tagged_models_empty_when_nothing_is_tagged():
    assert tc.tagged_models(["plain", "text"]) == set()


def test_index_model_note_is_empty_when_only_the_primary_model_was_used():
    assert tc.index_model_note({"primary"}, "primary") == ""
    assert tc.index_model_note(set(), "primary") == ""


def test_index_model_note_names_the_other_models_that_wrote_parts_of_the_index():
    note = tc.index_model_note({"primary", "fallback-b", "fallback-a"}, "primary")
    assert "fallback-a" in note and "fallback-b" in note and "primary" in note
    assert note.endswith("\n\n")
    # the primary model is not listed as a "different" model
    assert note.index("fallback-a") < note.index("fallback-b")


def test_is_daily_cap_error_matches_the_bedrock_daily_token_message():
    msg = "ThrottlingException: Too many tokens per day, please wait before trying again."
    assert tc.is_daily_cap_error(msg) is True
    assert tc.is_daily_cap_error(RuntimeError(msg)) is True


def test_is_daily_cap_error_ignores_ordinary_throttling_and_other_errors():
    assert tc.is_daily_cap_error("ThrottlingException: Too many requests, please wait") is False
    assert tc.is_daily_cap_error("ReadTimeoutError: Read timeout on endpoint URL") is False
    assert tc.is_daily_cap_error("") is False


def test_model_note_only_when_a_different_model_wrote_it():
    assert tc.model_note("model-a", "model-a") == ""
    note = tc.model_note("model-b", "model-a")
    assert "model-b" in note and "model-a" in note
    assert note.endswith("\n\n")


def test_no_speech_report_says_so_and_does_not_invent_content():
    r = tc.no_speech_report("call_1")
    assert r.startswith("# call_1")
    assert "no spoken content" in r.lower()
