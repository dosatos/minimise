"""Server-rendered loop pages and their read-only polling APIs."""

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from flask import Flask, jsonify, render_template, request

from minimise.interfaces.formatting import format_duration
from minimise.interfaces.prompt_preview import worker_prompt_preview
from minimise.models import JobStatus, Loop, LoopSpec, LoopStep, TaskStatus
from minimise.orchestration import loop_journal
from minimise.personas import load_personas
from minimise.storage.database import Database
from minimise.storage.loop_store import LoopStore

LOOPS_PAGE_SIZE = 50
ARTIFACT_LIMIT = 100


def _stage_label(step_type: Optional[str], dimension: Optional[str] = None) -> str:
    if not step_type:
        return "-"
    labels = {"plan": "Plan", "implement": "Implement", "evaluate": "Evaluate"}
    label = labels.get(step_type, step_type.replace("_", " ").title())
    return f"{label} / {dimension}" if dimension else label


def _current_stage(steps: list[LoopStep]) -> str:
    if not steps:
        return "Not started"
    step = next(
        (candidate for candidate in reversed(steps)
         if candidate.status == TaskStatus.RUNNING),
        steps[-1],
    )
    return _stage_label(step.step_type, step.dimension)


def _serialize_step(step: LoopStep) -> dict:
    return {
        "step_id": step.step_id,
        "iteration": step.iteration,
        "step_type": step.step_type,
        "stage": _stage_label(step.step_type),
        "dimension": step.dimension,
        "status": step.status.value,
        "retries": step.retries,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        "duration": format_duration(
            step.started_at,
            step.completed_at,
            is_running=step.status == TaskStatus.RUNNING,
        ),
    }


_FINISHED_STEP_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)


def _iteration_status(steps: list[LoopStep]) -> str:
    if any(step.status == TaskStatus.RUNNING for step in steps):
        return "running"
    started = [step for step in steps if step.started_at]
    if started:
        # Parallel evaluators can share the latest start, so any of them counts as last.
        last = max(step.started_at for step in started)
        for status in (TaskStatus.FAILED, TaskStatus.STOPPED):  # stopped: a usage limit or a stop
            if any(step.status == status and step.started_at == last for step in started):
                return status.value
    if all(step.status in _FINISHED_STEP_STATUSES for step in steps):
        return "completed"
    return "pending"


_VERDICTS = ("pass", "fail", "blocked")


def _normalize_verdict(value) -> Optional[str]:
    """verdict is agent convention, not schema: keep only pass, fail or blocked."""
    if not isinstance(value, str):
        return None
    verdict = value.strip().lower()
    return verdict if verdict in _VERDICTS else None


def _evaluate_records(steps: list[LoopStep], journal_records) -> dict[str, dict]:
    """Map evaluate step_id -> its journal record; the last matching record wins.

    The engine stamps step_id on every record; older records without one
    fall back to (iteration, dimension), resolved to that slot's latest step.
    """
    evaluate = [step for step in steps if step.step_type == "evaluate"]
    step_ids = {step.step_id for step in evaluate}
    by_slot = {(step.iteration, step.dimension): step.step_id for step in evaluate}
    matched: dict[str, dict] = {}
    for record in journal_records:
        if record.get("step_type") != "evaluate":
            continue
        step_id = record.get("step_id")
        if not step_id:
            step_id = by_slot.get((record.get("iteration"), record.get("dimension")))
        if step_id in step_ids:
            matched[step_id] = record
    return matched


def _carried_from(record: dict) -> Optional[int]:
    """select: failed — the iteration a carried-over pass was really evaluated in."""
    value = record.get("evaluated_in_iteration")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _iteration_passing(members: list[dict], dimension_count: int) -> Optional[dict]:
    """An iteration's passed/total evaluators, or None when none has a verdict.

    A dimension counts once, by its latest verdict, so a re-run evaluator
    can't push N past M. ``carried`` counts dimensions whose latest step is a
    pass carried over from an earlier iteration; those already count as passed.
    """
    latest = {s["dimension"]: s["verdict"] for s in members if s["verdict"]}
    if not latest:
        return None
    last_step = {s["dimension"]: s for s in members if s["step_type"] == "evaluate"}
    return {
        "passed": sum(verdict == "pass" for verdict in latest.values()),
        "total": max(dimension_count, len(last_step)),
        "carried": sum(s["carried_from"] is not None for s in last_step.values()),
    }


