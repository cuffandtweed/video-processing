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


STEM = "2026-07-22_152953_eng_standup_95311646981"


def test_build_links_for_google_drive_search():
    links = h.build_links([STEM], "drive")
    assert links[STEM] == f"https://drive.google.com/drive/search?q={STEM}.mp4"


def test_build_links_for_a_local_windows_folder():
    links = h.build_links([STEM], "local", "D:\\Footage\\Calls")
    assert links[STEM] == f"file:///D:/Footage/Calls/{STEM}.mp4"


def test_build_links_for_a_relative_folder_next_to_the_page():
    assert h.build_links([STEM], "local", "videos")[STEM] == f"videos/{STEM}.mp4"
    assert h.build_links([STEM], "local", "videos/")[STEM] == f"videos/{STEM}.mp4"


def test_build_links_encodes_spaces_and_special_characters_in_names():
    links = h.build_links(["a b&c"], "drive")
    assert "a%20b%26c.mp4" in links["a b&c"]


def test_build_links_none_mode_gives_no_links():
    assert h.build_links([STEM], "none") == {}


def test_build_html_with_links_embeds_them_and_a_button_label():
    moments = [{"call": STEM, "timestamp": "00:05:56", "type": "humor", "score": 3, "quote": "q", "why": "w",
                "source": "model", "verbatim": "yes"}]
    page = h.build_html(moments, [], links=h.build_links([STEM], "drive"), link_label="Open in Drive")
    assert "drive.google.com/drive/search?q=" in page and "Open in Drive" in page
    plain = h.build_html(moments, [])
    assert "drive.google.com" not in plain


def test_rebuild_with_a_link_mode_writes_the_named_page(tmp_path):
    import json
    moments = [{"call": STEM, "timestamp": "00:05:56", "type": "humor", "score": 3, "quote": "ha", "why": "funny",
                "source": "model", "verbatim": "yes"}]
    (tmp_path / "moments.json").write_text(json.dumps(moments), encoding="utf-8")
    (tmp_path / "topics.json").write_text("[]", encoding="utf-8")
    path = h.rebuild(str(tmp_path), mode="local", base="D:\\Footage", filename="moments_local.html")
    assert os.path.basename(path) == "moments_local.html"
    assert "file:///D:/Footage/" in (tmp_path / "moments_local.html").read_text(encoding="utf-8")


