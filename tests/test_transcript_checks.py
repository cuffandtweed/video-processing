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


def test_no_speech_report_says_so_and_does_not_invent_content():
    r = tc.no_speech_report("call_1")
    assert r.startswith("# call_1")
    assert "no spoken content" in r.lower()
