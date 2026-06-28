"""Engine-neutral query IR (`LogQuery`) and a CloudWatch Insights-style parser.

The IR encodes *intent, not dialect*: `LIKE` means "substring", never a raw SQL
`%x%` or a regex. Backends render that intent into their own syntax, so the engine
can change (JSONL now, DuckDB/CloudWatch later) without touching this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import pyparsing as pp


class Op(Enum):
    EQ = "="
    NE = "!="
    LIKE = "like"


@dataclass
class Predicate:
    field: str
    op: Op
    value: str


@dataclass
class FilterExpr:
    """Predicates joined by left-to-right `and`/`or` (no parens)."""

    predicates: List[Predicate]
    connectors: List[str]  # one fewer than predicates; values "and"/"or"


@dataclass
class LogQuery:
    fields: List[str] = field(default_factory=list)
    filters: Optional[FilterExpr] = None
    sort_key: str = "timestamp"
    sort_desc: bool = False
    sort_present: bool = False  # was a `sort` clause given (vs the default)?
    limit: Optional[int] = None


def _map_field(name: str) -> str:
    """`@timestamp` → `timestamp`; `@message` is kept (whole-record marker)."""
    return "timestamp" if name == "@timestamp" else name


# --- grammar ------------------------------------------------------------------

_field = pp.Combine(pp.Optional("@") + pp.Word(pp.alphas + "_", pp.alphanums + "_"))
_value = pp.QuotedString('"')
_op = pp.one_of("!= = like")
_connector = pp.one_of("and or")

_predicate = pp.Group(_field("field") + _op("op") + _value("value"))
_filter_body = _predicate + pp.ZeroOrMore(_connector + _predicate)

_fields_clause = pp.Group(
    pp.Keyword("fields") + pp.DelimitedList(_field)
)("fields_clause")
_filter_clause = pp.Group(pp.Keyword("filter") + _filter_body)("filter_clause")
_sort_clause = pp.Group(
    pp.Keyword("sort") + _field("key") + pp.Optional(pp.one_of("asc desc"))("dir")
)("sort_clause")
_limit_clause = pp.Group(pp.Keyword("limit") + pp.pyparsing_common.integer("n"))(
    "limit_clause"
)

_clause = _fields_clause | _filter_clause | _sort_clause | _limit_clause
_query = pp.Optional(pp.DelimitedList(_clause, delim="|"))


def parse_query(text: str) -> LogQuery:
    """Parse an Insights-style query string into a `LogQuery`.

    Bad syntax raises `ValueError` with a clear message.
    """
    try:
        parsed = _query.parse_string(text, parse_all=True)
    except pp.ParseBaseException as exc:
        raise ValueError(f"Invalid query: {exc}") from exc

    q = LogQuery()
    for clause in parsed:
        name = clause.get_name()
        if name == "fields_clause":
            q.fields = [_map_field(tok) for tok in clause[1:]]
        elif name == "filter_clause":
            preds, connectors = [], []
            for tok in clause[1:]:
                if isinstance(tok, str):
                    connectors.append(tok)
                else:
                    preds.append(
                        Predicate(_map_field(tok["field"]), Op(tok["op"]), tok["value"]))
            q.filters = FilterExpr(preds, connectors)
        elif name == "sort_clause":
            q.sort_key = _map_field(clause["key"])
            q.sort_desc = clause.get("dir") == "desc"
            q.sort_present = True
        elif name == "limit_clause":
            q.limit = clause["n"]
    return q
