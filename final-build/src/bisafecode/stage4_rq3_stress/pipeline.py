"""Commit-bound, create-once EXP-S4-010 stress execution and analysis."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import statistics
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from bisafecode.stage4_controlled.methods import COMMON_BUDGET, execute_method
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns

from . import (
    CONTRACT_RELATIVE,
    DERIVED_RELATIVE,
    DERIVED_STATUS,
    EXPERIMENT_ID,
    RAW_RELATIVE,
    RAW_STATUS,
)
from .generator import (
    REPETITIONS_PER_EXECUTED_LEVEL,
    STRESS_LEVELS,
    STRUCTURAL_CAPS,
    stress_candidates,
)


STATE_THRESHOLD = 50_000
TRANSITION_THRESHOLD = 250_000
CHECKER_MS_THRESHOLD = 30_000.0
SUPERLINEAR_ELASTICITY_THRESHOLD = 1.5
SUPERLINEAR_MIN_STATES = 10_000


class StressGateLocked(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _head(root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        capture_output=True, check=False,
    )
    if process.returncode != 0:
        raise StressGateLocked("cannot resolve exact freeze commit")
    return process.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise StressGateLocked(f"create-once output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _write_text_once(path: Path, value: str) -> None:
    if path.exists():
        raise StressGateLocked(f"create-once output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _tree_root(root: Path, paths: Sequence[Path]) -> str:
    inventory = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(paths)
    ]
    return hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def approval_template(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    return {
        "schema": "bisafecode.stage4.rq3-stress.authorization/v1",
        "experiment_id": EXPERIMENT_ID,
        "approved": True,
        "status": "APPROVED_FOR_CREATE_ONCE_STRESS_RUN",
        "reviewer": "Anonymous User",
        "authorization_source": (
            "authorization recorded before one-time stress run; source privately retained"
        ),
        "freeze_commit": _head(root),
        "contract_sha256": _sha256(root / CONTRACT_RELATIVE),
        "paper_export_authorized": False,
    }


def validate_approval(approval: Mapping[str, Any], *, root: Path) -> None:
    if dict(approval) != dict(approval_template(root)):
        raise StressGateLocked("authorization does not exactly bind the frozen stress protocol")


def _candidate_complexity(candidate: Any) -> Mapping[str, Any]:
    tree = ast.parse(candidate.source, filename=f"<{candidate.program_id}>", mode="exec")
    named_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    api_names = {
        "acquire", "release", "close", "open", "transfer_authority",
        "move", "wait", "barrier", "parallel",
    }
    source_api_call_count = sum(node.func.id in api_names for node in named_calls)
    branch_count = sum(isinstance(node, ast.If) for node in ast.walk(tree))
    parallel_region_count = sum(
        node.func.id == "parallel" for node in named_calls
    )
    resource_ids = {
        node.args[1].value
        for node in named_calls
        if node.func.id in {"acquire", "release"}
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    }
    complexity = {
        "source_line_count": len(candidate.source.splitlines()),
        "source_api_call_count": source_api_call_count,
        "branch_count": branch_count,
        "parallel_region_count": parallel_region_count,
        "parallel_width": 2 if parallel_region_count else 1,
        "resource_count": len(resource_ids),
        "source_time_upper_bound_ns": source_time_upper_bound_ns(candidate.source, {}),
    }
    if candidate.axis == "program_length":
        actual = int(complexity["source_api_call_count"])
    elif candidate.axis == "branch_count":
        actual = int(complexity["branch_count"])
    else:
        actual = int(complexity["parallel_region_count"])
        if (
            actual != 1
            or complexity["parallel_width"] != 2
            or complexity["resource_count"] != 2
            or complexity["source_api_call_count"] != 4 * candidate.requested_level + 1
        ):
            raise StressGateLocked("parallel stress source left the frozen two-lane language")
        actual = candidate.requested_level
    if actual != candidate.requested_level:
        raise StressGateLocked(f"stress axis extraction mismatch: {candidate.program_id}")
    if complexity["source_time_upper_bound_ns"] > COMMON_BUDGET["max_model_time_ns"]:
        raise StressGateLocked("stress source exceeds the common model-time budget by construction")
    return complexity


def _stop_thresholds() -> Mapping[str, Any]:
    return {
        "state_threshold": STATE_THRESHOLD,
        "transition_threshold": TRANSITION_THRESHOLD,
        "checker_elapsed_ms_threshold": CHECKER_MS_THRESHOLD,
        "superlinear_elasticity_threshold": SUPERLINEAR_ELASTICITY_THRESHOLD,
        "superlinear_min_states": SUPERLINEAR_MIN_STATES,
    }


def freeze_check(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    contract = _load(root / CONTRACT_RELATIVE)
    candidates = stress_candidates()
    for candidate in candidates:
        _candidate_complexity(candidate)
    raw_exists = (root / RAW_RELATIVE).exists()
    derived_exists = (root / DERIVED_RELATIVE).exists()
    if contract["axes"] != {axis: list(levels) for axis, levels in STRESS_LEVELS.items()}:
        raise StressGateLocked("contract and generator stress grids differ")
    if contract["structural_caps"] != STRUCTURAL_CAPS:
        raise StressGateLocked("contract and executable structural caps differ")
    if contract["common_budget"] != dict(COMMON_BUDGET):
        raise StressGateLocked("stress and original RQ3 common budgets differ")
    if contract["stop_thresholds"] != _stop_thresholds():
        raise StressGateLocked("contract and executable stop thresholds differ")
    return {
        "schema": "bisafecode.stage4.rq3-stress.freeze-check/v1",
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "contract_sha256": _sha256(root / CONTRACT_RELATIVE),
        "axes": {axis: list(levels) for axis, levels in STRESS_LEVELS.items()},
        "structural_caps": STRUCTURAL_CAPS,
        "candidate_count": len(candidates),
        "repetitions_per_executed_level": REPETITIONS_PER_EXECUTED_LEVEL,
        "maximum_scheduled_runs": len(candidates) * REPETITIONS_PER_EXECUTED_LEVEL,
        "unique_source_count": len({item.source_sha256 for item in candidates}),
        "formal_raw_absent": not raw_exists,
        "formal_derived_absent": not derived_exists,
        "ready": not raw_exists and not derived_exists,
        "formal_runs_this_check": 0,
        "paper_result_eligible": False,
    }


def _checker_elapsed_ms(artifact: Mapping[str, Any]) -> float | None:
    outcome = artifact.get("outcome")
    method_artifact = outcome.get("artifact") if isinstance(outcome, dict) else None
    report = method_artifact.get("report") if isinstance(method_artifact, dict) else None
    value = report.get("elapsed_ms") if isinstance(report, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [
        float(row[key]) for row in rows
        if row.get("status") == "ok" and row.get(key) is not None
    ]
    return float(statistics.median(values)) if values else None


def summarize_level(
    axis: str,
    level: int,
    rows: Sequence[Mapping[str, Any]],
    prior_summaries: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if len(rows) != REPETITIONS_PER_EXECUTED_LEVEL:
        raise StressGateLocked("each executed stress level requires exactly three records")
    status_counts = Counter(str(row["status"]) for row in rows)
    medians = {
        field: _median(rows, field)
        for field in (
            "runtime_ms", "checker_elapsed_ms", "peak_rss_bytes",
            "explored_states", "explored_transitions",
        )
    }
    state_elasticity = None
    previous = prior_summaries[-1] if prior_summaries else None
    if (
        previous is not None
        and medians["explored_states"] not in {None, 0.0}
        and previous["medians"]["explored_states"] not in {None, 0.0}
    ):
        state_elasticity = math.log(
            medians["explored_states"] / previous["medians"]["explored_states"]
        ) / math.log(level / int(previous["requested_level"]))
    result: dict[str, Any] = {
        "axis": axis,
        "requested_level": level,
        "scheduled_repetitions": REPETITIONS_PER_EXECUTED_LEVEL,
        "status_counts": dict(sorted(status_counts.items())),
        "verdict_counts": dict(sorted(Counter(
            str(row["verdict"]) for row in rows if row.get("verdict") is not None
        ).items())),
        "medians": medians,
        "state_growth_elasticity_vs_previous_level": state_elasticity,
        "stop_triggered": False,
        "stop_reasons": [],
    }
    reasons = []
    if any(row["status"] != "ok" for row in rows):
        reasons.append("NON_OK_STATUS_OBSERVED")
    if any(
        row["status"] == "ok"
        and row.get("verdict") not in {"verified-within-bounds", "violated"}
        for row in rows
    ):
        reasons.append("NON_CONCLUSIVE_VERDICT_OBSERVED")
    if medians["explored_states"] is not None and medians["explored_states"] >= STATE_THRESHOLD:
        reasons.append("STATE_THRESHOLD_REACHED")
    if medians["explored_transitions"] is not None and medians["explored_transitions"] >= TRANSITION_THRESHOLD:
        reasons.append("TRANSITION_THRESHOLD_REACHED")
    if medians["checker_elapsed_ms"] is not None and medians["checker_elapsed_ms"] >= CHECKER_MS_THRESHOLD:
        reasons.append("CHECKER_TIME_THRESHOLD_REACHED")
    elasticities = [
        item.get("state_growth_elasticity_vs_previous_level")
        for item in [*prior_summaries, result]
    ]
    recent = [value for value in elasticities[-2:] if value is not None]
    if (
        len(recent) == 2
        and all(value >= SUPERLINEAR_ELASTICITY_THRESHOLD for value in recent)
        and medians["explored_states"] is not None
        and medians["explored_states"] >= SUPERLINEAR_MIN_STATES
    ):
        reasons.append("SUSTAINED_SUPERLINEAR_STATE_GROWTH_AT_SCALE")
    result["stop_reasons"] = reasons
    result["stop_triggered"] = bool(reasons)
    return result


def _record_path(raw_root: Path, candidate: Any, repetition: int) -> Path:
    return (
        raw_root / "runs" / candidate.axis / candidate.program_id
        / f"{candidate.program_id}-R{repetition:02d}.record.json"
    )


def _validate_existing_record(
    record: Mapping[str, Any], *, candidate: Any, repetition: int, commit: str
) -> None:
    expected = {
        "experiment_id": EXPERIMENT_ID,
        "formal_commit": commit,
        "program_id": candidate.program_id,
        "source_sha256": candidate.source_sha256,
        "axis": candidate.axis,
        "requested_level": candidate.requested_level,
        "repetition_index": repetition,
        "paper_result_eligible": False,
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise StressGateLocked("existing stress record binding mismatch")


def _ensure_generation(root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_root = root / RAW_RELATIVE
    manifest_path = raw_root / "locked_manifest.json"
    if manifest_path.exists():
        manifest = _load(manifest_path)
        if manifest.get("formal_commit") != _head(root):
            raise StressGateLocked("existing stress manifest has a different freeze commit")
        return manifest
    if raw_root.exists():
        raise StressGateLocked("partial stress generation exists without a manifest")
    records = []
    for candidate in stress_candidates():
        relative = Path("programs") / candidate.axis / f"{candidate.program_id}.py"
        _write_text_once(raw_root / relative, candidate.source)
        records.append({
            "program_id": candidate.program_id,
            "axis": candidate.axis,
            "requested_level": candidate.requested_level,
            "source_path": relative.as_posix(),
            "source_sha256": candidate.source_sha256,
            "repetitions_if_executed": REPETITIONS_PER_EXECUTED_LEVEL,
            "complexity": _candidate_complexity(candidate),
        })
    manifest = {
        "schema": "bisafecode.stage4.rq3-stress.locked-manifest/v1",
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "formal_commit": _head(root),
        "contract_sha256": approval["contract_sha256"],
        "created_utc": _utc(),
        "axes": {axis: list(levels) for axis, levels in STRESS_LEVELS.items()},
        "structural_caps": STRUCTURAL_CAPS,
        "all_candidate_sources_sealed_before_first_method_run": True,
        "per_axis_sequential_stop_rule": True,
        "maximum_scheduled_runs": len(records) * REPETITIONS_PER_EXECUTED_LEVEL,
        "programs": records,
        "paper_result_eligible": False,
    }
    _write_once(manifest_path, manifest)
    return manifest


def run_formal(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_approval(approval, root=root)
    if (root / DERIVED_RELATIVE).exists():
        raise StressGateLocked("stress Derived output is already complete")
    manifest = _ensure_generation(root, approval)
    raw_root = root / RAW_RELATIVE
    commit = _head(root)
    candidates = {item.program_id: item for item in stress_candidates()}
    all_rows: list[Mapping[str, Any]] = []
    axis_results: dict[str, Mapping[str, Any]] = {}

    for axis, planned_levels in STRESS_LEVELS.items():
        level_summaries: list[Mapping[str, Any]] = []
        stop_level = None
        stop_reasons: list[str] = []
        entries = [item for item in manifest["programs"] if item["axis"] == axis]
        for entry in entries:
            level = int(entry["requested_level"])
            if stop_level is not None:
                continue
            candidate = candidates[str(entry["program_id"])]
            rows = []
            for repetition in range(1, REPETITIONS_PER_EXECUTED_LEVEL + 1):
                record_path = _record_path(raw_root, candidate, repetition)
                if record_path.exists():
                    row = _load(record_path)
                    _validate_existing_record(
                        row, candidate=candidate, repetition=repetition, commit=commit
                    )
                else:
                    base = (
                        Path("runs") / candidate.axis / candidate.program_id
                        / f"{candidate.program_id}-R{repetition:02d}"
                    )
                    result = dict(execute_method(
                        "bisafecode_full",
                        candidate.source,
                        program_id=candidate.program_id,
                        stdout_path=raw_root / base.with_suffix(".stdout.log"),
                        stderr_path=raw_root / base.with_suffix(".stderr.log"),
                        repository_root=root,
                        budget=COMMON_BUDGET,
                    ))
                    artifact = result.pop("artifact")
                    row = {
                        "schema": "bisafecode.stage4.rq3-stress.run-record/v1",
                        "evidence_status": RAW_STATUS,
                        "experiment_id": EXPERIMENT_ID,
                        "formal_commit": commit,
                        "program_id": candidate.program_id,
                        "source_sha256": candidate.source_sha256,
                        "axis": axis,
                        "requested_level": level,
                        "repetition_index": repetition,
                        "method_id": "bisafecode_full",
                        "status": result["status"],
                        "verdict": result["verdict"],
                        "runtime_ms": result["runtime_ms"],
                        "checker_elapsed_ms": _checker_elapsed_ms(artifact),
                        "peak_rss_bytes": result["peak_memory_bytes"],
                        "explored_states": result["states"],
                        "explored_transitions": result["transitions"],
                        "timeout": result["timeout"],
                        "resource_truncated": result["status"] == "resource_truncated",
                        "reason_codes": result["reason_codes"],
                        "budget": result["budget"],
                        "stdout_path": base.with_suffix(".stdout.log").as_posix(),
                        "stderr_path": base.with_suffix(".stderr.log").as_posix(),
                        "stdout_sha256": result["stdout_sha256"],
                        "stderr_sha256": result["stderr_sha256"],
                        "worker_exit_code": result["worker_exit_code"],
                        "paper_result_eligible": False,
                    }
                    _write_once(record_path, row)
                    artifact_path = record_path.with_name(
                        record_path.name.replace(".record.json", ".artifact.json")
                    )
                    _write_once(artifact_path, artifact)
                rows.append(row)
                all_rows.append(row)
            level_summary = summarize_level(axis, level, rows, level_summaries)
            level_summary_path = (
                raw_root / "level_summaries" / axis / f"level_{level}.json"
            )
            if level_summary_path.exists():
                if _load(level_summary_path) != level_summary:
                    raise StressGateLocked("existing level summary mismatch")
            else:
                _write_once(level_summary_path, level_summary)
            level_summaries.append(level_summary)
            if level_summary["stop_triggered"]:
                stop_level = level
                stop_reasons = list(level_summary["stop_reasons"])
        if stop_level is None:
            stop_level = int(planned_levels[-1])
            stop_reasons = ["MAX_REGISTERED_LEVEL_COMPLETED_WITHOUT_EARLIER_BOUNDARY"]
        axis_results[axis] = {
            "planned_levels": list(planned_levels),
            "executed_levels": [int(item["requested_level"]) for item in level_summaries],
            "skipped_levels_due_to_registered_stop_rule": [
                level for level in planned_levels if level > stop_level
            ],
            "registered_stop_level": stop_level,
            "registered_stop_reasons": stop_reasons,
            "state_log_log_slope_over_executed_levels": _log_log_slope(level_summaries),
            "level_summaries": level_summaries,
        }

    return _derive(root=root, manifest=manifest, rows=all_rows, axis_results=axis_results)


def _log_log_slope(level_summaries: Sequence[Mapping[str, Any]]) -> float | None:
    pairs = [
        (
            math.log(float(item["requested_level"])),
            math.log(float(item["medians"]["explored_states"])),
        )
        for item in level_summaries
        if item["medians"]["explored_states"] not in {None, 0.0}
    ]
    if len(pairs) < 2:
        return None
    xs, ys = zip(*pairs)
    x_mean, y_mean = statistics.mean(xs), statistics.mean(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator == 0:
        return None
    return sum((x - x_mean) * (y - y_mean) for x, y in pairs) / denominator


def _derive(
    *, root: Path, manifest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    axis_results: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    del manifest
    derived_root = root / DERIVED_RELATIVE
    if derived_root.exists():
        raise StressGateLocked("stress Derived root already exists")
    summary = {
        "schema": "bisafecode.stage4.rq3-stress.derived-summary/v1",
        "evidence_status": DERIVED_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "formal_commit": _head(root),
        "created_utc": _utc(),
        "axes": dict(axis_results),
        "structural_caps_not_extended": STRUCTURAL_CAPS,
        "repetitions_per_executed_level": REPETITIONS_PER_EXECUTED_LEVEL,
        "maximum_scheduled_runs": sum(len(levels) for levels in STRESS_LEVELS.values())
        * REPETITIONS_PER_EXECUTED_LEVEL,
        "executed_run_count": len(rows),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
        "verdict_counts": dict(sorted(Counter(
            str(row["verdict"]) for row in rows if row.get("verdict") is not None
        ).items())),
        "common_budget": dict(COMMON_BUDGET),
        "oracle_run_count": 0,
        "new_correctness_evidence_count": 0,
        "interpretation_boundary": (
            "Supplementary implementation stress evidence within the frozen language. "
            "Verifier verdicts are execution diagnostics, not new oracle-backed correctness results."
        ),
        "paper_result_eligible": False,
    }
    _write_once(derived_root / "stress_rows.json", list(rows))
    _write_once(derived_root / "STRESS_DERIVED_PENDING_REVIEW_SUMMARY.json", summary)
    raw_root = root / RAW_RELATIVE
    raw_files = [path for path in raw_root.rglob("*") if path.is_file()]
    derived_files = [path for path in derived_root.rglob("*") if path.is_file()]
    seal = {
        "schema": "bisafecode.stage4.rq3-stress.derived-seal/v1",
        "evidence_status": DERIVED_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "formal_commit": _head(root),
        "raw_file_count": len(raw_files),
        "raw_tree_root_sha256": _tree_root(raw_root, raw_files),
        "derived_file_count_before_seal": len(derived_files),
        "derived_tree_root_sha256_before_seal": _tree_root(derived_root, derived_files),
        "executed_run_count": len(rows),
        "paper_result_eligible": False,
    }
    _write_once(derived_root / "derived.seal.json", seal)
    return summary
