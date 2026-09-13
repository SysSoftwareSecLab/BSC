"""Prospective RQ2-only grasp/authority correlation challenge set.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The two matched families are generated before any RQ2 method output.  They do
not alter the frozen EXP-S4-002 RQ1 population.  The safe family exercises a
single-owner grasp lifecycle.  The cross-check family adds a logical authority
transfer to an arm that has no physical grasp, so a sound joint-state checker
must reject it while a separated grasp/authority checker lacks the join.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Tuple


CHALLENGE_SPLIT = "rq2_unified_state_challenge"
CHALLENGE_FAMILIES: Mapping[str, Mapping[str, str]] = {
    "RQ2_GRASP_AUTH_LOCAL_V1": {
        "intended_oracle_class": "safe",
        "role": "single-owner grasp lifecycle without cross-state transfer",
    },
    "RQ2_GRASP_AUTH_CROSSCHECK_V1": {
        "intended_oracle_class": "unsafe",
        "role": "authority transfer to an arm lacking a physical grasp",
    },
}
CHALLENGE_SCHEDULE: Tuple[Tuple[str, int], ...] = tuple(
    ("RQ2_GRASP_AUTH_LOCAL_V1", seed) for seed in range(71_001, 71_011)
) + tuple(
    ("RQ2_GRASP_AUTH_CROSSCHECK_V1", seed) for seed in range(72_001, 72_011)
)


@dataclass(frozen=True)
class RQ2ChallengeCandidate:
    family_id: str
    seed: int
    source: str
    source_sha256: str
    split: str = CHALLENGE_SPLIT


def _parameters(seed: int) -> tuple[str, str, int, int, int]:
    slot = (seed - 1) % 10
    arm = ("left", "right")[slot % 2]
    object_id = ("payload_alpha", "payload_beta")[(slot // 2) % 2]
    durations = (20_000_000, 40_000_000, 80_000_000)
    duration_a = durations[(slot // 4) % 3]
    duration_b = durations[(slot // 7) % 3]
    loop_bound = (2, 3, 4)[slot % 3]
    return arm, object_id, duration_a, duration_b, loop_bound


def render_challenge_candidate(
    family_id: str, seed: int, *, formal: bool = False
) -> RQ2ChallengeCandidate:
    if family_id not in CHALLENGE_FAMILIES:
        raise ValueError("unknown RQ2 challenge family")
    if (family_id, seed) not in CHALLENGE_SCHEDULE:
        raise ValueError("candidate is outside the frozen RQ2 challenge schedule")
    arm, object_id, duration_a, duration_b, loop_bound = _parameters(seed)
    other = "right" if arm == "left" else "left"
    transfer = ""
    if family_id == "RQ2_GRASP_AUTH_CROSSCHECK_V1":
        transfer = (
            f'    transfer_authority("{object_id}", "{arm}", "{other}")\n'
        )
    marker = (
        "LOCKED_RAW_PENDING_BLIND_EVALUATION"
        if formal
        else "PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE"
    )
    source = (
        f"# {marker}\n"
        "def task():\n"
        f'    close("{arm}", "{object_id}", {duration_a})\n'
        f"    for cycle in range({loop_bound}):\n"
        f'        wait("{arm}", {duration_b})\n'
        + transfer
        + f'    open("{arm}", "{object_id}", {duration_a})\n'
    )
    return RQ2ChallengeCandidate(
        family_id=family_id,
        seed=seed,
        source=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def challenge_program_ids() -> Tuple[str, ...]:
    return tuple(f"EXP-S4-004-U{index:03d}" for index in range(1, 21))
