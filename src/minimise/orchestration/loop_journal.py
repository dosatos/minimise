"""Append-only JSONL journal for the refinement loop.

Carries the loop's *content* (agent control lines, commit markers) across
fresh-context steps. Status/timing live in the DB — the journal and the DB are
deliberately separate, and this module never touches the DB.

The loop ENGINE is the sole writer: step agents EMIT a control line as their
last line of output, the engine appends it here. This module is just the
append/parse primitives.
"""
import json
from pathlib import Path
from typing import Optional

CONTROLS = {"continue", "stop", "done", "failed"}


def append(journal_path, record: dict) -> None:
    """Append one JSON line."""
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read(journal_path) -> list:
    """Read all lines, skipping any un-parseable (e.g. a trailing partial)."""
    path = Path(journal_path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def extract_last_json(text: str) -> Optional[dict]:
    """Pull the last JSON object out of an agent's raw output.

    Tolerant of prose before/around the control line. Returns None if no line
    parses to a JSON object (the validation gate treats None as malformed).
    """
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            return rec
    return None


def parse_control(last_line: dict) -> str:
    """Validate the reserved keys on a step's last line, return the control.

    Everything else on the line is agent convention — not validated here.
    Raises ValueError (caught by the validation gate) on a bad control value
    or a "failed" line missing a non-empty handover.
    """
    if not isinstance(last_line, dict):
        raise ValueError("control line is not a JSON object")
    control = last_line.get("control")
    if control not in CONTROLS:
        raise ValueError(f"unknown control {control!r}; expected one of {sorted(CONTROLS)}")
    if control == "failed" and not last_line.get("handover"):
        raise ValueError("control 'failed' requires a non-empty 'handover'")
    return control


def write_commit_marker(journal_path, iteration: int) -> None:
    """Append the engine's marker that closes iteration N."""
    append(journal_path, {"marker": "commit", "iteration": iteration})


def last_committed_iteration(journal_path) -> int:
    """Highest committed iteration; 0 if none. Anything after it is incomplete."""
    last = 0
    for rec in read(journal_path):
        if rec.get("marker") == "commit":
            last = rec.get("iteration", last)
    return last


def demo():
    import tempfile, os
    d = tempfile.mkdtemp()
    p = os.path.join(d, "journal.jsonl")

    append(p, {"control": "continue", "note": "step 1"})
    append(p, {"control": "done"})
    assert len(read(p)) == 2

    # extract_last_json: prose before, then the control line
    assert extract_last_json("thinking...\nblah\n{\"control\": \"stop\"}") == {"control": "stop"}
    assert extract_last_json("no json here") is None

    # parse_control: happy paths + rejections
    assert parse_control({"control": "continue"}) == "continue"
    assert parse_control({"control": "failed", "handover": "ran out of ideas"}) == "failed"
    for bad in ({"control": "failed"}, {"control": "bogus"}, {}):
        try:
            parse_control(bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass

    # commit markers + resume boundary (partial trailing line tolerated)
    assert last_committed_iteration(p) == 0
    write_commit_marker(p, 1)
    write_commit_marker(p, 2)
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"control": "continue"')  # partial, un-terminated
    assert last_committed_iteration(p) == 2
    assert len(read(p)) == 4  # 2 controls + 2 markers, partial skipped

    print("loop_journal demo OK")


if __name__ == "__main__":
    demo()
