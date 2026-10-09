import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moments_html as h


def test_call_label_turns_a_file_stem_into_a_readable_title():
    label = h.call_label("2026-07-22_152953_eng_standup_95311646981")
    assert label == {"date": "2026-07-22", "time": "15:29", "title": "Eng Standup", "pretty": "Jul 22 · 3:29 PM · Eng Standup"}


def test_call_label_handles_possessives_and_morning_times():
    label = h.call_label("2026-09-28_080517_joel_solymosi_s_zoom_meeting_95402650399")
    assert label["title"] == "Joel Solymosi's Zoom Meeting"
    assert label["pretty"].startswith("Sep 28 · 8:05 AM")


def test_call_label_handles_noon_and_midnight():
    assert h.call_label("2026-08-03_120000_x_123456789")["pretty"].startswith("Aug 3 · 12:00 PM")
    assert h.call_label("2026-08-03_001500_x_123456789")["pretty"].startswith("Aug 3 · 12:15 AM")


def test_call_label_falls_back_to_the_raw_stem_when_it_does_not_match():
    assert h.call_label("GMT20260924-180147_Recording")["pretty"] == "GMT20260924-180147_Recording"


def test_humanize_speakers_replaces_labels_with_readable_words():
    assert h.humanize_speakers("spk_1 pushes back against spk_0's idea") == "Speaker 1 pushes back against Speaker 0's idea"
    assert h.humanize_speakers("no labels here") == "no labels here"
    assert h.humanize_speakers("") == ""


def test_build_html_embeds_the_data_and_has_only_the_two_tabs():
    moments = [{"call": "2026-07-22_152953_eng_standup_95311646981", "timestamp": "00:05:56", "type": "breakthrough",
                "score": 4, "quote": "Prompt </script> injection", "why": "spk_5 finds a problem",
                "source": "model", "verbatim": "yes"}]
    topics = [{"canonical": "Miranda", "topic": "Miranda homepage", "call": "2026-07-22_152953_eng_standup_95311646981",
               "start": "00:01:00", "end": "00:02:00", "summary": "spk_0 shows the page"}]
    page = h.build_html(moments, topics)
    assert page.startswith("<!doctype html>")
    assert page.count('data-tab="') == 2 and 'data-tab="moments"' in page and 'data-tab="topics"' in page
    assert "Speaker 5 finds a problem" in page and "spk_5" not in page
    assert page.count("</script>") == 2          # the data block and the code block, nothing injected
    assert "Jul 22 · 3:29 PM · Eng Standup" in page


def test_rebuild_from_json_files_writes_the_page(tmp_path):
    import json
    moments = [{"call": "2026-07-22_152953_eng_standup_95311646981", "timestamp": "00:05:56", "type": "humor",
                "score": 3, "quote": "ha", "why": "funny", "source": "model", "verbatim": "yes"}]
    (tmp_path / "moments.json").write_text(json.dumps(moments), encoding="utf-8")
    (tmp_path / "topics.json").write_text("[]", encoding="utf-8")
    path = h.rebuild(str(tmp_path))
    assert os.path.basename(path) == "moments.html"
    assert "Eng Standup" in (tmp_path / "moments.html").read_text(encoding="utf-8")


def test_script_safe_json_never_contains_a_closing_script_tag():
    out = h.script_safe_json({"quote": "</script><b>hi</b>"})
    assert "</script" not in out
    import json
    assert json.loads(out.replace("<\\/", "</")) == {"quote": "</script><b>hi</b>"}
