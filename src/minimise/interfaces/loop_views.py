"""Server-rendered loop pages and their read-only polling APIs."""

import json
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from flask import Flask, jsonify, render_template, request

from minimise.interfaces.formatting import format_duration
from minimise.models import JobStatus, Loop, LoopSpec, LoopStep, TaskStatus
from minimise.orchestration import loop_journal
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


def _serialize_loop(
    loop: Loop,
    steps: list[LoopStep],
    spec: Optional[LoopSpec] = None,
    include_steps: bool = True,
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
        result["steps"] = [_serialize_step(step) for step in steps]
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
    return json.dumps(value, sort_keys=True)


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
        "raw": json.dumps(record, indent=2, sort_keys=True),
    }


def _journal_entries(store: LoopStore, loop_id: str, limit: int) -> list[dict]:
    records = loop_journal.read(store.journal_path(loop_id))
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
        return render_template(
            "loop_detail.html",
            loop_view=_serialize_loop(loop, steps, spec),
            spec=spec,
            spec_raw=spec_raw,
            spec_error=spec_error,
            journal_entries=_journal_entries(store, loop_id, ARTIFACT_LIMIT),
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
        return jsonify(
            _serialize_loop(loop, db.list_loop_steps(loop_id), spec)
        ), 200

    @app.route("/api/loops/<loop_id>/journal", methods=["GET"])
    def get_loop_journal_api(loop_id: str):
        if store.load(loop_id) is None:
            return jsonify({"error": "Loop not found"}), 404
        return jsonify({
            "records": _journal_entries(store, loop_id, _artifact_limit()),
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