def test_the_command_line_builds_a_page_and_a_failure_leaves_no_empty_file(tmp_path):
    import json
    import subprocess
    moments = [{"call": STEM, "timestamp": "00:05:56", "type": "humor", "score": 4, "quote": "ha", "why": "funny",
                "source": "model", "verbatim": "yes"}]
    (tmp_path / "moments.json").write_text(json.dumps(moments), encoding="utf-8")
    (tmp_path / "topics.json").write_text("[]", encoding="utf-8")
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moments_html.py")
    done = subprocess.run([sys.executable, script, str(tmp_path), "--links", "drive", "--name", "page.html"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    page = (tmp_path / "page.html").read_text(encoding="utf-8")
    assert "drive.google.com/drive/search" in page and len(page) > 5000


def test_a_failed_build_does_not_overwrite_an_existing_page(tmp_path):
    (tmp_path / "keep.html").write_text("original page", encoding="utf-8")
    try:
        h.write_html(str(tmp_path), [], [], links={STEM: object()}, filename="keep.html")   # cannot be turned into JSON
    except TypeError:
        pass
    assert (tmp_path / "keep.html").read_text(encoding="utf-8") == "original page"


PEOPLE_ROWS = {"Ulderico": [
    {"call": "Jul 22 · 3:29 PM · Eng Standup", "file": STEM + ".mp4", "date": "2026-07-22", "time": "00:05:58",
     "end": "", "kind": "spoken", "speaker": "Speaker 2", "matched": "old Derico",
     "snippet": "Marty thought old Derico was great, da da da."},
    {"call": "Jul 22 · 3:29 PM · Eng Standup", "file": STEM + ".mp4", "date": "2026-07-22", "time": "00:03:00",
     "end": "00:04:10", "kind": "video summary", "speaker": "", "matched": "Olderrico",
     "snippet": "Garrett interviews Olderrico about the contract."},
]}


def test_normalize_people_keeps_only_what_the_page_needs_and_adds_the_call_stem():
    out = h.normalize_people(PEOPLE_ROWS)
    row = out["Ulderico"][0]
    assert row["stem"] == STEM
    assert set(row) == {"stem", "time", "end", "kind", "speaker", "matched", "snippet", "note"}


def test_build_html_without_people_has_no_people_tab():
    page = h.build_html([], [])
    assert 'data-tab="people"' not in page


def test_build_html_with_people_adds_a_third_tab_and_embeds_the_data():
    page = h.build_html([], [], people=PEOPLE_ROWS)
    assert page.count('data-tab="') == 3 and 'data-tab="people"' in page
    assert "Ulderico" in page and "old Derico" in page


def test_rebuild_reads_a_people_file_when_given(tmp_path):
    import json
    moments = [{"call": STEM, "timestamp": "00:05:56", "type": "humor", "score": 4, "quote": "ha", "why": "funny",
                "source": "model", "verbatim": "yes"}]
    (tmp_path / "moments.json").write_text(json.dumps(moments), encoding="utf-8")
    (tmp_path / "topics.json").write_text("[]", encoding="utf-8")
    (tmp_path / "people.json").write_text(json.dumps(PEOPLE_ROWS), encoding="utf-8")
    path = h.rebuild(str(tmp_path), people_path=str(tmp_path / "people.json"), filename="p.html")
    assert 'data-tab="people"' in (tmp_path / "p.html").read_text(encoding="utf-8")
    assert os.path.basename(path) == "p.html"


def _node():
    import shutil
    return shutil.which("node")


def _run_page(tmp_path, moments, topics, people):
    import json
    import subprocess
    page = tmp_path / "page.html"
    page.write_text(h.build_html(moments, topics, links=h.build_links([STEM], "drive"), link_label="Open in Google Drive",
                                 people=people), encoding="utf-8")
    harness = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_harness.js")
    done = subprocess.run([_node(), harness, str(page)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


import pytest

needs_node = pytest.mark.skipif(not _node(), reason="Node.js is not installed")


@needs_node
def test_page_renders_the_people_tab_with_counts_highlighting_and_links(tmp_path):
    out = _run_page(tmp_path, [], [], PEOPLE_ROWS)
    assert out["tabs"] == ["moments", "topics", "people"]
    people = out["people"]
    assert "Ulderico" in people and "1 said in 1 call" in people and "1 in the video summary" in people
    assert "<mark>old Derico</mark>" in people                      # the matched spelling is highlighted
    assert "Open in Google Drive" in people and "drive.google.com/drive/search" in people
    assert "said by Speaker 2" in people and "in the video summary" in people
    assert "00:03:00</code>–<code>00:04:10" in people              # the video-summary stretch shows its range


@needs_node
def test_page_tags_a_moment_when_a_person_is_named_within_a_minute(tmp_path):
    moments = [
        {"call": STEM, "timestamp": "00:06:20", "type": "humor", "score": 4, "quote": "near", "why": "w", "source": "model", "verbatim": "yes"},
        {"call": STEM, "timestamp": "00:40:00", "type": "humor", "score": 4, "quote": "far away", "why": "w", "source": "model", "verbatim": "yes"},
    ]
    out = _run_page(tmp_path, moments, [], PEOPLE_ROWS)
    cards = out["moments"].split("<article")
    near = [c for c in cards if "near" in c][0]
    far = [c for c in cards if "far away" in c][0]
    assert 'class="ptag"' in near and "Ulderico" in near          # spoken at 00:05:58, moment at 00:06:20
    assert 'class="ptag"' not in far
    assert 'id="person"' in out["momentsBar"] and "Any person or topic" in out["momentsBar"]


@needs_node
def test_page_without_people_has_no_person_filter_or_tags(tmp_path):
    moments = [{"call": STEM, "timestamp": "00:06:20", "type": "humor", "score": 4, "quote": "q", "why": "w", "source": "model", "verbatim": "yes"}]
    out = _run_page(tmp_path, moments, [], None)
    assert out["tabs"] == ["moments", "topics"]
    assert 'id="person"' not in out["momentsBar"] and "ptag" not in out["moments"]


TERM_ROWS = {"B2C": [
    {"call": "Sep 22 · 12:00 PM · X", "file": STEM + ".mp4", "date": "2026-09-22", "time": "00:35:38", "end": "",
     "kind": "spoken", "speaker": "Speaker 0", "matched": "B2C",
     "snippet": "It's basically B2C because the people are small businesses."},
]}


def _run_page_with_terms(tmp_path, moments, people=None, terms=None):
    import json
    import subprocess
    page = tmp_path / "page.html"
    page.write_text(h.build_html(moments, [], links=h.build_links([STEM], "drive"), link_label="Open in Google Drive",
                                 people=people, terms=terms), encoding="utf-8")
    harness = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_harness.js")
    done = subprocess.run([_node(), harness, str(page)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_build_html_with_only_terms_has_no_people_tab_but_embeds_the_terms():
    page = h.build_html([], [], terms=TERM_ROWS)
    assert 'data-tab="people"' not in page and page.count('data-tab="') == 2
    assert "B2C" in page


@needs_node
def test_topics_tab_shows_tagged_topics_with_counts_highlighting_and_links(tmp_path):
    out = _run_page_with_terms(tmp_path, [], terms=TERM_ROWS)
    topics = out["topics"]
    assert "Tagged topics" in topics and "B2C" in topics
    assert "1 said in 1 call" in topics
    assert "<mark>B2C</mark>" in topics
    assert "Open in Google Drive" in topics


@needs_node
def test_a_moment_near_a_tagged_topic_gets_a_tag_and_the_filter_lists_topics_separately(tmp_path):
    moments = [
        {"call": STEM, "timestamp": "00:35:50", "type": "breakthrough", "score": 4, "quote": "near it", "why": "w", "source": "model", "verbatim": "yes"},
        {"call": STEM, "timestamp": "00:10:00", "type": "breakthrough", "score": 4, "quote": "far away", "why": "w", "source": "model", "verbatim": "yes"},
    ]
    out = _run_page_with_terms(tmp_path, moments, people=PEOPLE_ROWS, terms=TERM_ROWS)
    cards = out["moments"].split("<article")
    assert 'class="ptag"' in [c for c in cards if "near it" in c][0] and "B2C" in [c for c in cards if "near it" in c][0]
    assert 'class="ptag"' not in [c for c in cards if "far away" in c][0]
    bar = out["momentsBar"]
    assert 'id="person"' in bar and '<optgroup label="People">' in bar and '<optgroup label="Topics">' in bar


OTHER = "2026-09-23_090000_other_call_12345678901"
PRESENCE = {STEM: "in", OTHER: "no sign"}
FRUSTRATION_ROWS = {"Frustration with Liz": [
    {"call": "x", "file": OTHER + ".mp4", "date": "2026-09-23", "time": "00:01:05", "end": "", "kind": "spoken",
     "speaker": "spk_1", "matched": "", "snippet": "she isn't giving any direction", "note": "wants direction"}]}


def _page_eval(tmp_path, expr, moments, topics=(), presence=None, terms=None):
    import json
    import subprocess
    page = tmp_path / "page.html"
    page.write_text(h.build_html(moments, list(topics), links=h.build_links([STEM, OTHER], "drive"), presence=presence,
                                 terms=terms), encoding="utf-8")
    harness = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_harness.js")
    done = subprocess.run([_node(), harness, str(page), expr], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _m(call, quote):
    return {"call": call, "timestamp": "00:01:10", "type": "tension", "score": 4, "quote": quote, "why": "w", "source": "model", "verbatim": "yes"}


@needs_node
def test_page_offers_a_meetings_liz_isnt_in_filter_that_keeps_only_those_calls(tmp_path):
    moments = [_m(STEM, "with her"), _m(OTHER, "without her")]
    out = _page_eval(tmp_path, "state.outOnly = true; renderMoments(); document.getElementById('app').innerHTML",
                     moments, presence=PRESENCE)
    assert 'id="outonly"' in out["momentsBar"] and "Liz isn" in out["momentsBar"]
    assert "without her" in out["eval"] and "with her" not in out["eval"].replace("without her", "")
    assert "without her" in out["moments"] and "with her" in out["moments"].replace("without her", "")   # off by default


@needs_node
def test_moment_cards_in_calls_liz_is_out_of_carry_a_tag_and_others_do_not(tmp_path):
    out = _page_eval(tmp_path, "0", [_m(STEM, "with her"), _m(OTHER, "without her")], presence=PRESENCE)
    cards = out["moments"].split("<article")
    assert "Liz not in this call" in [c for c in cards if "without her" in c][0]
    assert "Liz not in this call" not in [c for c in cards if ">with her<" in c][0]


@needs_node
def test_without_presence_data_there_is_no_filter_and_no_tag(tmp_path):
    out = _page_eval(tmp_path, "0", [_m(OTHER, "q")])
    assert 'id="outonly"' not in out["momentsBar"] and "Liz not in" not in out["moments"]


@needs_node
def test_the_filter_also_applies_to_topics_and_to_tagged_topics(tmp_path):
    topics = [{"call": STEM, "canonical": "Docs", "topic": "docs", "summary": "with", "start": "00:00:01", "end": "00:00:09"},
              {"call": OTHER, "canonical": "Docs", "topic": "docs", "summary": "without", "start": "00:00:01", "end": "00:00:09"}]
    out = _page_eval(tmp_path, "state.outOnly = true; renderTopics(); document.getElementById('app').innerHTML",
                     [], topics, presence=PRESENCE, terms=FRUSTRATION_ROWS)
    assert "<b>docs</b> — without" in out["eval"] and "<b>docs</b> — with<" not in out["eval"]
    assert "Frustration with Liz" in out["eval"] and "she isn&#39;t giving any direction" in out["eval"]


@needs_node
def test_a_tagged_quote_shows_its_note_and_never_a_blank_highlight(tmp_path):
    out = _page_eval(tmp_path, "0", [], presence=PRESENCE, terms=FRUSTRATION_ROWS)
    assert "wants direction" in out["topics"] and "<mark></mark>" not in out["topics"]


def test_rebuild_adds_presence_and_frustrations_from_their_saved_files(tmp_path):
    import json
    (tmp_path / "moments.json").write_text("[]", encoding="utf-8")
    (tmp_path / "topics.json").write_text("[]", encoding="utf-8")
    (tmp_path / "pres.json").write_text(json.dumps(PRESENCE), encoding="utf-8")
    (tmp_path / "fr.json").write_text(json.dumps(FRUSTRATION_ROWS), encoding="utf-8")
    path = h.rebuild(str(tmp_path), presence_path=str(tmp_path / "pres.json"), frustrations_path=str(tmp_path / "fr.json"))
    page = open(path, encoding="utf-8").read()
    assert "no sign" in page and "she isn" in page and "Frustration with Liz" in page


def test_script_safe_json_never_contains_a_closing_script_tag():
    out = h.script_safe_json({"quote": "</script><b>hi</b>"})
    assert "</script" not in out
    import json
    assert json.loads(out.replace("<\\/", "</")) == {"quote": "</script><b>hi</b>"}
