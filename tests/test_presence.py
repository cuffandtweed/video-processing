import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mentions as mn
import presence as pr

SCREEN = re.compile(r"\bElizabeth\s+Zalman\b|\bLiz\s+Zalman\b", re.I)
SPOKEN = re.compile(r"\bLiz\b|\bElizabeth\b", re.I)
TITLE = re.compile(r"(?:^|_)(?:liz|elizabeth|zalman)(?:_|$)", re.I)
TALK = "x" * 300      # enough speech to classify


def parsed(chapters="", speech=TALK, extra_spoken=()):
    lines = ["=== CHAPTERS ===", chapters, "", "=== TRANSCRIPT ==="] + [f"[00:00:{i:02d}] spk_0: {t}" for i, t in enumerate([speech, *extra_spoken])]
    return mn.parse_transcript("\n".join(lines))


def classify(p, stem="2026-07-22_152953_eng_standup_95311646981"):
    return pr.classify_presence(p, stem, SCREEN, SPOKEN, TITLE)


def test_named_on_screen_means_she_is_in():
    p = parsed("[00:00:00:00 - 00:00:10:00] Chapter 1: Elizabeth Zalman appears in a room with a brick wall.")
    assert classify(p) == "in"


def test_a_title_that_names_her_means_she_is_in_even_without_an_on_screen_trace():
    assert classify(parsed(), "2026-07-21_154000_britt_marty_liz_sebastian_decide_on_produc_91234567890") == "in"
    assert classify(parsed(), "2026-08-20_173029_elizabeth_zalman_s_zoom_meeting_97791237417") == "in"


def test_spoken_name_only_means_talked_about_not_present():
    p = parsed(extra_spoken=["Liz messaged me one minute into this call."])
    assert classify(p) == "mentioned"


def test_no_trace_at_all_means_no_sign():
    assert classify(parsed("[00:00:00:00 - 00:00:10:00] Chapter 1: Marty talks to Joel about docs.")) == "no sign"


def test_a_call_without_enough_speech_is_unknown():
    assert classify(parsed(speech="Uh, I'll, um,")) == "unknown"


def test_on_screen_beats_spoken_and_title_words_inside_other_words_do_not_count():
    p = parsed("[00:00:00:00 - 00:00:10:00] Chapter 1: Elizabeth Zalman smiles.", extra_spoken=["Liz is great"])
    assert classify(p) == "in"
    assert classify(parsed(), "2026-07-22_152953_lizard_wrangling_95311646981") == "no sign"   # "lizard" is not "liz"


def test_is_out_is_true_only_when_the_call_was_classified_and_she_is_not_in_it():
    assert pr.is_out("mentioned") and pr.is_out("no sign")
    assert not pr.is_out("in") and not pr.is_out("unknown")


def test_summarize_counts_each_status():
    assert pr.summarize({"a": "in", "b": "in", "c": "no sign", "d": "mentioned", "e": "unknown"}) == {
        "in": 2, "mentioned": 1, "no sign": 1, "unknown": 1, "out": 2}