def _loop_timeline(
    loop: Loop,
    steps: list[LoopStep],
    now: datetime,
    *,
    journal_records=(),
    dimension_count: int = 0,
) -> dict:
    """Group a loop's steps by iteration on one timeline measured in seconds
    from its origin.

    started_at resets on every run, so a resumed loop's earlier steps began
    before it: the origin is whichever came first, and ``run_start_offset``
    marks where the current run began. Evaluators within an iteration run in
    parallel, so an iteration's duration is its wall time, never a sum. A
    running iteration has no duration; the client ticks it from start_offset.

    A finished evaluate step carries its journaled ``verdict``, and an
    iteration with any verdict gets ``passing`` out of ``dimension_count``.
    Under select: failed, a pass carried over from an earlier iteration has
    ``carried_from`` (and no bar: nothing ran), and a re-check has ``recheck``.
    """
    origin = min(
        [t for t in (loop.started_at, *(step.started_at for step in steps)) if t],
        default=None,
    )
    records = _evaluate_records(steps, journal_records)
    if loop.status == JobStatus.RUNNING:
        end = now
    else:
        end = loop.completed_at or max(
            (step.completed_at for step in steps if step.completed_at), default=now
        )

    def offset(moment: Optional[datetime]) -> Optional[float]:
        if moment is None or origin is None:
            return None
        return round((moment - origin).total_seconds(), 1)

    def timed(step: LoopStep) -> dict:
        start = offset(step.started_at)
        record = (
            records.get(step.step_id, {})
            if step.step_type == "evaluate" and step.status != TaskStatus.RUNNING
            else {}
        )
        carried_from = _carried_from(record)
        return {
            **_serialize_step(step),
            "start_offset": start,
            "duration": (
                round((step.completed_at - step.started_at).total_seconds(), 1)
                if step.started_at and step.completed_at else None
            ),
            "bar": None if start is None or carried_from is not None else {
                "start": start,
                "end": max(start, offset(step.completed_at or end)),
            },
            "verdict": _normalize_verdict(record.get("verdict")),
            "carried_from": carried_from,
            "recheck": record.get("recheck") is True,
        }

    by_iteration: dict[int, list[LoopStep]] = {}
    for step in steps:
        by_iteration.setdefault(step.iteration, []).append(step)

    groups = []
    for iteration in sorted(by_iteration):
        members = by_iteration[iteration]
        status = _iteration_status(members)
        first = min((step.started_at for step in members if step.started_at), default=None)
        last = max(
            (step.completed_at for step in members if step.started_at and step.completed_at),
            default=None,
        )
        parts = [timed(step) for step in members]
        groups.append({
            "iteration": iteration,
            "status": status,
            "start_offset": offset(first),
            "duration": (
                round((last - first).total_seconds(), 1)
                if first and last and status != "running" else None
            ),
            "passing": _iteration_passing(parts, dimension_count),
            "steps": parts,
        })

    durations = [g["duration"] for g in groups
                 if g["status"] != "running" and g["duration"] is not None]
    now_offset = offset(end)
    bar_ends = [s["bar"]["end"] for g in groups for s in g["steps"] if s["bar"]]
    return {
        "now_offset": now_offset,
        "run_start_offset": offset(loop.started_at),
        "total_secs": round(max(bar_ends + [now_offset or 0, 1]), 1),
        "avg_iteration_secs": (
            round(sum(durations) / len(durations), 1) if durations else None
        ),
        "groups": groups,
    }


def _serialize_loop(
    loop: Loop,
    steps: list[LoopStep],
    spec: Optional[LoopSpec] = None,
    include_steps: bool = True,
    journal_records=(),
) -> dict:
    current_iteration = max((step.iteration for step in steps), default=0)
    max_iterations = spec.max_iterations if spec else loop.max_iterations
    result = {
        "loop_id": loop.loop_id,
        "name": loop.name,
        "status": loop.status.value,
        "iteration": current_iteration,
        "max_iterations": max_iterations,
        "stage": _current_stage(steps),
        "plan_version": spec.plan_version if spec else None,
        "evaluator_count": len(spec.loop.evaluate.dimensions) if spec else None,
        "created_at": loop.created_at.isoformat() if loop.created_at else None,
        "started_at": loop.started_at.isoformat() if loop.started_at else None,
        "completed_at": loop.completed_at.isoformat() if loop.completed_at else None,
        "elapsed": format_duration(
            loop.started_at,
            loop.completed_at,
            is_running=loop.status == JobStatus.RUNNING,
        ),
    }
    if include_steps:
        result["stop_reason"] = loop.stop_reason
        result["steps"] = [_serialize_step(step) for step in steps]
        result["timeline"] = _loop_timeline(
            loop,
            steps,
            datetime.utcnow(),
            journal_records=journal_records,
            dimension_count=len(spec.loop.evaluate.dimensions) if spec else 0,
        )
    return result


