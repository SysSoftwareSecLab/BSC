"""Deterministic, label-separated generator for EXP-S4-009.

The method-facing source never contains a target verdict, exposure stratum,
or generator parameter.  Those fields live only in the design manifest used
after method outputs have been sealed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping

from . import EXPERIMENT_ID, FREEZE_STATUS


STRATA: Mapping[str, Mapping[str, Any]] = {
    "common": {
        "benign_cycles": 0,
        "exact_exposure_probability": "1/2",
    },
    "medium": {
        "benign_cycles": 2,
        "exact_exposure_probability": "3/32",
    },
    "rare": {
        "benign_cycles": 4,
        "exact_exposure_probability": "5/512",
    },
    "extreme": {
        "benign_cycles": 6,
        "exact_exposure_probability": "7/8192",
    },
}
SAFE_CONTROL_CYCLES = 6
RESOURCES = ("fixture_alpha", "tool_beta")
ARMS = ("left", "right")
POSTLUDE_CYCLES = (1, 2)


@dataclass(frozen=True)
class ChallengeCase:
    opaque_case_id: str
    source: str
    source_sha256: str
    role: str
    stratum: str
    benign_cycles: int
    fast_arm: str
    contested_resource: str
    slow_postlude_cycles: int
    symmetry_variant: int

    def method_record(self) -> Mapping[str, Any]:
        return {
            "opaque_case_id": self.opaque_case_id,
            "source_sha256": self.source_sha256,
            "source_path": f"sealed_sources/{self.opaque_case_id}.py",
        }

    def design_record(self) -> Mapping[str, Any]:
        return {
            **self.method_record(),
            "role": self.role,
            "stratum": self.stratum,
            "benign_cycles": self.benign_cycles,
            "fast_arm": self.fast_arm,
            "contested_resource": self.contested_resource,
            "slow_postlude_cycles": self.slow_postlude_cycles,
            "symmetry_variant": self.symmetry_variant,
        }


def _calls(arm: str, resource: str, cycles: int) -> list[str]:
    result: list[str] = []
    for _ in range(cycles):
        result.extend(
            (
                f'    acquire("{arm}", "{resource}")',
                f'    release("{arm}", "{resource}")',
            )
        )
    return result


def render_source(
    *,
    benign_cycles: int,
    fast_arm: str,
    contested_resource: str,
    slow_postlude_cycles: int,
    unsafe: bool,
) -> str:
    if benign_cycles < 0:
        raise ValueError("benign_cycles must be nonnegative")
    if fast_arm not in ARMS or contested_resource not in RESOURCES:
        raise ValueError("unsupported symmetry parameter")
    if slow_postlude_cycles not in POSTLUDE_CYCLES:
        raise ValueError("unsupported slow-lane postlude")
    slow_arm = "right" if fast_arm == "left" else "left"
    benign_resource = next(item for item in RESOURCES if item != contested_resource)
    bodies = {
        fast_arm: _calls(fast_arm, contested_resource, 1),
        slow_arm: _calls(slow_arm, benign_resource, benign_cycles),
    }
    bodies[slow_arm].extend(
        _calls(
            slow_arm,
            contested_resource if unsafe else benign_resource,
            1,
        )
    )
    # Postlude work occurs only after the slow lane has released its final
    # resource.  It therefore changes the concrete program while leaving the
    # contested-acquire exposure probability unchanged.
    bodies[slow_arm].extend(
        _calls(slow_arm, benign_resource, slow_postlude_cycles)
    )
    lines = ["# EXP-S4-009_LOCKED_SOURCE"]
    for arm in ("left", "right"):
        lines.append(f"def {arm}_lane():")
        lines.extend(bodies[arm])
        lines.append("")
    lines.extend(
        (
            "def task():",
            "    parallel(left_lane, right_lane)",
            "",
        )
    )
    return "\n".join(lines)


def _case(
    *, role: str, stratum: str, benign_cycles: int, variant: int,
    fast_arm: str, contested_resource: str, slow_postlude_cycles: int,
) -> ChallengeCase:
    source = render_source(
        benign_cycles=benign_cycles,
        fast_arm=fast_arm,
        contested_resource=contested_resource,
        slow_postlude_cycles=slow_postlude_cycles,
        unsafe=role == "unsafe_challenge",
    )
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    opaque_case_id = "CASE-RS-" + hashlib.sha256(
        f"{EXPERIMENT_ID}:{source_sha256}".encode("ascii")
    ).hexdigest()[:16].upper()
    return ChallengeCase(
        opaque_case_id=opaque_case_id,
        source=source,
        source_sha256=source_sha256,
        role=role,
        stratum=stratum,
        benign_cycles=benign_cycles,
        fast_arm=fast_arm,
        contested_resource=contested_resource,
        slow_postlude_cycles=slow_postlude_cycles,
        symmetry_variant=variant,
    )


def build_challenge_cases() -> tuple[ChallengeCase, ...]:
    variants = tuple(product(ARMS, RESOURCES, POSTLUDE_CYCLES))
    cases: list[ChallengeCase] = []
    for stratum, specification in STRATA.items():
        for variant, (fast_arm, contested, postlude_cycles) in enumerate(variants):
            cases.append(
                _case(
                    role="unsafe_challenge",
                    stratum=stratum,
                    benign_cycles=int(specification["benign_cycles"]),
                    variant=variant,
                    fast_arm=fast_arm,
                    contested_resource=contested,
                    slow_postlude_cycles=postlude_cycles,
                )
            )
    for variant, (fast_arm, contested, postlude_cycles) in enumerate(variants):
        cases.append(
            _case(
                role="safe_control",
                stratum="safe_control",
                benign_cycles=SAFE_CONTROL_CYCLES,
                variant=variant,
                fast_arm=fast_arm,
                contested_resource=contested,
                slow_postlude_cycles=postlude_cycles,
            )
        )
    if len(cases) != 40 or len({item.source_sha256 for item in cases}) != 40:
        raise AssertionError("challenge schedule must contain forty unique sources")
    return tuple(sorted(cases, key=lambda item: item.opaque_case_id))


def freeze_preview() -> Mapping[str, Any]:
    cases = build_challenge_cases()
    return {
        "schema": "bisafecode.stage4.rare-schedule.freeze-preview/v1",
        "evidence_status": FREEZE_STATUS,
        "experiment_id": EXPERIMENT_ID,
        "population": {
            "total": len(cases),
            "unsafe_challenge": sum(item.role == "unsafe_challenge" for item in cases),
            "safe_control": sum(item.role == "safe_control" for item in cases),
            "symmetry_variants_per_stratum": 8,
        },
        "method_input_records": [item.method_record() for item in cases],
        "design_records": [item.design_record() for item in cases],
        "method_inputs_contain_role_or_stratum": False,
        "formal_generation_executed": False,
        "paper_result_eligible": False,
    }
