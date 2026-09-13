"""Content-addressed phase seals and strict predecessor validation.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import compute_asset_identity, compute_contract_identity, compute_toolchain_identity, sha256_file
from .provenance import evidence_status_for_phase


PHASES = (
    "generation",
    "bisafecode_full",
    "non_timed_action_boundary",
    "random_dynamic_testing",
    "llm_as_judge",
    "oracle",
    "derived",
)


class InvalidPhaseSeal(ValueError):
    pass


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _safe_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise InvalidPhaseSeal("phase seal path is not a safe relative path")
    return candidate.as_posix()


def build_phase_seal(
    *,
    phase: str,
    data_root: Path,
    file_paths: Sequence[str],
    record_count: int,
    program_ids: Sequence[str],
    predecessor_roots: Mapping[str, str],
    repository_root: Path,
) -> Mapping[str, Any]:
    if phase not in PHASES:
        raise InvalidPhaseSeal("unknown phase")
    if record_count <= 0 or record_count != len(program_ids) or len(set(program_ids)) != len(program_ids):
        raise InvalidPhaseSeal("record count/program IDs are incomplete")
    normalized = tuple(sorted(_safe_relative(path) for path in file_paths))
    if not normalized or len(set(normalized)) != len(normalized):
        raise InvalidPhaseSeal("phase seal requires unique nonempty files")
    files = []
    for relative in normalized:
        path = data_root / relative
        if not path.is_file():
            raise InvalidPhaseSeal(f"sealed file missing: {relative}")
        files.append({"path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    for predecessor, value in predecessor_roots.items():
        if predecessor not in PHASES or not isinstance(value, str) or len(value) != 64:
            raise InvalidPhaseSeal("predecessor identity malformed")
    identity = {
        "asset_root_sha256": compute_asset_identity(repository_root)["root_sha256"],
        "contract_root_sha256": compute_contract_identity(repository_root)["root_sha256"],
        "toolchain_root_sha256": compute_toolchain_identity(repository_root)["root_sha256"],
    }
    payload = {
        "phase": phase,
        "record_count": record_count,
        "program_ids": list(program_ids),
        "predecessor_roots": dict(sorted(predecessor_roots.items())),
        "identity": identity,
        "files": files,
    }
    return {
        "evidence_status": evidence_status_for_phase(phase),
        "schema": "bisafecode.stage4.controlled.phase-seal/v3",
        **payload,
        "root_sha256": _canonical_hash(payload),
    }


def write_phase_seal(path: Path, seal: Mapping[str, Any]) -> None:
    if path.exists():
        raise InvalidPhaseSeal("phase seal is create-once")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seal, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_phase_seal(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidPhaseSeal("phase seal missing or malformed") from error
    if not isinstance(value, dict):
        raise InvalidPhaseSeal("phase seal must be an object")
    return value


def validate_phase_seal(
    seal: Mapping[str, Any],
    *,
    expected_phase: str,
    data_root: Path,
    expected_record_count: int,
    expected_program_ids: Sequence[str],
    expected_predecessor_roots: Mapping[str, str],
    repository_root: Path,
    expected_file_paths: Sequence[str] | None = None,
) -> Mapping[str, Any]:
    errors = []
    if seal.get("schema") != "bisafecode.stage4.controlled.phase-seal/v3":
        errors.append("schema")
    if seal.get("evidence_status") != evidence_status_for_phase(expected_phase):
        errors.append("evidence_status")
    if seal.get("phase") != expected_phase:
        errors.append("phase")
    if seal.get("record_count") != expected_record_count:
        errors.append("record_count")
    if seal.get("program_ids") != list(expected_program_ids):
        errors.append("program_ids")
    if seal.get("predecessor_roots") != dict(sorted(expected_predecessor_roots.items())):
        errors.append("predecessor_roots")
    expected_identity = {
        "asset_root_sha256": compute_asset_identity(repository_root)["root_sha256"],
        "contract_root_sha256": compute_contract_identity(repository_root)["root_sha256"],
        "toolchain_root_sha256": compute_toolchain_identity(repository_root)["root_sha256"],
    }
    if seal.get("identity") != expected_identity:
        errors.append("identity")
    files = seal.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files")
        files = []
    observed_paths = []
    for item in files:
        if not isinstance(item, dict):
            errors.append("file_entry")
            continue
        try:
            relative = _safe_relative(item.get("path"))
        except (InvalidPhaseSeal, TypeError):
            errors.append("file_path")
            continue
        observed_paths.append(relative)
        path = data_root / relative
        if not path.is_file():
            errors.append(f"missing:{relative}")
            continue
        if item.get("sha256") != sha256_file(path) or item.get("size_bytes") != path.stat().st_size:
            errors.append(f"content:{relative}")
    if observed_paths != sorted(observed_paths) or len(set(observed_paths)) != len(observed_paths):
        errors.append("file_order_or_duplicate")
    if expected_file_paths is not None:
        expected_paths = sorted(_safe_relative(path) for path in expected_file_paths)
        if observed_paths != expected_paths:
            errors.append("complete_file_inventory")
    payload = {
        "phase": seal.get("phase"),
        "record_count": seal.get("record_count"),
        "program_ids": seal.get("program_ids"),
        "predecessor_roots": seal.get("predecessor_roots"),
        "identity": seal.get("identity"),
        "files": seal.get("files"),
    }
    if seal.get("root_sha256") != _canonical_hash(payload):
        errors.append("root_sha256")
    return {"passed": not errors, "errors": sorted(set(errors)), "root_sha256": seal.get("root_sha256")}


def require_valid_phase_seal(**kwargs: Any) -> Mapping[str, Any]:
    result = validate_phase_seal(**kwargs)
    if not result["passed"]:
        raise InvalidPhaseSeal("invalid phase seal: " + ",".join(result["errors"]))
    return result
