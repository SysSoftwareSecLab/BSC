"""Unscheduled PREP-only source generator for the RQ3 grid pilot.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The pilot varies one requested structural axis at a time.  Its outputs cannot
enter the locked test set and do not freeze the final RQ3 grid.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from bisafecode.stage4_controlled.generator import TRAJECTORIES

from . import PREP_STATUS


PREVIEW_LEVELS = {
    "program_length": (4, 16, 64, 128),
    "branch_count": (0, 2, 8, 16, 32),
    "loop_bound": (1, 2, 3, 4),
    "parallel_width": (1, 2),
    "parallel_region_count": (0, 1, 4, 8, 16),
    "object_count": (0, 1, 2),
    "resource_count": (0, 1, 2),
    "geometric_segment_count": (0, 1, 2, 4, 8),
}
FORMAL_LEVELS = {axis: tuple(levels) for axis, levels in PREVIEW_LEVELS.items()}
FORMAL_REPETITIONS_PER_POINT = 5


@dataclass(frozen=True)
class ScalabilityPreview:
    prep_status: str
    program_id: str
    axis: str
    requested_level: int
    seed: int
    source: str
    source_sha256: str


def _waits(count: int, *, indent: str = "    ") -> list[str]:
    return [f'{indent}wait("left", 20000000)' for _ in range(max(1, count))]


def _render_body(axis: str, level: int) -> str:
    if axis == "program_length":
        return "def task():\n" + "\n".join(_waits(level)) + "\n"
    if axis == "branch_count":
        lines = ["def task():"]
        if level == 0:
            lines.extend(_waits(1))
        for _ in range(level):
            lines.extend(
                (
                    "    if ready:",
                    '        wait("left", 20000000)',
                    "    else:",
                    '        wait("right", 20000000)',
                )
            )
        return "\n".join(lines) + "\n"
    if axis == "loop_bound":
        return (
            "def task():\n"
            f"    for cycle in range({level}):\n"
            '        wait("left", 20000000)\n'
        )
    if axis == "parallel_width":
        if level == 1:
            return 'def task():\n    wait("left", 20000000)\n    wait("right", 20000000)\n'
        return (
            'def left_lane():\n    wait("left", 20000000)\n\n'
            'def right_lane():\n    wait("right", 20000000)\n\n'
            "def task():\n    parallel(left_lane, right_lane)\n"
        )
    if axis == "parallel_region_count":
        if level == 0:
            return 'def task():\n    wait("left", 20000000)\n'
        functions = []
        for index in range(level):
            functions.append(
                f'def left_lane_{index}():\n    wait("left", 20000000)\n\n'
                f'def right_lane_{index}():\n    wait("right", 20000000)\n'
            )
        parallel_calls = "\n".join(
            f"    parallel(left_lane_{index}, right_lane_{index})"
            for index in range(level)
        )
        return "\n".join(functions) + f"\ndef task():\n{parallel_calls}\n"
    if axis == "object_count":
        if level == 0:
            return 'def task():\n    wait("left", 20000000)\n'
        lines = ["def task():", '    close("left", "payload_alpha", 20000000)']
        if level == 2:
            lines.append('    close("right", "payload_beta", 20000000)')
        return "\n".join(lines) + "\n"
    if axis == "resource_count":
        if level == 0:
            return 'def task():\n    wait("left", 20000000)\n'
        lines = ["def task():", '    acquire("left", "fixture_alpha")']
        if level == 2:
            lines.append('    acquire("right", "tool_beta")')
        lines.append('    wait("left", 20000000)')
        if level == 2:
            lines.append('    release("right", "tool_beta")')
        lines.append('    release("left", "fixture_alpha")')
        return "\n".join(lines) + "\n"
    if axis == "geometric_segment_count":
        if level == 0:
            return 'def task():\n    wait("left", 20000000)\n'
        hashes = (
            TRAJECTORIES["left_approach"]["sha256"],
            TRAJECTORIES["left_retreat"]["sha256"],
        )
        lines = ["def task():"]
        for index in range(level):
            lines.append(f'    move("left", "{hashes[index % 2]}")')
        return "\n".join(lines) + "\n"
    raise ValueError("unknown RQ3 preview axis")


def render_scalability_preview(axis: str, level: int, *, seed: int = 20260818) -> ScalabilityPreview:
    if axis not in PREVIEW_LEVELS or level not in PREVIEW_LEVELS[axis]:
        raise ValueError("axis/level is outside the declared RQ3 preview grid")
    program_id = f"PREP-RQ3-{axis.upper()}-{level}-{seed}"
    source = (
        f"# {PREP_STATUS}\n"
        "# UNSCHEDULED_RQ3_GRID_PILOT_NOT_LOCKED_TEST\n"
        f"# AXIS={axis};LEVEL={level};SEED={seed}\n"
        + _render_body(axis, level)
    )
    return ScalabilityPreview(
        prep_status=PREP_STATUS,
        program_id=program_id,
        axis=axis,
        requested_level=level,
        seed=seed,
        source=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def render_scalability_candidate(axis: str, level: int) -> ScalabilityPreview:
    """Render one locked semantic program shared by all five process repeats."""

    if axis not in FORMAL_LEVELS or level not in FORMAL_LEVELS[axis]:
        raise ValueError("axis/level is outside the frozen RQ3 formal grid")
    program_id = f"EXP-S4-005-{axis.upper().replace('_', '-')}-{level}"
    source = (
        "# LOCKED_RAW_PENDING_SCALABILITY_EVALUATION\n"
        f"# AXIS={axis};LEVEL={level}\n"
        + _render_body(axis, level)
    )
    return ScalabilityPreview(
        prep_status="LOCKED_RAW_PENDING_SCALABILITY_EVALUATION",
        program_id=program_id,
        axis=axis,
        requested_level=level,
        seed=20260819,
        source=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def formal_scalability_candidates() -> tuple[ScalabilityPreview, ...]:
    return tuple(
        render_scalability_candidate(axis, level)
        for axis, levels in FORMAL_LEVELS.items()
        for level in levels
    )
