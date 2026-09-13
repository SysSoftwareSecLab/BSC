"""Non-executing phase-isolation guards for future Stage 4 experiments.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

EXP-S4-001 has no formal execution path.  EXP-S4-002/003 runners and generator
versions remain unavailable until a separate Mac authorization.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from . import PREP_STATUS


APPROVED_EXPERIMENT_IDS = {
    "EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS",
    "EXP-S4-003_NATURAL_LLM_UNSEEN_PROGRAM_CORRECTNESS",
}


class FormalRunLocked(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_future_approval(
    approval: Mapping[str, Any],
    *,
    root: Path,
    required_bindings: Mapping[str, str],
) -> None:
    """Validate only a future experiment approval; never authorize EXP-S4-001."""

    if approval.get("experiment_id") not in APPROVED_EXPERIMENT_IDS:
        raise FormalRunLocked("EXP-S4-001 is prepare-only")
    if approval.get("evidence_status") != PREP_STATUS:
        raise FormalRunLocked("approval does not bind the prep review marker")
    if approval.get("status") != "APPROVED_FOR_FORMAL_EXECUTION":
        raise FormalRunLocked("Mac formal approval is absent")
    if approval.get("formal_generator_status") != "MAC_APPROVED_AND_HASH_FROZEN":
        raise FormalRunLocked("formal generator/model invocation is not frozen")
    for relative_path, expected in required_bindings.items():
        path = root / relative_path
        if not path.is_file() or sha256_file(path) != expected:
            raise FormalRunLocked("required identity mismatch: {}".format(relative_path))
        if approval.get("sha256_bindings", {}).get(relative_path) != expected:
            raise FormalRunLocked("approval hash mismatch: {}".format(relative_path))


def ensure_phase_isolation(
    *,
    phase: str,
    verifier_directory: Path,
    oracle_directory: Path,
) -> None:
    if phase == "verifier":
        if oracle_directory.exists() and any(oracle_directory.iterdir()):
            raise FormalRunLocked(
                "oracle labels already exist; verifier blindness cannot be guaranteed"
            )
    elif phase == "oracle":
        if not verifier_directory.is_dir():
            raise FormalRunLocked(
                "verifier outputs must be frozen before oracle labels are materialized"
            )
    else:
        raise ValueError("phase must be verifier or oracle")


def run_external_phase(*args, **kwargs):
    """Remain unavailable until EXP-S4-002/003 implementation review."""

    del args, kwargs
    raise FormalRunLocked(
        "formal runner implementation unavailable_pending_mac_approval"
    )
