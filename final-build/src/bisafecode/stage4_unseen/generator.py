"""Deterministic, label-blind restricted-program generator for EXP-S4-001.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The generator accepts only split, family, and seed.  It has no expected-verdict
or target-class input and does not import a verifier or oracle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from . import GRAMMAR_VERSION, PREP_STATUS
from .canonicalize import CanonicalizationResult, canonicalize_source
from .time_bounds import source_time_upper_bound_ns
from .toolchain import compute_toolchain_identity


TRAJECTORIES: Tuple[str, ...] = (
    "0f" * 32,
    "1e" * 32,
    "2d" * 32,
    "3c" * 32,
)
TRAJECTORY_DURATION_NS = 100_000_000
OBJECTS: Tuple[str, ...] = ("payload_alpha", "payload_beta")
RESOURCES: Tuple[str, ...] = ("fixture_alpha", "tool_beta")
DURATIONS_NS: Tuple[int, ...] = (
    10_000_000,
    20_000_000,
    40_000_000,
    80_000_000,
)


FAMILY_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "DS_BRANCH_WAIT_DIAMOND_V1": {
        "split": "design_set",
        "template": "branch_wait_diamond",
        "structural_axes": ["branch", "sequential", "time"],
    },
    "DS_LOOPED_RESOURCE_V1": {
        "split": "design_set",
        "template": "looped_resource",
        "structural_axes": ["loop", "resource", "time"],
    },
    "DS_SINGLE_BARRIER_PARALLEL_V1": {
        "split": "design_set",
        "template": "single_barrier_parallel",
        "structural_axes": ["parallel", "barrier", "time"],
    },
    "DF_BRANCH_RESOURCE_DIAMOND_V1": {
        "split": "development_fixture",
        "template": "branch_resource_diamond",
        "structural_axes": ["branch", "resource", "loop"],
    },
    "DF_LOOP_OBJECT_GUARD_V1": {
        "split": "development_fixture",
        "template": "loop_object_guard",
        "structural_axes": ["loop", "branch", "object"],
    },
    "DF_PARALLEL_TWO_BARRIER_V1": {
        "split": "development_fixture",
        "template": "parallel_two_barrier",
        "structural_axes": ["parallel", "barrier", "time"],
    },
    "DF_PARALLEL_THEN_RESOURCE_V1": {
        "split": "development_fixture",
        "template": "parallel_then_resource",
        "structural_axes": ["parallel", "barrier", "resource", "branch"],
    },
}


class HashRng:
    """Small SHA-256 counter RNG with cross-runtime deterministic choices."""

    def __init__(self, seed: int, namespace: str):
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        self._key = "{}:{}".format(namespace, seed).encode("ascii")
        self._counter = 0

    def _word(self) -> int:
        payload = self._key + b":" + str(self._counter).encode("ascii")
        self._counter += 1
        return int.from_bytes(hashlib.sha256(payload).digest(), "big")

    def randbelow(self, upper: int) -> int:
        if upper <= 0:
            raise ValueError("upper must be positive")
        return self._word() % upper

    def choice(self, values: Sequence[Any]) -> Any:
        return values[self.randbelow(len(values))]


@dataclass(frozen=True)
class GeneratedCandidate:
    evidence_status: str
    grammar_version: str
    family_id: str
    split: str
    seed: int
    source: str
    parameters: Mapping[str, Any]
    declared_time_bound_ns: int
    computed_structural_time_bound_ns: int
    canonical: CanonicalizationResult
    generator_sha256: str
    toolchain_identity: Mapping[str, Any]


def generator_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _header() -> str:
    return "# {}\n".format(PREP_STATUS)


def _arms(rng: HashRng) -> Tuple[str, str]:
    first = rng.choice(("left", "right"))
    return (first, "right" if first == "left" else "left")


def _parameters(rng: HashRng) -> Dict[str, Any]:
    primary, secondary = _arms(rng)
    return {
        "primary_arm": primary,
        "secondary_arm": secondary,
        "duration_a_ns": rng.choice(DURATIONS_NS),
        "duration_b_ns": rng.choice(DURATIONS_NS),
        "duration_c_ns": rng.choice(DURATIONS_NS),
        "loop_bound": rng.choice((2, 3, 4)),
        "object_id": rng.choice(OBJECTS),
        "resource_id": rng.choice(RESOURCES),
        "trajectory_primary": rng.choice(TRAJECTORIES[:2]),
        "trajectory_secondary": rng.choice(TRAJECTORIES[2:]),
        "barrier_a": "sync_alpha",
        "barrier_b": "sync_beta",
    }


def _branch_wait_diamond(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def task():
    if ready:
        wait("{primary_arm}", {duration_a_ns})
        wait("{primary_arm}", {duration_b_ns})
    else:
        wait("{secondary_arm}", {duration_b_ns})
        wait("{secondary_arm}", {duration_a_ns})
'''.format(**p)
    return source, p["duration_a_ns"] + p["duration_b_ns"]


def _looped_resource(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def task():
    acquire("{primary_arm}", "{resource_id}")
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_a_ns})
    release("{primary_arm}", "{resource_id}")
'''.format(**p)
    return source, p["loop_bound"] * p["duration_a_ns"]


def _single_barrier_parallel(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def lane_left():
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")

def lane_right():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")

def task():
    parallel(lane_left, lane_right)
'''.format(**p)
    return source, max(p["duration_a_ns"], p["duration_b_ns"])


def _branch_resource_diamond(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def task():
    if ready:
        acquire("{primary_arm}", "{resource_id}")
        wait("{primary_arm}", {duration_a_ns})
        release("{primary_arm}", "{resource_id}")
    else:
        acquire("{secondary_arm}", "{resource_id}")
        wait("{secondary_arm}", {duration_b_ns})
        release("{secondary_arm}", "{resource_id}")
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_c_ns})
'''.format(**p)
    return source, max(p["duration_a_ns"], p["duration_b_ns"]) + p["loop_bound"] * p["duration_c_ns"]


def _loop_object_guard(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def task():
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_a_ns})
    if mode == "safe":
        close("{primary_arm}", "{object_id}", {duration_b_ns})
        wait("{primary_arm}", {duration_c_ns})
    else:
        wait("{primary_arm}", {duration_c_ns})
        close("{primary_arm}", "{object_id}", {duration_b_ns})
    open("{primary_arm}", "{object_id}", {duration_a_ns})
'''.format(**p)
    bound = p["loop_bound"] * p["duration_a_ns"] + p["duration_b_ns"] + p["duration_c_ns"] + p["duration_a_ns"]
    return source, bound


def _parallel_two_barrier(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def left_lane():
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")
    wait("left", {duration_c_ns})
    barrier("{barrier_b}")

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")
    wait("right", {duration_a_ns})
    barrier("{barrier_b}")

def task():
    parallel(left_lane, right_lane)
'''.format(**p)
    return source, (
        max(p["duration_a_ns"], p["duration_b_ns"])
        + max(p["duration_c_ns"], p["duration_a_ns"])
    )


def _parallel_then_resource(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def left_lane():
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")
    wait("left", {duration_b_ns})

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")
    wait("right", {duration_c_ns})

def task():
    parallel(left_lane, right_lane)
    if ready:
        acquire("{primary_arm}", "{resource_id}")
        wait("{primary_arm}", {duration_c_ns})
        release("{primary_arm}", "{resource_id}")
    else:
        wait("{secondary_arm}", {duration_a_ns})
'''.format(**p)
    parallel_bound = (
        max(p["duration_a_ns"], p["duration_b_ns"])
        + max(p["duration_b_ns"], p["duration_c_ns"])
    )
    return source, parallel_bound + max(p["duration_c_ns"], p["duration_a_ns"])


def _branch_around_parallel_move(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def left_lane():
    move("left", "{trajectory_primary}")
    barrier("{barrier_a}")
    wait("left", {duration_a_ns})

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")
    move("right", "{trajectory_secondary}")

def task():
    if ready:
        parallel(left_lane, right_lane)
    else:
        for cycle in range({loop_bound}):
            wait("{primary_arm}", {duration_c_ns})
'''.format(**p)
    parallel_bound = (
        max(TRAJECTORY_DURATION_NS, p["duration_b_ns"])
        + max(p["duration_a_ns"], TRAJECTORY_DURATION_NS)
    )
    return source, max(parallel_bound, p["loop_bound"] * p["duration_c_ns"])


def _loop_then_dual_barrier(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def left_lane():
    move("left", "{trajectory_primary}")
    barrier("{barrier_a}")
    wait("left", {duration_a_ns})
    barrier("{barrier_b}")

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")
    move("right", "{trajectory_secondary}")
    barrier("{barrier_b}")

def task():
    for cycle in range({loop_bound}):
        wait("{primary_arm}", {duration_c_ns})
    parallel(left_lane, right_lane)
'''.format(**p)
    parallel_bound = (
        max(TRAJECTORY_DURATION_NS, p["duration_b_ns"])
        + max(p["duration_a_ns"], TRAJECTORY_DURATION_NS)
    )
    return source, p["loop_bound"] * p["duration_c_ns"] + parallel_bound


def _resource_object_interleave(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def task():
    acquire("{primary_arm}", "{resource_id}")
    close("{primary_arm}", "{object_id}", {duration_a_ns})
    if mode != "fast":
        wait("{primary_arm}", {duration_b_ns})
        open("{primary_arm}", "{object_id}", {duration_c_ns})
    else:
        for cycle in range({loop_bound}):
            wait("{primary_arm}", {duration_c_ns})
        open("{primary_arm}", "{object_id}", {duration_b_ns})
    release("{primary_arm}", "{resource_id}")
'''.format(**p)
    return source, p["duration_a_ns"] + max(
        p["duration_b_ns"] + p["duration_c_ns"],
        p["loop_bound"] * p["duration_c_ns"] + p["duration_b_ns"],
    )


def _synced_guarded_handover(p: Mapping[str, Any]) -> Tuple[str, int]:
    source = '''def left_lane():
    wait("left", {duration_a_ns})
    barrier("{barrier_a}")

def right_lane():
    wait("right", {duration_b_ns})
    barrier("{barrier_a}")

def task():
    close("{primary_arm}", "{object_id}", {duration_a_ns})
    parallel(left_lane, right_lane)
    close("{secondary_arm}", "{object_id}", {duration_b_ns})
    if ready:
        transfer_authority("{object_id}", "{primary_arm}", "{secondary_arm}")
        wait("{secondary_arm}", {duration_c_ns})
    else:
        wait("{primary_arm}", {duration_c_ns})
    open("{primary_arm}", "{object_id}", {duration_a_ns})
'''.format(**p)
    return source, p["duration_a_ns"] + max(p["duration_a_ns"], p["duration_b_ns"]) + p["duration_b_ns"] + p["duration_c_ns"] + p["duration_a_ns"]


TEMPLATES: Mapping[str, Callable[[Mapping[str, Any]], Tuple[str, int]]] = {
    "branch_wait_diamond": _branch_wait_diamond,
    "looped_resource": _looped_resource,
    "single_barrier_parallel": _single_barrier_parallel,
    "branch_resource_diamond": _branch_resource_diamond,
    "loop_object_guard": _loop_object_guard,
    "parallel_two_barrier": _parallel_two_barrier,
    "parallel_then_resource": _parallel_then_resource,
    "branch_around_parallel_move": _branch_around_parallel_move,
    "loop_then_dual_barrier": _loop_then_dual_barrier,
    "resource_object_interleave": _resource_object_interleave,
    "synced_guarded_handover": _synced_guarded_handover,
}


def families_for_split(split: str) -> Tuple[str, ...]:
    return tuple(
        sorted(
            family_id
            for family_id, definition in FAMILY_DEFINITIONS.items()
            if definition["split"] == split
        )
    )


def generate_candidate(split: str, family_id: str, seed: int) -> GeneratedCandidate:
    """Generate one identity-frozen candidate without consulting any label."""

    if split not in {"design_set", "development_fixture"}:
        raise ValueError("the current generator is prep-only and cannot produce formal sets")
    if family_id not in FAMILY_DEFINITIONS:
        raise ValueError("unknown family_id")
    definition = FAMILY_DEFINITIONS[family_id]
    if definition["split"] != split:
        raise ValueError("family is assigned to a different split")
    if split == "blind_handwritten_test":
        raise ValueError("blind_handwritten_test is never generator-produced")
    rng = HashRng(seed, "{}:{}".format(GRAMMAR_VERSION, family_id))
    parameters = _parameters(rng)
    body, declared_time_bound_ns = TEMPLATES[definition["template"]](parameters)
    source = _header() + body
    computed_structural_time_bound_ns = source_time_upper_bound_ns(
        source, {trajectory: TRAJECTORY_DURATION_NS for trajectory in TRAJECTORIES}
    )
    if declared_time_bound_ns < computed_structural_time_bound_ns:
        raise ValueError(
            "declared time bound {} is below structural upper bound {}".format(
                declared_time_bound_ns, computed_structural_time_bound_ns
            )
        )
    return GeneratedCandidate(
        evidence_status=PREP_STATUS,
        grammar_version=GRAMMAR_VERSION,
        family_id=family_id,
        split=split,
        seed=seed,
        source=source,
        parameters=parameters,
        declared_time_bound_ns=declared_time_bound_ns,
        computed_structural_time_bound_ns=computed_structural_time_bound_ns,
        canonical=canonicalize_source(source),
        generator_sha256=generator_sha256(),
        toolchain_identity=compute_toolchain_identity(),
    )
