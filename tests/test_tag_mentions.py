import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tag_mentions as tm


def test_save_people_creates_the_file(tmp_path):
    path = str(tmp_path / "people.json")
    tm.save_people(path, {"Ulderico": [{"time": "00:01:00"}]})
    assert json.load(open(path, encoding="utf-8")) == {"Ulderico": [{"time": "00:01:00"}]}


def test_save_people_keeps_other_people_and_replaces_the_one_rerun(tmp_path):
    path = str(tmp_path / "people.json")
    tm.save_people(path, {"Ulderico": [{"time": "old"}], "Ben Sweeney": [{"time": "keep me"}]})
    tm.save_people(path, {"Ulderico": [{"time": "new"}]})
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["Ulderico"] == [{"time": "new"}]
    assert saved["Ben Sweeney"] == [{"time": "keep me"}]


def test_save_people_survives_an_unreadable_existing_file(tmp_path):
    path = tmp_path / "people.json"
    path.write_text("not json", encoding="utf-8")
    tm.save_people(str(path), {"A": []})
    assert json.load(open(path, encoding="utf-8")) == {"A": []}
