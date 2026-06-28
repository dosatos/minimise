import json

import pytest

from minimise.logging.backend import JsonlLogBackend
from minimise.logging.log_query import LogQuery, Op, Predicate, parse_query


def test_parse_full_query():
    q = parse_query(
        'fields @timestamp, message '
        '| filter type = "task" and execution_id like "task-9f" '
        '| sort @timestamp desc '
        '| limit 20'
    )
    assert q.fields == ["timestamp", "message"]
    assert q.sort_key == "timestamp"
    assert q.sort_desc is True
    assert q.limit == 20
    assert q.filters.predicates == [
        Predicate("type", Op.EQ, "task"),
        Predicate("execution_id", Op.LIKE, "task-9f"),
    ]
    assert q.filters.connectors == ["and"]


def test_at_message_maps_to_whole_record():
    q = parse_query("fields @message")
    assert q.fields == ["@message"]


def test_filter_at_timestamp_maps_to_timestamp():
    """`@timestamp` in a filter predicate maps to `timestamp` like elsewhere."""
    q = parse_query('filter @timestamp = "t1"')
    assert q.filters.predicates == [Predicate("timestamp", Op.EQ, "t1")]


def test_verbs_optional_and_order_independent():
    q = parse_query("limit 5 | filter level != \"debug\"")
    assert q.limit == 5
    assert q.fields == []
    assert q.sort_key == "timestamp"  # default
    assert q.sort_desc is False
    assert q.filters.predicates == [Predicate("level", Op.NE, "debug")]
    assert q.filters.connectors == []


def test_empty_query_defaults():
    q = parse_query("")
    assert q.fields == []
    assert q.filters is None
    assert q.sort_key == "timestamp"
    assert q.sort_desc is False
    assert q.limit is None


def test_or_connector_left_to_right():
    q = parse_query('filter a = "1" or b = "2" and c like "3"')
    assert [p.field for p in q.filters.predicates] == ["a", "b", "c"]
    assert q.filters.connectors == ["or", "and"]


def test_sort_default_asc():
    q = parse_query("sort message")
    assert q.sort_key == "message"
    assert q.sort_desc is False


@pytest.mark.parametrize("bad", [
    "fields",            # no field list
    "filter type task",  # missing operator
    "limit abc",         # non-numeric limit
    "sort",              # no key
    "wibble foo",        # unknown verb
])
def test_bad_syntax_raises(bad):
    with pytest.raises(ValueError):
        parse_query(bad)


# --- backend ------------------------------------------------------------------

_RECORDS = [
    {"timestamp": "2026-06-27T01:00:00", "type": "task", "level": "info",
     "execution_id": "task-9f", "message": "alpha"},
    {"timestamp": "2026-06-27T01:00:02", "type": "pre_task", "level": "info",
     "execution_id": "task-9f", "message": "beta running pytest"},
    {"timestamp": "2026-06-27T01:00:01", "type": "task", "level": "debug",
     "execution_id": "task-7a", "message": "gamma"},
]


def _seed(tmp_path):
    # Write raw lines so timestamps are deterministic (record() stamps its own).
    log = tmp_path / "job.log"
    log.write_text("".join(json.dumps(r) + "\n" for r in _RECORDS))
    return JsonlLogBackend(), log


def test_record_appends_json_line(tmp_path):
    backend = JsonlLogBackend()
    log = tmp_path / "job.log"
    backend.record(log, {"type": "task", "execution_id": "task-9f"}, "hello", "info")
    line = log.read_text().strip()
    rec = json.loads(line)
    assert rec["type"] == "task"
    assert rec["execution_id"] == "task-9f"
    assert rec["message"] == "hello"
    assert rec["level"] == "info"
    assert "timestamp" in rec


def test_search_filter_eq(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(log, parse_query('filter type = "task"')))
    assert [r["message"] for r in out] == ["alpha", "gamma"]


def test_search_filter_ne(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(log, parse_query('filter level != "info"')))
    assert [r["message"] for r in out] == ["gamma"]


def test_search_filter_like_substring(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(log, parse_query('filter message like "pytest"')))
    assert [r["message"] for r in out] == ["beta running pytest"]


def test_search_filter_at_timestamp_matches_records(tmp_path):
    """A `@timestamp` time filter must match real records keyed on `timestamp`."""
    backend, log = _seed(tmp_path)
    out = list(backend.search(log, parse_query('filter @timestamp like "01:00:0"')))
    assert sorted(r["message"] for r in out) == [
        "alpha", "beta running pytest", "gamma"]


def test_search_filter_and(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(
        log, parse_query('filter type = "task" and level = "info"')))
    assert [r["message"] for r in out] == ["alpha"]


def test_search_filter_or(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(
        log, parse_query('filter type = "pre_task" or level = "debug"')))
    assert sorted(r["message"] for r in out) == ["beta running pytest", "gamma"]


def test_search_sort_desc_then_limit(tmp_path):
    backend, log = _seed(tmp_path)
    out = list(backend.search(log, parse_query("sort @timestamp desc | limit 2")))
    assert [r["message"] for r in out] == ["beta running pytest", "gamma"]


def test_search_non_json_line_tolerated(tmp_path):
    backend = JsonlLogBackend()
    log = tmp_path / "job.log"
    log.write_text("[2026-06-27] legacy flat line\n")
    out = list(backend.search(log, parse_query("")))
    assert out == [{"message": "[2026-06-27] legacy flat line"}]


def test_search_missing_file_is_empty(tmp_path):
    backend = JsonlLogBackend()
    out = list(backend.search(tmp_path / "nope.log", parse_query("")))
    assert out == []


def test_backend_matches_is_public_seam():
    """A single record can be tested against a query via a public method,
    so callers (the live-tail) need not reach for a private helper."""
    backend = JsonlLogBackend()
    q = parse_query('filter level = "info"')
    assert backend.matches(q, {"level": "info"}) is True
    assert backend.matches(q, {"level": "debug"}) is False
    # No filter clause → every record matches.
    assert backend.matches(parse_query(""), {"level": "anything"}) is True


def test_matches_compares_values_as_strings():
    """Per spec, query values are quoted strings; comparison is string-based,
    so a numeric record value equals its string form (documented behaviour)."""
    backend = JsonlLogBackend()
    q = parse_query('filter level = "1"')
    assert backend.matches(q, {"level": "1"}) is True
    assert backend.matches(q, {"level": 1}) is True
