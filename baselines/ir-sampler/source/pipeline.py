"""Approval-gated create-once execution for EXP-S4-009.

The method phase consumes only opaque source records.  The clean-room oracle
then parses the sealed sources without reading method outputs.  Derived
statistics are the first phase allowed to join those two sealed views.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from bisafecode.stage4_baseline_replication.independent_random_v4 import (
    run_independent_random_dynamic_v4,
)
from bisafecode.stage4_controlled.methods import run_full_method

from . import (
    ASSET_RELATIVE,
    BUDGET_PREFIXES,
    CONTRACT_RELATIVE,
    DERIVED_RELATIVE,
    DERIVED_STATUS,
    EXPERIMENT_ID,
    METHOD_FULL,
    METHOD_RANDOM,
    RAW_RELATIVE,
    RAW_STATUS,
)
from .generator import build_challenge_cases
from .oracle import evaluate_exact_schedule_oracle, expected_detection_probability


METHOD_IDS = (METHOD_FULL, METHOD_RANDOM)
PROTOCOL_FILE = "PROTOCOL.json"


class RareScheduleGateLocked(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _head(root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        capture_output=True, check=False,
    )
    if process.returncode != 0:
        raise RareScheduleGateLocked("cannot resolve exact freeze commit")
    return process.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise RareScheduleGateLocked(f"create-once output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _write_text_once(path: Path, value: str) -> None:
    if path.exists():
        raise RareScheduleGateLocked(f"create-once output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def approval_template(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    return {
        "schema": "bisafecode.stage4.rare-schedule.approval/v1",
        "evidence_status": "LOCAL_EXECUTION_AUTHORIZATION_NOT_PAPER_EVIDENCE",
        "experiment_id": EXPERIMENT_ID,
        "approved": True,
        "reviewer": "Anonymous User",
        "freeze_commit": _head(root),
        "protocol_sha256": _sha256(root / CONTRACT_RELATIVE / PROTOCOL_FILE),
        "authorized_phases": ["generation", "methods", "oracle", "derived"],
        "random_rollouts_per_program": 100,
        "paper_export_authorized": False,
        "status": "AUTHORIZED",
    }


def validate_approval(approval: Mapping[str, Any], *, root: Path) -> None:
    if dict(approval) != dict(approval_template(root)):
        raise RareScheduleGateLocked(
            "approval must exactly bind HEAD, protocol, phases, and the 100-rollout budget"
        )


def freeze_check(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    cases = build_challenge_cases()
    raw = root / RAW_RELATIVE
    derived = root / DERIVED_RELATIVE
    return {
        "schema": "bisafecode.stage4.rare-schedule.freeze-check/v1",
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "protocol_sha256": _sha256(root / CONTRACT_RELATIVE / PROTOCOL_FILE),
        "case_count": len(cases),
        "unique_source_count": len({case.source_sha256 for case in cases}),
        "unsafe_count": sum(case.role == "unsafe_challenge" for case in cases),
        "safe_control_count": sum(case.role == "safe_control" for case in cases),
        "formal_raw_absent": not raw.exists(),
        "formal_derived_absent": not derived.exists(),
        "ready": len(cases) == 40 and not raw.exists() and not derived.exists(),
        "formal_runs_this_check": 0,
        "paper_result_eligible": False,
    }


def generate_create_once(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_approval(approval, root=root)
    raw = root / RAW_RELATIVE
    if raw.exists():
        raise RareScheduleGateLocked("formal generation root already exists")
    cases = build_challenge_cases()
    method_records = []
    design_records = []
    for case in cases:
        relative = Path("generation/sealed_sources") / f"{case.opaque_case_id}.py"
        _write_text_once(raw / relative, case.source)
        method_record = dict(case.method_record())
        method_record["source_path"] = relative.as_posix()
        method_records.append(method_record)
        design_record = dict(case.design_record())
        design_record["source_path"] = relative.as_posix()
        design_records.append(design_record)
    binding = {
        "schema": "bisafecode.stage4.rare-schedule.input-binding/v1",
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "protocol_sha256": approval["protocol_sha256"],
        "created_utc": _utc(),
        "case_count": 40,
        "paper_result_eligible": False,
    }
    method_manifest = {
        **binding,
        "schema": "bisafecode.stage4.rare-schedule.method-input-manifest/v1",
        "records": method_records,
        "role_or_stratum_fields_present": False,
        "oracle_fields_present": False,
    }
    design_manifest = {
        **binding,
        "schema": "bisafecode.stage4.rare-schedule.private-design-manifest/v1",
        "records": design_records,
        "method_phase_may_read_this_file": False,
    }
    _write_once(raw / "input_binding.json", binding)
    _write_once(raw / "generation/method_input_manifest.json", method_manifest)
    _write_once(raw / "generation/private_design_manifest.json", design_manifest)
    return {
        "phase": "generation",
        "created": 40,
        "freeze_commit": binding["freeze_commit"],
        "methods_run": 0,
        "oracle_run": 0,
        "paper_result_eligible": False,
    }


def _method_manifest(root: Path) -> Mapping[str, Any]:
    path = root / RAW_RELATIVE / "generation/method_input_manifest.json"
    if not path.is_file():
        raise RareScheduleGateLocked("formal method-input manifest is missing")
    manifest = _load(path)
    if manifest.get("role_or_stratum_fields_present") is not False:
        raise RareScheduleGateLocked("method manifest is not label blind")
    records = list(manifest.get("records", []))
    identities = {str(item.get("opaque_case_id")) for item in records}
    if len(records) != 40 or len(identities) != 40:
        raise RareScheduleGateLocked("method manifest must bind forty unique cases")
    return manifest


def _validate_existing_method_record(
    path: Path, *, method_id: str, case_id: str, source_sha: str, commit: str
) -> None:
    value = _load(path)
    required = {
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "method_id": method_id,
        "opaque_case_id": case_id,
        "source_sha256": source_sha,
        "paper_result_eligible": False,
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise RareScheduleGateLocked(f"existing method record binding mismatch: {path}")


def run_methods(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_approval(approval, root=root)
    manifest = _method_manifest(root)
    methods_root = root / RAW_RELATIVE / "methods"
    seal_path = root / RAW_RELATIVE / "seals/methods.json"
    if seal_path.exists():
        raise RareScheduleGateLocked("formal methods phase is already sealed")
    commit = _head(root)
    counts = {method_id: 0 for method_id in METHOD_IDS}
    for record in manifest["records"]:
        case_id = str(record["opaque_case_id"])
        source_sha = str(record["source_sha256"])
        source = (root / RAW_RELATIVE / str(record["source_path"])).read_text(
            encoding="utf-8"
        )
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != source_sha:
            raise RareScheduleGateLocked("sealed source identity mismatch")

        full_path = methods_root / METHOD_FULL / f"{case_id}.json"
        if full_path.exists():
            _validate_existing_method_record(
                full_path, method_id=METHOD_FULL, case_id=case_id,
                source_sha=source_sha, commit=commit,
            )
        else:
            full = dict(run_full_method(source, program_id=case_id))
            _write_once(full_path, {
                "schema": "bisafecode.stage4.rare-schedule.method-result/v1",
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "freeze_commit": commit,
                "method_id": METHOD_FULL,
                "opaque_case_id": case_id,
                "source_sha256": source_sha,
                "prediction": (
                    "unsafe" if full["verdict"] == "violated" else
                    "safe" if full["verdict"] == "verified-within-bounds" else "unknown"
                ),
                "result": full,
                "design_or_oracle_fields_visible": False,
                "paper_result_eligible": False,
            })
        counts[METHOD_FULL] += 1

        random_path = methods_root / METHOD_RANDOM / f"{case_id}.json"
        if random_path.exists():
            _validate_existing_method_record(
                random_path, method_id=METHOD_RANDOM, case_id=case_id,
                source_sha=source_sha, commit=commit,
            )
        else:
            random = dict(run_independent_random_dynamic_v4(
                source,
                source_sha256=source_sha,
                asset_root=root / ASSET_RELATIVE,
                rollouts=100,
            ))
            _write_once(random_path, {
                "schema": "bisafecode.stage4.rare-schedule.method-result/v1",
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "freeze_commit": commit,
                "method_id": METHOD_RANDOM,
                "opaque_case_id": case_id,
                "source_sha256": source_sha,
                "prediction": random["predicted_class"],
                "result": random,
                "design_or_oracle_fields_visible": False,
                "rollouts_are_one_create_once_sequence": True,
                "paper_result_eligible": False,
            })
        counts[METHOD_RANDOM] += 1
    seal = {
        "schema": "bisafecode.stage4.rare-schedule.methods-seal/v1",
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "record_counts": counts,
        "random_rollouts_per_program": 100,
        "oracle_outputs_visible": False,
        "paper_result_eligible": False,
    }
    _write_once(seal_path, seal)
    return seal


def run_oracle(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_approval(approval, root=root)
    manifest = _method_manifest(root)
    oracle_root = root / RAW_RELATIVE / "oracle"
    seal_path = root / RAW_RELATIVE / "seals/oracle.json"
    if seal_path.exists():
        raise RareScheduleGateLocked("formal oracle phase is already sealed")
    commit = _head(root)
    counts = {"safe": 0, "unsafe": 0}
    for record in manifest["records"]:
        case_id = str(record["opaque_case_id"])
        source = (root / RAW_RELATIVE / str(record["source_path"])).read_text(
            encoding="utf-8"
        )
        oracle_path = oracle_root / f"{case_id}.json"
        if oracle_path.exists():
            result = _load(oracle_path)
            required = {
                "experiment_id": EXPERIMENT_ID,
                "freeze_commit": commit,
                "opaque_case_id": case_id,
                "source_sha256": str(record["source_sha256"]),
                "method_outputs_visible": False,
                "paper_result_eligible": False,
            }
            if any(result.get(key) != value for key, value in required.items()):
                raise RareScheduleGateLocked(
                    f"existing oracle record binding mismatch: {oracle_path}"
                )
        else:
            result = dict(evaluate_exact_schedule_oracle(source))
            result.update({
                "evidence_status": RAW_STATUS,
                "experiment_id": EXPERIMENT_ID,
                "freeze_commit": commit,
                "opaque_case_id": case_id,
                "source_sha256": str(record["source_sha256"]),
                "method_outputs_visible": False,
                "paper_result_eligible": False,
            })
            _write_once(oracle_path, result)
        counts[result["verdict"]] += 1
    seal = {
        "schema": "bisafecode.stage4.rare-schedule.oracle-seal/v1",
        "evidence_status": RAW_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": commit,
        "record_count": sum(counts.values()),
        "verdict_counts": counts,
        "method_outputs_visible": False,
        "paper_result_eligible": False,
    }
    _write_once(seal_path, seal)
    return seal


def _prefix_prediction(random_result: Mapping[str, Any], budget: int) -> str:
    records = list(random_result["rollouts"][:budget])
    outcomes = [str(item["outcome"]) for item in records]
    if "violation" in outcomes:
        return "unsafe"
    if len(records) == budget and all(item == "safe-completion" for item in outcomes):
        return "safe"
    return "unknown"


def derive(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    root = root.resolve()
    validate_approval(approval, root=root)
    derived_root = root / DERIVED_RELATIVE
    summary_path = derived_root / "DERIVED_PENDING_REVIEW_SUMMARY.json"
    if summary_path.exists():
        raise RareScheduleGateLocked("formal Derived phase is already complete")
    design = _load(root / RAW_RELATIVE / "generation/private_design_manifest.json")
    if not (root / RAW_RELATIVE / "seals/methods.json").is_file():
        raise RareScheduleGateLocked("methods are not sealed")
    if not (root / RAW_RELATIVE / "seals/oracle.json").is_file():
        raise RareScheduleGateLocked("oracle is not sealed")

    joined = []
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for design_record in design["records"]:
        case_id = str(design_record["opaque_case_id"])
        oracle = _load(root / RAW_RELATIVE / "oracle" / f"{case_id}.json")
        full = _load(root / RAW_RELATIVE / "methods" / METHOD_FULL / f"{case_id}.json")
        random = _load(root / RAW_RELATIVE / "methods" / METHOD_RANDOM / f"{case_id}.json")
        if len({oracle["source_sha256"], full["source_sha256"], random["source_sha256"]}) != 1:
            raise RareScheduleGateLocked("source identity mismatch during Raw-to-Derived join")
        prefix_predictions = {
            str(budget): _prefix_prediction(random["result"], budget)
            for budget in BUDGET_PREFIXES
        }
        first_violation = next((
            index + 1 for index, item in enumerate(random["result"]["rollouts"])
            if item["outcome"] == "violation"
        ), None)
        item = {
            "opaque_case_id": case_id,
            "source_sha256": oracle["source_sha256"],
            "role": design_record["role"],
            "stratum": design_record["stratum"],
            "oracle_verdict": oracle["verdict"],
            "exact_exposure_probability": oracle["exact_exposure_probability"],
            "bisafecode_prediction": full["prediction"],
            "bisafecode_correct": full["prediction"] == oracle["verdict"],
            "random_prefix_predictions": prefix_predictions,
            "random_first_violation_rollout": first_violation,
            "random_violation_rollouts_at_100": random["result"]["violation_rollouts"],
        }
        joined.append(item)
        grouped[str(design_record["stratum"])].append(item)

    strata = {}
    for stratum, items in sorted(grouped.items()):
        exposure = str(items[0]["exact_exposure_probability"])
        strata[stratum] = {
            "n": len(items),
            "oracle_verdict_counts": {
                verdict: sum(item["oracle_verdict"] == verdict for item in items)
                for verdict in ("safe", "unsafe")
            },
            "exact_exposure_probability": exposure,
            "bisafecode_correct": sum(bool(item["bisafecode_correct"]) for item in items),
            "random_detected_unsafe_by_budget": {
                str(budget): sum(
                    item["random_prefix_predictions"][str(budget)] == "unsafe"
                    for item in items
                )
                for budget in BUDGET_PREFIXES
            },
            "ideal_independent_random_detection_probability_by_budget": {
                str(budget): float(expected_detection_probability(exposure, budget))
                for budget in BUDGET_PREFIXES
            },
        }
    summary = {
        "schema": "bisafecode.stage4.rare-schedule.derived-summary/v1",
        "evidence_status": DERIVED_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "freeze_commit": _head(root),
        "created_utc": _utc(),
        "population": {
            "total": len(joined),
            "safe": sum(item["oracle_verdict"] == "safe" for item in joined),
            "unsafe": sum(item["oracle_verdict"] == "unsafe" for item in joined),
        },
        "overall": {
            "bisafecode_correct": sum(bool(item["bisafecode_correct"]) for item in joined),
            "random_correct_at_100": sum(
                item["random_prefix_predictions"]["100"] == item["oracle_verdict"]
                for item in joined
            ),
        },
        "strata": strata,
        "interpretation_boundary": (
            "Mechanistic challenge evidence only: it does not estimate natural-program "
            "prevalence or prove unbounded scalability. A random 'safe' result means no "
            "violation observed within the frozen prefix budget."
        ),
        "history_overwritten": False,
        "paper_result_eligible": False,
    }
    per_case_path = derived_root / "PER_CASE_RESULTS.json"
    if per_case_path.exists():
        if _load(per_case_path) != joined:
            raise RareScheduleGateLocked("existing partial Derived per-case join differs")
    else:
        _write_once(per_case_path, joined)
    _write_once(summary_path, summary)
    return summary


def run_all(*, root: Path, approval: Mapping[str, Any]) -> Mapping[str, Any]:
    """Run the four authorized phases once, in their frozen order."""

    return {
        "generation": generate_create_once(root=root, approval=approval),
        "methods": run_methods(root=root, approval=approval),
        "oracle": run_oracle(root=root, approval=approval),
        "derived": derive(root=root, approval=approval),
    }
