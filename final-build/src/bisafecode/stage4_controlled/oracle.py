"""Program-level independent logical+geometry oracle composition.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from . import FORMAL_RAW_STATUS, FREEZE_STATUS
from .geometry_oracle import adjudicate_interval_evidence, produce_geometry_evidence
from .identity import expected_geometry_oracle_identities, expected_logical_oracle_identities
from .logical_oracle import evaluate_program_logical_oracle
from .isolation import run_isolated_job


def combine_oracle_components(
    logical: Mapping[str, Any], geometry: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    """Frozen fail-closed combination rule over all finite-input traces."""

    logical_verdict = logical.get("verdict")
    geometry_verdicts = [item.get("verdict") for item in geometry]
    if logical_verdict == "invalid" or "invalid" in geometry_verdicts:
        verdict = "invalid"
    elif logical_verdict == "violated" or "violated" in geometry_verdicts:
        verdict = "unsafe"
    elif logical_verdict != "verified-within-bounds" or any(
        not (
            item.get("verdict") == "verified-within-bounds"
            or (
                item.get("verdict") == "unknown"
                and item.get("reason_codes") == ["NO_GEOMETRY_RELEVANT_MOVE_INTERVALS"]
            )
        )
        for item in geometry
    ):
        verdict = "unknown"
    elif len(geometry) != 4:
        verdict = "unknown"
    else:
        verdict = "safe"
    reasons = list(logical.get("reason_codes", ()))
    for item in geometry:
        reasons.extend(item.get("reason_codes", ()))
    return {
        "evidence_status": FREEZE_STATUS,
        "schema": "bisafecode.stage4.controlled.combined-oracle/v1",
        "verdict": verdict,
        "reason_codes": sorted(set(reasons)),
        "logical_verdict": logical_verdict,
        "geometry_verdicts": geometry_verdicts,
        "finite_input_traces_required": 4,
        "finite_input_traces_observed": len(geometry),
    }


def evaluate_program_oracle(
    source: str,
    *,
    program_id: str,
    trajectory_durations_ns: Mapping[str, int],
    traces: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Run the independent oracle after the method seals exist and are hidden."""

    geometry_evidence = [produce_geometry_evidence(trace) for trace in traces]
    geometry_results = [
        adjudicate_interval_evidence(
            evidence,
            trace=trace,
            identities=expected_geometry_oracle_identities(),
        )
        for trace, evidence in zip(traces, geometry_evidence)
    ]
    observations = {}
    for trace, evidence in zip(traces, geometry_evidence):
        key = f"mode={trace['inputs']['mode']};ready={str(trace['inputs']['ready']).lower()}"
        observations[key] = evidence.get("transfer_observations", {})
    logical = evaluate_program_logical_oracle(
        source,
        program_id=program_id,
        trajectory_durations_ns=trajectory_durations_ns,
        identities=expected_logical_oracle_identities(),
        traces=traces,
        transfer_observations_by_input=observations,
    )
    combined = combine_oracle_components(logical, geometry_results)
    return {
        **combined,
        "program_id": program_id,
        "logical": logical,
        "geometry": geometry_results,
        "geometry_production": geometry_evidence,
        "verifier_outputs_visible": False,
    }


def execute_oracle(
    source: str,
    *,
    program_id: str,
    stdout_path: Path,
    stderr_path: Path,
    repository_root: Path,
    budget: Mapping[str, int],
) -> Mapping[str, Any]:
    """Run one independent program oracle in its own hard-limited process."""

    isolated = run_isolated_job(
        "oracle",
        {"source": source, "program_id": program_id},
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        budget=budget,
        repository_root=repository_root,
    )
    outcome = isolated["outcome"] if isinstance(isolated.get("outcome"), dict) else None
    result = outcome.get("result") if outcome and isinstance(outcome.get("result"), dict) else None
    status = isolated["status"]
    verdict = result.get("verdict") if status == "ok" and result else None
    reasons = list(result.get("reason_codes", ())) if verdict is not None else list(isolated["reason_codes"])
    if status != "ok":
        traces: Any = {
            "evidence_status": FORMAL_RAW_STATUS,
            "schema": "bisafecode.stage4.controlled.oracle-traces-failure/v1",
            "status": status,
            "reason_codes": reasons,
            "traces": [],
        }
        evidence: Any = {
            "evidence_status": FORMAL_RAW_STATUS,
            "schema": "bisafecode.stage4.controlled.oracle-evidence-failure/v1",
            "status": status,
            "verdict": None,
            "reason_codes": reasons,
        }
    else:
        traces = outcome["traces"]
        evidence = result
    return {
        "evidence_status": FORMAL_RAW_STATUS,
        "program_id": program_id,
        "status": status,
        "verdict": verdict,
        "runtime_ms": isolated["runtime_ms"],
        "peak_memory_bytes": isolated["peak_memory_bytes"],
        "reason_codes": reasons,
        "timeout": status == "timeout",
        "worker_exit_code": isolated["worker_exit_code"],
        "stdout_sha256": isolated["stdout_sha256"],
        "stderr_sha256": isolated["stderr_sha256"],
        "stdout_size_bytes": isolated["stdout_size_bytes"],
        "stderr_size_bytes": isolated["stderr_size_bytes"],
        "traces": traces,
        "evidence": evidence,
        "verifier_outputs_visible": False,
    }