def _load_spec(
    store: LoopStore,
    loop_id: str,
) -> tuple[Optional[LoopSpec], Optional[str], Optional[str]]:
    path = store.jobs_dir / loop_id / "plan.yaml"
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, None, "The cached loop spec is missing."
    except OSError as exc:
        return None, None, f"The cached loop spec could not be read: {exc}"

    try:
        return store.load_spec(loop_id), raw, None
    except Exception as exc:
        return None, raw, f"The cached loop spec could not be parsed: {exc}"


def _worker_prompts(spec: LoopSpec) -> dict:
    """Prompt previews for the plan, implement and evaluate workers."""
    workers = [spec.loop.plan, spec.loop.implement, *spec.loop.evaluate.dimensions]
    personas, load_error = None, None
    if any(worker.persona for worker in workers):
        import minimise.interfaces.cli as _cli  # lazy: avoids circular import (cli.view imports us)

        try:
            personas = load_personas(_cli.CONFIG_DIR)
        except Exception as exc:
            load_error = str(exc)

    def preview(worker, step_type: str) -> Optional[dict]:
        return worker_prompt_preview(worker, step_type, personas, load_error)

    return {
        "plan": preview(spec.loop.plan, "plan"),
        "implement": preview(spec.loop.implement, "implement"),
        "dimensions": [preview(dimension, "evaluate")
                       for dimension in spec.loop.evaluate.dimensions],
    }


def _load_loop_page(
    db: Database,
    store: LoopStore,
    page: int,
) -> tuple[list[dict], bool]:
    offset = (page - 1) * LOOPS_PAGE_SIZE
    loops = store.load_many(limit=LOOPS_PAGE_SIZE + 1, offset=offset)
    has_next = len(loops) > LOOPS_PAGE_SIZE
    views = []
    for loop in loops[:LOOPS_PAGE_SIZE]:
        spec, _, _ = _load_spec(store, loop.loop_id)
        views.append(_serialize_loop(
            loop,
            db.list_loop_steps(loop.loop_id),
            spec,
            include_steps=False,
        ))
    return views, has_next


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # Evaluators write findings as a list of strings: one paragraph each.
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n\n".join(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _outcome_tone(outcome: str) -> str:
    normalized = outcome.strip().lower()
    if normalized in {"pass", "done", "committed"}:
        return "success"
    if normalized in {"fail", "failed"}:
        return "danger"
    if normalized in {"blocked", "stop"}:
        return "warning"
    if normalized == "continue":
        return "active"
    return "neutral"


def _journal_entry(record: dict) -> dict:
    if record.get("marker") == "commit":
        iteration = record.get("iteration")
        stage = "Iteration"
        outcome = "Committed"
        message = f"Iteration {iteration} completed."
    elif record.get("event") == "patched":
        iteration = None
        stage = "Spec"
        outcome = f"v{record.get('plan_version', '?')}"
        message = "Loop specification patched for the next step."
    else:
        iteration = record.get("iteration")
        stage = _stage_label(record.get("step_type"), record.get("dimension"))
        outcome = _text_value(record.get("verdict") or record.get("control") or "Recorded")
        message = next(
            (
                _text_value(record.get(key))
                for key in ("summary", "findings", "handover", "plan")
                if record.get(key) is not None
            ),
            "",
        )

    return {
        "timestamp": record.get("timestamp") or "-",
        "iteration": iteration,
        "stage": stage,
        "outcome": outcome,
        "tone": _outcome_tone(outcome),
        "message": message or "No summary provided.",
        "raw": json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False),
    }


def _read_journal(store: LoopStore, loop_id: str) -> list[dict]:
    return loop_journal.read(store.journal_path(loop_id))


def _journal_entries(records: list[dict], limit: int) -> list[dict]:
    return [_journal_entry(record) for record in records[-limit:]]


def _read_jsonl_tail(
    path: Path,
    limit: int,
    matches: Optional[Callable[[dict], bool]] = None,
) -> list[dict]:
    if not path.exists():
        return []

    records = deque(maxlen=limit)
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    record = {"message": line}
            except json.JSONDecodeError:
                record = {"message": line}
            if matches is None or matches(record):
                records.append(record)
    return list(records)


def _loop_log_entry(record: dict) -> dict:
    return {
        "timestamp": record.get("timestamp") or "-",
        "iteration": record.get("iteration"),
        "stage": _stage_label(record.get("step_type"), record.get("dimension")),
        "level": record.get("level") or "",
        "message": _text_value(record.get("message")),
    }


