"""Hash-bindable run provenance and evidence lifecycle for EXP-S4-002.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import (
    DERIVED_STATUS,
    EXPERIMENT_ID,
    FORMAL_RAW_STATUS,
    FREEZE_STATUS,
    LOCKED_RAW_STATUS,
    SPLIT,
)
from .identity import (
    compute_asset_identity,
    compute_contract_identity,
    compute_toolchain_identity,
)


LIFECYCLE_STATUS_BY_PHASE = {
    "freeze": FREEZE_STATUS,
    "generation": LOCKED_RAW_STATUS,
    "bisafecode_full": FORMAL_RAW_STATUS,
    "non_timed_action_boundary": FORMAL_RAW_STATUS,
    "random_dynamic_testing": FORMAL_RAW_STATUS,
    "llm_as_judge": FORMAL_RAW_STATUS,
    "oracle": FORMAL_RAW_STATUS,
    "derived": DERIVED_STATUS,
}
LIFECYCLE_ORDER = (
    FREEZE_STATUS,
    LOCKED_RAW_STATUS,
    FORMAL_RAW_STATUS,
    DERIVED_STATUS,
)
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def evidence_status_for_phase(phase: str) -> str:
    try:
        return LIFECYCLE_STATUS_BY_PHASE[phase]
    except KeyError as error:
        raise ValueError("unknown evidence lifecycle phase") from error


def validate_lifecycle_transition(source_status: str, target_status: str) -> bool:
    try:
        return LIFECYCLE_ORDER.index(target_status) == LIFECYCLE_ORDER.index(source_status) + 1
    except ValueError:
        return False


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def machine_environment_identity() -> Mapping[str, Any]:
    machine = {
        "hostname_sha256": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
    }
    environment = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_build": list(platform.python_build()),
        "byte_order": sys.byteorder,
        "worker_protocol": "bisafecode.stage4.controlled.isolated-worker/v1",
    }
    return {
        "machine": machine,
        "environment": environment,
        "machine_environment_sha256": _canonical_sha256(
            {"machine": machine, "environment": environment}
        ),
    }


def build_run_manifest(
    *,
    repository_root: Path,
    approval: Mapping[str, Any],
    commit: str,
    program_id: str,
    scene_id: str,
    method_id: str,
    seed: int | None,
    status: str,
    verdict: str | None,
    reason_codes: Sequence[str],
    oracle_status: str,
    adjudication_status: str,
    timestamp_utc: str | None = None,
) -> Mapping[str, Any]:
    identity = machine_environment_identity()
    timestamp = timestamp_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    config = {
        "asset_root_sha256": compute_asset_identity(repository_root)["root_sha256"],
        "contract_root_sha256": compute_contract_identity(repository_root)["root_sha256"],
        "toolchain_root_sha256": compute_toolchain_identity(repository_root)["root_sha256"],
    }
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "run_id": approval.get("formal_run_id"),
        "timestamp_utc": timestamp,
        "operator_id": approval.get("operator_id"),
        "program_id": program_id,
        "scene_id": scene_id,
        "split": SPLIT,
        "method_id": method_id,
        "commit": commit,
        "config": config,
        "seed": seed,
        **identity,
        "oracle": {
            "status": oracle_status,
            "adjudication": adjudication_status,
            "verifier_outputs_visible": False,
        },
        "verdict": verdict,
        "status": status,
        "reason_codes": list(reason_codes),
    }
    return {
        "schema": "bisafecode.stage4.controlled.run-manifest/v1",
        **payload,
        "root_sha256": _canonical_sha256(payload),
    }


def validate_run_manifest(manifest: Mapping[str, Any]) -> list[str]:
    errors = []
    required = {
        "experiment_id",
        "run_id",
        "timestamp_utc",
        "operator_id",
        "program_id",
        "scene_id",
        "split",
        "method_id",
        "commit",
        "config",
        "seed",
        "machine",
        "environment",
        "machine_environment_sha256",
        "oracle",
        "verdict",
        "status",
        "reason_codes",
        "root_sha256",
    }
    if manifest.get("schema") != "bisafecode.stage4.controlled.run-manifest/v1":
        errors.append("schema")
    errors.extend(f"missing:{key}" for key in sorted(required - set(manifest)))
    if manifest.get("experiment_id") != EXPERIMENT_ID or manifest.get("split") != SPLIT:
        errors.append("experiment_or_split")
    if not isinstance(manifest.get("run_id"), str) or not manifest.get("run_id"):
        errors.append("run_id")
    if not isinstance(manifest.get("operator_id"), str) or not manifest.get("operator_id"):
        errors.append("operator_id")
    if not isinstance(manifest.get("timestamp_utc"), str) or not UTC_RE.fullmatch(manifest.get("timestamp_utc", "")):
        errors.append("timestamp_utc")
    if not isinstance(manifest.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", "")):
        errors.append("commit")
    config = manifest.get("config")
    if not isinstance(config, dict) or set(config) != {
        "asset_root_sha256", "contract_root_sha256", "toolchain_root_sha256"
    } or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in (config or {}).values()):
        errors.append("config")
    for key in ("machine", "environment", "oracle"):
        if not isinstance(manifest.get(key), dict) or not manifest[key]:
            errors.append(key)
    payload = {
        key: manifest.get(key)
        for key in manifest
        if key not in {"schema", "root_sha256"}
    }
    if manifest.get("root_sha256") != _canonical_sha256(payload):
        errors.append("root_sha256")
    return sorted(set(errors))
