import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import frustrations as fr

STEM = "2026-08-20_173029_eng_standup_97791237417"
TRANSCRIPT = ("[00:01:05] spk_1: I just never know what Liz wants from us, she isn't giving any direction.\n"
              "[00:02:10] spk_2: Yeah, honestly this roadmap keeps changing and nobody tells us why.\n")


def reply(*items):
    return json.dumps({"frustrations": list(items)})


def test_a_quote_found_word_for_word_becomes_a_tagged_row_with_its_speaker():
    r = reply({"timestamp": "00:01:05", "quote": "she isn't giving any direction", "about": "Liz", "why": "no direction"})
    rows, dropped = fr.build_rows(STEM, r, TRANSCRIPT)
    assert dropped == 0 and len(rows) == 1
    row = rows[0]
    assert row["file"] == STEM + ".mp4" and row["time"] == "00:01:05" and row["kind"] == "spoken"
    assert row["speaker"] == "spk_1" and row["snippet"] == "she isn't giving any direction"
    assert row["note"] == "no direction" and row["date"] and row["call"]


def test_a_quote_not_in_the_transcript_is_dropped_and_counted():
    r = reply({"timestamp": "00:01:05", "quote": "Liz sucks", "why": "x"},
              {"timestamp": "00:02:10", "quote": "this roadmap keeps changing", "why": "churn"})
    rows, dropped = fr.build_rows(STEM, r, TRANSCRIPT)
    assert [x["snippet"] for x in rows] == ["this roadmap keeps changing"] and dropped == 1


def test_unreadable_or_empty_answers_give_no_rows():
    assert fr.build_rows(STEM, reply(), TRANSCRIPT) == ([], 0)
    try:
        fr.build_rows(STEM, "not json", TRANSCRIPT)
        assert False
    except ValueError:
        pass


def test_a_timestamp_with_no_line_leaves_the_speaker_blank():
    rows, _ = fr.build_rows(STEM, reply({"timestamp": "09:09:09", "quote": "this roadmap keeps changing", "why": ""}), TRANSCRIPT)
    assert rows[0]["speaker"] == ""


def test_calls_to_search_are_only_the_ones_liz_is_out_of_and_in_order():
    statuses = {"b": "no sign", "a": "mentioned", "c": "in", "d": "unknown"}
    assert fr.calls_to_search(statuses) == ["a", "b"]
