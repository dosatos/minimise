import pytest

from minimise.orchestration import loop_journal as lj


def test_append_read_roundtrip(tmp_path):
    p = tmp_path / "journal.jsonl"
    lj.append(p, {"control": "continue", "note": "a"})
    lj.append(p, {"control": "done"})
    assert lj.read(p) == [{"control": "continue", "note": "a"}, {"control": "done"}]


def test_read_missing_file_is_empty(tmp_path):
    assert lj.read(tmp_path / "nope.jsonl") == []


def test_read_skips_trailing_partial_line(tmp_path):
    p = tmp_path / "journal.jsonl"
    lj.append(p, {"control": "stop"})
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"control": "cont')  # crash mid-write
    assert lj.read(p) == [{"control": "stop"}]


def test_extract_last_json_tolerates_prose():
    text = "let me think\nsome reasoning\n{\"control\": \"stop\", \"why\": \"good enough\"}"
    assert lj.extract_last_json(text) == {"control": "stop", "why": "good enough"}


def test_extract_last_json_picks_the_last():
    text = '{"control": "continue"}\n{"control": "done"}'
    assert lj.extract_last_json(text) == {"control": "done"}


def test_extract_last_json_none_when_no_object():
    assert lj.extract_last_json("just prose, no json") is None
    assert lj.extract_last_json("") is None


def test_parse_control_accepts_valid():
    for c in ("continue", "stop", "done"):
        assert lj.parse_control({"control": c}) == c


def test_parse_control_failed_requires_handover():
    assert lj.parse_control({"control": "failed", "handover": "stuck"}) == "failed"
    with pytest.raises(ValueError):
        lj.parse_control({"control": "failed"})
    with pytest.raises(ValueError):
        lj.parse_control({"control": "failed", "handover": ""})


def test_parse_control_rejects_unknown_and_missing():
    with pytest.raises(ValueError):
        lj.parse_control({"control": "bogus"})
    with pytest.raises(ValueError):
        lj.parse_control({})


def test_commit_marker_and_resume_boundary(tmp_path):
    p = tmp_path / "journal.jsonl"
    assert lj.last_committed_iteration(p) == 0
    lj.write_commit_marker(p, 1)
    lj.append(p, {"control": "continue"})
    lj.write_commit_marker(p, 2)
    lj.append(p, {"control": "continue"})  # incomplete iteration 3
    assert lj.last_committed_iteration(p) == 2
