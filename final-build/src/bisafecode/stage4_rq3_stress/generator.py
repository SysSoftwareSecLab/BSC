"""Deterministic sources for the frozen multi-axis RQ3 stress sequence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import FREEZE_STATUS


STRESS_LEVELS = {
    "program_length": (128, 256, 512, 1024, 2048, 4096, 8192, 16384),
    "branch_count": (32, 64, 128, 256, 512, 1024, 2048, 4096),
    "parallel_lane_resource_cycles": (1, 2, 4, 8, 16, 32, 64, 128, 256),
}
STRUCTURAL_CAPS = {
    "parallel_width": {
        "maximum": 2,
        "reason": "frozen dual-arm language exposes at most two lanes",
    },
    "loop_bound": {
        "maximum": 4,
        "reason": "frozen Stage-4 grammar admits loop bounds only through four",
    },
}
REPETITIONS_PER_EXECUTED_LEVEL = 3


@dataclass(frozen=True)
class StressCandidate:
    program_id: str
    axis: str
    requested_level: int
    source: str
    source_sha256: str


def _program_length_source(level: int) -> str:
    if level % 2:
        raise ValueError("program-length stress levels must be even")
    lines = ["def task():"]
    for _ in range(level // 2):
        lines.extend((
            '    acquire("left", "fixture_alpha")',
            '    release("left", "fixture_alpha")',
        ))
    return "\n".join(lines) + "\n"


def _branch_source(level: int) -> str:
    lines = ["def task():"]
    for _ in range(level):
        lines.extend((
            "    if ready:",
            '        acquire("left", "fixture_alpha")',
            '        release("left", "fixture_alpha")',
            "    else:",
            '        acquire("right", "tool_beta")',
            '        release("right", "tool_beta")',
        ))
    return "\n".join(lines) + "\n"


def _parallel_resource_source(level: int) -> str:
    lines = ["def left_lane():"]
    for _ in range(level):
        lines.extend((
            '    acquire("left", "fixture_alpha")',
            '    release("left", "fixture_alpha")',
        ))
    lines.extend(("", "def right_lane():"))
    for _ in range(level):
        lines.extend((
            '    acquire("right", "tool_beta")',
            '    release("right", "tool_beta")',
        ))
    lines.extend(("", "def task():", "    parallel(left_lane, right_lane)", ""))
    return "\n".join(lines)


def render_stress_candidate(axis: str, requested_level: int) -> StressCandidate:
    if axis not in STRESS_LEVELS or requested_level not in STRESS_LEVELS[axis]:
        raise ValueError("axis/level is outside the frozen stress grid")
    if axis == "program_length":
        body = _program_length_source(requested_level)
    elif axis == "branch_count":
        body = _branch_source(requested_level)
    else:
        body = _parallel_resource_source(requested_level)
    program_id = (
        "EXP-S4-010-" + axis.upper().replace("_", "-") + f"-{requested_level}"
    )
    source = (
        "# EXP-S4-010_LOCKED_STRESS_SOURCE\n"
        f"# AXIS={axis};LEVEL={requested_level}\n"
        + body
    )
    return StressCandidate(
        program_id=program_id,
        axis=axis,
        requested_level=requested_level,
        source=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def stress_candidates() -> tuple[StressCandidate, ...]:
    return tuple(
        render_stress_candidate(axis, level)
        for axis, levels in STRESS_LEVELS.items()
        for level in levels
    )


def freeze_preview() -> dict[str, object]:
    candidates = stress_candidates()
    return {
        "schema": "bisafecode.stage4.rq3-stress.freeze-preview/v1",
        "evidence_status": FREEZE_STATUS,
        "axes": {axis: list(levels) for axis, levels in STRESS_LEVELS.items()},
        "structural_caps": STRUCTURAL_CAPS,
        "candidate_count": len(candidates),
        "maximum_scheduled_runs": len(candidates) * REPETITIONS_PER_EXECUTED_LEVEL,
        "unique_source_count": len({item.source_sha256 for item in candidates}),
        "formal_runs": 0,
        "paper_result_eligible": False,
    }
