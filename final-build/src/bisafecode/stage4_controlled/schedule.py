"""Exact, non-extensible EXP-S4-002 population schedule.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class FamilySchedule:
    family_id: str
    seed_first: int
    seed_last: int
    structural_subset: str

    @property
    def count(self) -> int:
        return self.seed_last - self.seed_first + 1


FAMILY_SCHEDULES: Tuple[FamilySchedule, ...] = (
    FamilySchedule("CTL_HANDOVER_BRANCH_SYNC_V1", 61001, 61010, "handover"),
    FamilySchedule("CTL_HANDOVER_LOOP_SYNC_V1", 62001, 62010, "handover"),
    FamilySchedule("CTL_RESOURCE_BRANCH_INTERLEAVE_V1", 63001, 63010, "resource"),
    FamilySchedule("CTL_RESOURCE_PARALLEL_RELEASE_V1", 64001, 64010, "resource"),
    FamilySchedule("CTL_COLLISION_GUARDED_MOVE_V1", 65001, 65010, "collision"),
    FamilySchedule("CTL_COLLISION_PARALLEL_MOVE_V1", 66001, 66010, "collision"),
)


def exact_schedule() -> Tuple[Tuple[str, int], ...]:
    """Return exactly 60 slots in family order and ascending seed order."""

    return tuple(
        (family.family_id, seed)
        for family in FAMILY_SCHEDULES
        for seed in range(family.seed_first, family.seed_last + 1)
    )


def assert_schedule_contract() -> None:
    if len(FAMILY_SCHEDULES) != 6:
        raise ValueError("EXP-S4-002 requires exactly six families")
    if any(family.count != 10 for family in FAMILY_SCHEDULES):
        raise ValueError("every EXP-S4-002 family requires exactly ten slots")
    schedule = exact_schedule()
    if len(schedule) != 60 or len(set(schedule)) != 60:
        raise ValueError("EXP-S4-002 schedule must contain 60 unique slots")
    if {family.structural_subset for family in FAMILY_SCHEDULES} != {
        "handover",
        "resource",
        "collision",
    }:
        raise ValueError("all three preregistered structural subsets are required")


def is_scheduled(family_id: str, seed: int) -> bool:
    return (family_id, seed) in set(exact_schedule())


assert_schedule_contract()
