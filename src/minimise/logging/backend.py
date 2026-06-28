"""Swappable job-log backend: the seam between the CLI/harness and the engine.

`JsonlLogBackend` is the only impl now — `record()` appends one JSON line,
`search()` runs a `LogQuery` over the file with stdlib (filter → sort → limit).
A DuckDB/CloudWatch backend later is a new class reading the same JSONL file;
nothing else changes.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator

from .log_query import FilterExpr, LogQuery, Op, Predicate


class JobLogBackend(ABC):
    @abstractmethod
    def record(self, log_path, fields: Dict, text: str, level: str = "info") -> None:
        """Write one log line."""

    @abstractmethod
    def search(self, log_path, query: LogQuery) -> Iterator[dict]:
        """Yield matching records, filtered → sorted → limited."""

    @abstractmethod
    def matches(self, query: LogQuery, rec: dict) -> bool:
        """Does one record satisfy the query's filter? (No filter → True.)

        The live-tail seam: callers test a single streamed record without
        reaching into backend internals.
        """


def _matches(pred: Predicate, rec: dict) -> bool:
    # Values are quoted strings in the query surface, so compare as strings
    # deliberately (numeric/string record values both coerce here).
    actual = str(rec.get(pred.field, ""))
    if pred.op is Op.EQ:
        return actual == pred.value
    if pred.op is Op.NE:
        return actual != pred.value
    return pred.value in actual  # Op.LIKE — substring intent


def _filter_ok(expr: FilterExpr, rec: dict) -> bool:
    """Predicates joined left-to-right; no precedence (no parens in the grammar)."""
    result = _matches(expr.predicates[0], rec)
    for connector, pred in zip(expr.connectors, expr.predicates[1:]):
        nxt = _matches(pred, rec)
        result = (result and nxt) if connector == "and" else (result or nxt)
    return result


class JsonlLogBackend(JobLogBackend):
    def matches(self, query: LogQuery, rec: dict) -> bool:
        return query.filters is None or _filter_ok(query.filters, rec)

    def record(self, log_path, fields: Dict, text: str, level: str = "info") -> None:
        rec = {"timestamp": datetime.now().isoformat(timespec="seconds"),
               **fields, "level": level, "message": text}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def search(self, log_path, query: LogQuery) -> Iterator[dict]:
        path = Path(log_path)
        if not path.exists():
            return iter([])
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    rec = {"message": line}
            except json.JSONDecodeError:
                rec = {"message": line}  # legacy flat-text line
            records.append(rec)

        if query.filters:
            records = [r for r in records if _filter_ok(query.filters, r)]
        records.sort(key=lambda r: str(r.get(query.sort_key, "")),
                     reverse=query.sort_desc)
        if query.limit is not None:
            records = records[:query.limit]
        return iter(records)
