import pytest

from minimise.orchestration import loop_journal as lj


def test_append_read_roundtrip(tmp_path):
    p = tmp_path / "journal.jsonl"
    lj.append(p, {"control": "continue", "note": "a"})
    lj.append(p, {"control": "done"})
    assert lj.read(p) == [{"control": "continue", "note": "a"}, {"control": "done"}]


def test_append_writes_non_ascii_raw_and_read_loads_old_escaped_lines(tmp_path):
    p = tmp_path / "journal.jsonl"
    lj.append(p, {"note": "Глава"})
    assert "Глава" in p.read_text(encoding="utf-8")
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"note": "\\u0413\\u043b\\u0430\\u0432\\u0430"}\n')  # written before the change
    assert lj.read(p) == [{"note": "Глава"}, {"note": "Глава"}]


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


def test_extract_last_json_accepts_a_pretty_printed_control_object_at_the_end():
    text = 'reasoning first\n{\n  "control": "done",\n  "summary": "all good"\n}\n\n'
    assert lj.extract_last_json(text) == {"control": "done", "summary": "all good"}


def test_extract_last_json_multi_line_object_with_braces_inside_strings():
    text = 'thinking\n{\n  "control": "continue",\n  "summary": "fix {x} in f() { return 1; }"\n}'
    assert lj.extract_last_json(text) == {
        "control": "continue", "summary": "fix {x} in f() { return 1; }",
    }


def test_extract_last_json_multi_line_object_followed_by_prose_is_not_taken():
    text = 'reasoning\n{\n  "control": "done"\n}\nThat is my verdict.'
    assert lj.extract_last_json(text) is None


def test_extract_last_json_single_line_object_followed_by_prose_still_found():
    # today's single-line scan is the fallback, unchanged
    text = 'reasoning\n{"control": "done"}\nThat is my verdict.'
    assert lj.extract_last_json(text) == {"control": "done"}


def test_extract_last_json_final_single_line_control_beats_an_earlier_snippet():
    text = (
        'I first tried this config:\n{\n  "control": "stop",\n  "x": 1\n}\n'
        'and also {"inline": true} in prose.\n'
        '{"control": "done", "summary": "final"}'
    )
    assert lj.extract_last_json(text) == {"control": "done", "summary": "final"}


def test_extract_last_json_pretty_printed_findings_array_returns_the_whole_object():
    text = (
        "Reviewed everything.\n"
        "{\n"
        '  "control": "done",\n'
        '  "verdict": "fail",\n'
        '  "findings": [\n'
        '    {"file": "a.py", "issue": "bug"},\n'
        '    {"file": "b.py", "issue": "typo"}\n'
        "  ]\n"
        "}\n"
    )
    assert lj.extract_last_json(text) == {
        "control": "done", "verdict": "fail",
        "findings": [{"file": "a.py", "issue": "bug"}, {"file": "b.py", "issue": "typo"}],
    }


def test_strip_control_line_removes_a_multi_line_control_object():
    text = 'reasoning here\nmore reasoning\n{\n  "control": "done",\n  "findings": [\n    {"a": 1}\n  ]\n}\n'
    assert lj.strip_control_line(text) == "reasoning here\nmore reasoning"


def test_strip_control_line_single_line_and_plain_prose_unchanged():
    assert lj.strip_control_line('reasoning\n{"control": "done"}') == "reasoning"
    assert lj.strip_control_line('{"control": "continue"}') == ""
    assert lj.strip_control_line("just prose, no control\n\n") == "just prose, no control"
    # an object followed by prose is reasoning, not a trailing control: kept
    text = 'reasoning\n{\n  "control": "done"\n}\nmore prose'
    assert lj.strip_control_line(text) == text


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