def _loop_log_entries(
    store: LoopStore,
    loop_id: str,
    limit: int,
    step_type: Optional[str] = None,
    iteration: Optional[int] = None,
    dimension: Optional[str] = None,
) -> list[dict]:
    def matches(record: dict) -> bool:
        if step_type and record.get("step_type") != step_type:
            return False
        if iteration is not None and record.get("iteration") != iteration:
            return False
        if dimension and record.get("dimension") != dimension:
            return False
        return True

    records = _read_jsonl_tail(store.loop_log_path(loop_id), limit, matches)
    return [_loop_log_entry(record) for record in records]


def _loop_log_filter_options(
    spec: Optional[LoopSpec],
    steps: list[LoopStep],
) -> dict:
    dimensions = []
    seen_dimensions = set()

    if spec:
        for dimension in spec.loop.evaluate.dimensions:
            dimensions.append(dimension.name)
            seen_dimensions.add(dimension.name)
    for step in steps:
        if step.dimension and step.dimension not in seen_dimensions:
            dimensions.append(step.dimension)
            seen_dimensions.add(step.dimension)

    return {
        "iterations": sorted({step.iteration for step in steps}),
        "dimensions": dimensions,
    }


def _active_filter(value: Optional[str]) -> Optional[str]:
    return None if not value or value == "all" else value


def _artifact_limit() -> int:
    limit = request.args.get("limit", ARTIFACT_LIMIT, type=int)
    return ARTIFACT_LIMIT if limit is None or limit <= 0 else min(limit, 500)


def register_loop_routes(app: Flask, db: Database, store: LoopStore) -> None:
    """Register loop HTML pages and read-only JSON endpoints."""

    @app.route("/loops", methods=["GET"], strict_slashes=False)
    def loop_list_page():
        page = max(1, request.args.get("page", 1, type=int))
        loops, has_next = _load_loop_page(db, store, page)
        return render_template(
            "loops.html",
            loops=loops,
            page=page,
            has_next=has_next,
        )

    @app.route("/loops/<loop_id>/view", methods=["GET"])
    def loop_detail_page(loop_id: str):
        loop = store.load(loop_id)
        if loop is None:
            return "Loop not found", 404

        spec, spec_raw, spec_error = _load_spec(store, loop_id)
        steps = db.list_loop_steps(loop_id)
        journal = _read_journal(store, loop_id)
        return render_template(
            "loop_detail.html",
            loop_view=_serialize_loop(loop, steps, spec, journal_records=journal),
            spec=spec,
            spec_raw=spec_raw,
            spec_error=spec_error,
            worker_prompts=_worker_prompts(spec) if spec else None,
            journal_entries=_journal_entries(journal, ARTIFACT_LIMIT),
            log_entries=_loop_log_entries(store, loop_id, ARTIFACT_LIMIT),
            log_filter_options=_loop_log_filter_options(spec, steps),
        )

    @app.route("/api/loops", methods=["GET"])
    def get_loops_api():
        page = max(1, request.args.get("page", 1, type=int))
        loops, has_next = _load_loop_page(db, store, page)
        response = jsonify(loops)
        response.headers["X-Has-Next"] = "true" if has_next else "false"
        return response, 200

    @app.route("/api/loops/<loop_id>", methods=["GET"])
    def get_loop_api(loop_id: str):
        loop = store.load(loop_id)
        if loop is None:
            return jsonify({"error": "Loop not found"}), 404
        spec, _, _ = _load_spec(store, loop_id)
        return jsonify(_serialize_loop(
            loop,
            db.list_loop_steps(loop_id),
            spec,
            journal_records=_read_journal(store, loop_id),
        )), 200

    @app.route("/api/loops/<loop_id>/journal", methods=["GET"])
    def get_loop_journal_api(loop_id: str):
        if store.load(loop_id) is None:
            return jsonify({"error": "Loop not found"}), 404
        return jsonify({
            "records": _journal_entries(_read_journal(store, loop_id), _artifact_limit()),
        }), 200

    @app.route("/api/loops/<loop_id>/logs", methods=["GET"])
    def get_loop_logs_api(loop_id: str):
        if store.load(loop_id) is None:
            return jsonify({"error": "Loop not found"}), 404
        spec, _, _ = _load_spec(store, loop_id)
        steps = db.list_loop_steps(loop_id)
        return jsonify({
            "records": _loop_log_entries(
                store,
                loop_id,
                _artifact_limit(),
                _active_filter(request.args.get("step_type")),
                request.args.get("iteration", type=int),
                _active_filter(request.args.get("dimension")),
            ),
            "filter_options": _loop_log_filter_options(spec, steps),
        }), 200
