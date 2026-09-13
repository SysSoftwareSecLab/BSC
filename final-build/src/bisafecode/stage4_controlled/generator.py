"""Label-blind formal generator frozen for future EXP-S4-002 generation.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The public formal renderer accepts only a scheduled family and seed.  It has
no label, target class, verifier output, or resampling input.  This module is
not an execution entry point; generation remains locked by ``gate.py`` until
Mac supplies a matching approval record.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from bisafecode.stage4_unseen.canonicalize import (
    CanonicalizationResult,
    canonicalize_source,
)
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns

from . import (
    FORMAL_DATA_STATUS,
    FREEZE_STATUS,
    GENERATOR_VERSION,
    GRAMMAR_VERSION,
    SPLIT,
)
from .schedule import FAMILY_SCHEDULES, is_scheduled
from .identity import compute_asset_identity, compute_toolchain_identity


TRAJECTORIES: Mapping[str, Mapping[str, Any]] = {
    "left_approach": {
        "sha256": "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26",
        "duration_ns": 400_000_000,
    },
    "left_retreat": {
        "sha256": "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6",
        "duration_ns": 400_000_000,
    },
    "right_approach": {
        "sha256": "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609",
        "duration_ns": 400_000_000,
    },
    "right_retreat": {
        "sha256": "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea",
        "duration_ns": 400_000_000,
    },
}

OBJECTS: Tuple[str, ...] = ("payload_alpha", "payload_beta")
RESOURCES: Tuple[str, ...] = ("fixture_alpha", "tool_beta")
DURATIONS_NS: Tuple[int, ...] = (20_000_000, 40_000_000, 80_000_000)
LOOP_BOUNDS: Tuple[int, ...] = (2, 3, 4)

FAMILY_DEFINITIONS: Mapping[str, Mapping[str, str]] = {
    "CTL_HANDOVER_BRANCH_SYNC_V1": {
        "intended_oracle_class": "unsafe",
        "scientific_stratum": "H_grasp_control_attachment",
        "template": "handover_branch_sync",
        "structural_subset": "handover",
    },
    "CTL_HANDOVER_LOOP_SYNC_V1": {
        "intended_oracle_class": "safe",
        "scientific_stratum": "H_grasp_control_attachment",
        "template": "handover_loop_sync",
        "structural_subset": "handover",
    },
    "CTL_RESOURCE_BRANCH_INTERLEAVE_V1": {
        "intended_oracle_class": "safe",
        "scientific_stratum": "R_resource_mutual_exclusion",
        "template": "resource_branch_interleave",
        "structural_subset": "resource",
    },
    "CTL_RESOURCE_PARALLEL_RELEASE_V1": {
        "intended_oracle_class": "unsafe",
        "scientific_stratum": "R_resource_mutual_exclusion",
        "template": "resource_parallel_release",
        "structural_subset": "resource",
    },
    "CTL_COLLISION_GUARDED_MOVE_V1": {
        "intended_oracle_class": "safe",
        "scientific_stratum": "C_collision",
        "template": "collision_guarded_move",
        "structural_subset": "collision",
    },
    "CTL_COLLISION_PARALLEL_MOVE_V1": {
        "intended_oracle_class": "unsafe",
        "scientific_stratum": "C_collision",
        "template": "collision_parallel_move",
        "structural_subset": "collision",
    },
}


class HashRng:
    """Cross-runtime SHA-256 counter RNG; seeds reproduce but do not isolate."""

    def __init__(self, family_id: str, seed: int):
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        self._key = f"{GENERATOR_VERSION}:{GRAMMAR_VERSION}:{family_id}:{seed}".encode(
            "ascii"
        )
        self._counter = 0

    def choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise ValueError("choice domain must be nonempty")
        digest = hashlib.sha256(
            self._key + b":" + str(self._counter).encode("ascii")
        ).digest()
        self._counter += 1
        return values[int.from_bytes(digest, "big") % len(values)]


@dataclass(frozen=True)
class ControlledCandidate:
    evidence_status: str
    grammar_version: str
    generator_version: str
    family_id: str
    seed: int
    split: str
    source: str
    parameters: Mapping[str, Any]
    declared_time_bound_ns: int
    computed_structural_time_bound_ns: int
    canonical: CanonicalizationResult
    generator_sha256: str
    toolchain_identity: Mapping[str, Any]
    asset_identity: Mapping[str, Any]


def generator_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _parameters(family_id: str, seed: int) -> Dict[str, Any]:
    rng = HashRng(family_id, seed)
    # The ten-slot spine is injective in (primary, duration_a, duration_b).
    # It prevents within-family duplicate sources without drawing replacements.
    # Remaining choices stay SHA-256-derived and none encode a target label.
    slot_variant = (seed - 1) % 10
    primary = ("left", "right")[slot_variant % 2]
    return {
        "primary_arm": primary,
        "secondary_arm": "right" if primary == "left" else "left",
        "duration_a_ns": DURATIONS_NS[(slot_variant // 2) % 3],
        "duration_b_ns": DURATIONS_NS[(slot_variant // 6) % 3],
        "duration_c_ns": rng.choice(DURATIONS_NS),
        "loop_bound": rng.choice(LOOP_BOUNDS),
        "object_id": rng.choice(OBJECTS),
        "resource_id": rng.choice(RESOURCES),
        "left_approach": TRAJECTORIES["left_approach"]["sha256"],
        "left_retreat": TRAJECTORIES["left_retreat"]["sha256"],
        "right_approach": TRAJECTORIES["right_approach"]["sha256"],
        "right_retreat": TRAJECTORIES["right_retreat"]["sha256"],
        "barrier_a": "ctl_sync_alpha",
        "barrier_b": "ctl_sync_beta",
    }


def _handover_branch_sync(p: Mapping[str, Any]) -> str:
    return '''def left_lane():
    close("left", "{object_id}", {duration_a_ns})
    barrier("{barrier_a}")
    wait("left", {duration_b_ns})

def right_lane():
    wait("right", {duration_c_ns})
    close("right", "{object_id}", {duration_a_ns})
    barrier("{barrier_a}")
    wait("right", {duration_b_ns})

def task():
    parallel(left_lane, right_lane)
    if ready:
        transfer_authority("{object_id}", "left", "right")
    else:
        transfer_authority("{object_id}", "right", "left")
    open("{primary_arm}", "{object_id}", {duration_c_ns})
'''.format(**p)


def _handover_loop_sync(p: Mapping[str, Any]) -> str:
    return '''def left_lane():
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")
    wait("left", {duration_b_ns})

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")
    wait("right", {duration_c_ns})

def task():
    close("{primary_arm}", "{object_id}", {duration_c_ns})
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_a_ns})
    parallel(left_lane, right_lane)
    open("{primary_arm}", "{object_id}", {duration_b_ns})
'''.format(**p)


def _resource_branch_interleave(p: Mapping[str, Any]) -> str:
    return '''def task():
    if mode == "safe":
        acquire("{primary_arm}", "{resource_id}")
        wait("{primary_arm}", {duration_a_ns})
        release("{primary_arm}", "{resource_id}")
    else:
        wait("{secondary_arm}", {duration_b_ns})
        acquire("{secondary_arm}", "{resource_id}")
        wait("{secondary_arm}", {duration_c_ns})
        release("{secondary_arm}", "{resource_id}")
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_b_ns})
'''.format(**p)


def _resource_parallel_release(p: Mapping[str, Any]) -> str:
    return '''def left_lane():
    acquire("left", "{resource_id}")
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")
    release("left", "{resource_id}")

def right_lane():
    wait("right", {duration_b_ns})
    acquire("right", "{resource_id}")
    barrier("{barrier_a}")
    release("right", "{resource_id}")

def task():
    parallel(left_lane, right_lane)
    if ready:
        wait("{primary_arm}", {duration_c_ns})
    else:
        wait("{secondary_arm}", {duration_a_ns})
'''.format(**p)


def _collision_guarded_move(p: Mapping[str, Any]) -> str:
    return '''def left_lane():
    move("left", "{left_approach}")
    barrier("{barrier_a}")

def right_lane():
    move("right", "{right_approach}")
    barrier("{barrier_a}")

def task():
    parallel(left_lane, right_lane)
    if ready:
        wait("{primary_arm}", {duration_b_ns})
    else:
        wait("{secondary_arm}", {duration_a_ns})
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_c_ns})
'''.format(**p)


def _collision_parallel_move(p: Mapping[str, Any]) -> str:
    return '''def left_lane():
    move("left", "{left_approach}")
    barrier("{barrier_a}")

def right_lane():
    move("right", "{right_approach}")
    barrier("{barrier_a}")

def task():
    close("{primary_arm}", "payload_alpha", {duration_c_ns})
    parallel(left_lane, right_lane)
    open("{primary_arm}", "payload_alpha", {duration_a_ns})
    wait("{primary_arm}", {duration_b_ns})
'''.format(**p)


TEMPLATES: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    "handover_branch_sync": _handover_branch_sync,
    "handover_loop_sync": _handover_loop_sync,
    "resource_branch_interleave": _resource_branch_interleave,
    "resource_parallel_release": _resource_parallel_release,
    "collision_guarded_move": _collision_guarded_move,
    "collision_parallel_move": _collision_parallel_move,
}


def _render(family_id: str, seed: int, *, preview: bool) -> ControlledCandidate:
    if family_id not in FAMILY_DEFINITIONS:
        raise ValueError("unknown controlled family")
    scheduled = is_scheduled(family_id, seed)
    if preview and scheduled:
        raise ValueError("scheduled slots cannot be rendered through preview")
    if not preview and not scheduled:
        raise ValueError("formal renderer accepts only the exact frozen schedule")
    parameters = _parameters(family_id, seed)
    body = TEMPLATES[FAMILY_DEFINITIONS[family_id]["template"]](parameters)
    marker = FREEZE_STATUS if preview else FORMAL_DATA_STATUS
    source = f"# {marker}\n" + body
    trajectory_durations = {
        str(item["sha256"]): int(item["duration_ns"])
        for item in TRAJECTORIES.values()
    }
    computed = source_time_upper_bound_ns(source, trajectory_durations)
    return ControlledCandidate(
        evidence_status=marker,
        grammar_version=GRAMMAR_VERSION,
        generator_version=GENERATOR_VERSION,
        family_id=family_id,
        seed=seed,
        split=SPLIT,
        source=source,
        parameters=parameters,
        declared_time_bound_ns=computed,
        computed_structural_time_bound_ns=computed,
        canonical=canonicalize_source(source),
        generator_sha256=generator_sha256(),
        toolchain_identity=compute_toolchain_identity(),
        asset_identity=compute_asset_identity(),
    )


def render_formal_candidate(family_id: str, seed: int) -> ControlledCandidate:
    """Render one approved schedule slot; callers must first pass ``gate.py``."""

    return _render(family_id, seed, preview=False)


def render_freeze_test_preview(family_id: str, seed: int) -> ControlledCandidate:
    """Render an unscheduled, non-persistable source for targeted unit tests."""

    return _render(family_id, seed, preview=True)


if tuple(FAMILY_DEFINITIONS) != tuple(item.family_id for item in FAMILY_SCHEDULES):
    raise ValueError("generator family order must equal the frozen schedule order")
