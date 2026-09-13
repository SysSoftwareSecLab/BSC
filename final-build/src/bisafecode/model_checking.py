"""Auditable contract for BiSafeCode's bounded explicit-state model checker.

This module does not add a symbolic SAT/SMT backend.  It names the finite
transition system already implemented by :mod:`bisafecode.explicit_state` and
mechanically guards the two facts needed for the explicit-state claim:

* every supported Timed IR node kind has a declared successor rule; and
* every mutable semantic quantity used by those rules is part of the exact
  structural state key, while immutable inputs are bound by the resolved
  environment and property-checker hashes.

The resulting method name is ``bounded explicit-state model checking``.  Bare
``BMC`` remains ambiguous in the literature because it commonly denotes a
SAT/SMT unrolling.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import textwrap
from dataclasses import dataclass, fields
from typing import get_args

from .explicit_state import (
    ActiveAction,
    ExplicitStateEngine,
    IntervalChecker,
    ObjectState,
    PointChecker,
    SearchBounds,
    SearchEnvironment,
    SearchReport,
    SearchState,
    WorldState,
    search_artifact_dict,
    search_environment_sha256,
)
from .timed_ir import Node, TimedProgram, program_sha256


SHA256 = re.compile(r"[0-9a-f]{64}")

METHOD_NAME = "bounded explicit-state model checking"
METHOD_ABBREVIATION = "explicit-state BMC"
ENCODING = "explicit reachable-state graph; no SAT/SMT encoding"

SUCCESSOR_RULES = {
    "ActionNode": "instantaneous transition or timed-action start; timed completion is emitted at the next event",
    "BarrierNode": "release exactly when both frozen participants wait at the same barrier and are inactive",
    "BranchNode": "one deterministic edge selected from the fully enumerated finite input valuation",
    "EndNode": "terminal obligation check; no ordinary successor",
    "ForkNode": "spawn the left and right lanes and park the main lane at the matching join",
    "JoinNode": "release only after both lanes reach the matching join and are inactive",
    "LoopHeadNode": "enter or exit according to the explicit bounded loop counter",
    "LoopLatchNode": "return to the matching bounded loop head",
    "NopNode": "single control-flow successor",
}

# Full structural equality of these frozen dataclasses is the visited-state
# key.  SourceSpan inside ActiveAction is deliberately retained: it may split
# equivalent states but cannot merge semantically different states.
STATE_KEY_SCHEMA = {
    "SearchState": (
        "environment_hash",
        "time_ns",
        "pc_main",
        "pc_left",
        "pc_right",
        "loop_counts",
        "active",
        "valuation",
        "world",
    ),
    "ActiveAction": (
        "node_id",
        "lane",
        "arm",
        "kind",
        "start_ns",
        "end_ns",
        "source",
        "trajectory_hash",
        "object_id",
        "carrying_objects",
        "gripper_trajectory_ref",
        "gripper_end",
    ),
    "WorldState": (
        "left_q",
        "right_q",
        "left_gripper",
        "right_gripper",
        "objects",
        "resources",
        "scene_facts",
    ),
    "ObjectState": (
        "object_id",
        "grasps",
        "authority",
        "phase",
        "sender",
        "receiver",
        "attachments",
        "free_pose_ref",
    ),
}

SEMANTIC_DEPENDENCY_BINDING = {
    "control location": "SearchState.pc_main/pc_left/pc_right",
    "bounded loop progress": "SearchState.loop_counts",
    "finite program inputs": "SearchState.valuation",
    "global time and running actions": "SearchState.time_ns/active",
    "J1-J8 configurations": "SearchState.world left/right_q and left/right_gripper",
    "grasp, authority, protocol phase, and attachments": "SearchState.world.objects",
    "logical resource ownership": "SearchState.world.resources",
    "physical/task observations already materialized into the transition system": "SearchState.world.scene_facts",
    "program, trajectories, primitive contracts, terminal obligations, and initial world": "immutable SearchEnvironment hash",
    "point and interval property semantics": "immutable PropertyCheckerBinding hashes",
}


def _require_sha256(value: str, label: str) -> None:
    if not SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")


def callable_implementation_sha256(checker) -> str:
    """Hash the actual Python implementation bound to a property checker.

    The qualified entry point rejects a declaration that names some other
    implementation.  Closure/evidence values remain separately named and
    hashed by ``declared_dependencies`` and ``evidence_sha256``.
    """

    target = inspect.unwrap(checker)
    try:
        source = textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError) as error:
        raise ValueError("property checker implementation source is unavailable") from error
    payload = json.dumps(
        {
            "module": getattr(target, "__module__", None),
            "qualname": getattr(target, "__qualname__", None),
            "source": source,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PropertyCheckerBinding:
    """Frozen identity and purity contract for a property checker."""

    name: str
    implementation_sha256: str
    evidence_sha256: tuple[str, ...]
    input_contract: str
    deterministic: bool
    side_effect_free: bool
    declared_dependencies: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.input_contract:
            raise ValueError("property checker requires a name and input contract")
        _require_sha256(self.implementation_sha256, "implementation_sha256")
        for value in self.evidence_sha256:
            _require_sha256(value, "evidence_sha256")
        dependency_names = tuple(name for name, _value in self.declared_dependencies)
        if (
            any(not name for name in dependency_names)
            or len(dependency_names) != len(set(dependency_names))
            or tuple(sorted(self.declared_dependencies)) != self.declared_dependencies
        ):
            raise ValueError("declared dependencies require unique sorted non-empty names")
        for _name, value in self.declared_dependencies:
            _require_sha256(value, "declared dependency SHA-256")
        if not self.deterministic or not self.side_effect_free:
            raise ValueError(
                "model-checking qualification requires deterministic, side-effect-free checkers"
            )

    def identity_sha256(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "implementation_sha256": self.implementation_sha256,
                "evidence_sha256": self.evidence_sha256,
                "input_contract": self.input_contract,
                "deterministic": self.deterministic,
                "side_effect_free": self.side_effect_free,
                "declared_dependencies": self.declared_dependencies,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ExplicitStateModelCheckingResult:
    contract: dict
    search_artifact: dict
    report: SearchReport


class BoundedExplicitStateModelChecker:
    """Qualified entry point for explicit-state bounded model checking.

    Unlike the lower-level ``ExplicitStateEngine``, this entry point requires
    hash-bound purity contracts for both property checkers and emits the
    finite-transition-system contract together with every search result.
    """

    def __init__(
        self,
        program: TimedProgram,
        environment: SearchEnvironment,
        *,
        interval_checker: IntervalChecker,
        point_checker: PointChecker,
        interval_checker_binding: PropertyCheckerBinding,
        point_checker_binding: PropertyCheckerBinding,
    ) -> None:
        self.program = program
        self.environment = environment
        self.interval_checker = interval_checker
        self.point_checker = point_checker
        self.interval_checker_binding = interval_checker_binding
        self.point_checker_binding = point_checker_binding
        actual_interval = callable_implementation_sha256(interval_checker)
        actual_point = callable_implementation_sha256(point_checker)
        if actual_interval != interval_checker_binding.implementation_sha256:
            raise ValueError("interval checker implementation identity mismatch")
        if actual_point != point_checker_binding.implementation_sha256:
            raise ValueError("point checker implementation identity mismatch")

    def run(self, bounds: SearchBounds) -> ExplicitStateModelCheckingResult:
        contract = explicit_state_contract(
            self.program,
            self.environment,
            bounds,
            point_checker=self.point_checker_binding,
            interval_checker=self.interval_checker_binding,
        )
        report = ExplicitStateEngine(
            self.program,
            self.environment,
            interval_checker=self.interval_checker,
            point_checker=self.point_checker,
        ).run(bounds)
        if report.verdict.value == "verified-within-bounds" and not report.search_exhausted:
            raise AssertionError("a non-exhausted search cannot be verified")
        artifact = search_artifact_dict(
            self.program, self.environment, bounds, report
        )
        artifact["model_checking_contract"] = contract
        return ExplicitStateModelCheckingResult(contract, artifact, report)


def implementation_schema_audit() -> dict:
    """Check that the declared transition and state schemas match the code."""

    node_types = sorted(item.__name__ for item in get_args(Node))
    declared_node_types = sorted(SUCCESSOR_RULES)
    actual_state_fields = {
        cls.__name__: tuple(field.name for field in fields(cls))
        for cls in (SearchState, ActiveAction, WorldState, ObjectState)
    }
    return {
        "supported_ir_node_types": node_types,
        "successor_rule_node_types": declared_node_types,
        "successor_rule_coverage_complete": node_types == declared_node_types,
        "state_key_schema": actual_state_fields,
        "state_key_schema_matches_implementation": actual_state_fields
        == STATE_KEY_SCHEMA,
        "semantic_dependency_binding": SEMANTIC_DEPENDENCY_BINDING,
        "no_partial_order_reduction": True,
    }


def explicit_state_contract(
    program: TimedProgram,
    environment: SearchEnvironment,
    bounds: SearchBounds,
    *,
    point_checker: PropertyCheckerBinding,
    interval_checker: PropertyCheckerBinding,
) -> dict:
    """Return the finite transition-system contract for one bounded run."""

    audit = implementation_schema_audit()
    if not audit["successor_rule_coverage_complete"]:
        raise ValueError("a supported Timed IR node lacks a successor rule")
    if not audit["state_key_schema_matches_implementation"]:
        raise ValueError("the exact structural state key drifted from its schema")
    return {
        "schema": "bisafecode.explicit-state-model-checking-contract/v1",
        "method": METHOD_NAME,
        "abbreviation": METHOD_ABBREVIATION,
        "encoding": ENCODING,
        "finite_transition_system": {
            "initial_states": "Cartesian product of declared finite inputs at the frozen initial world",
            "state_key": STATE_KEY_SCHEMA,
            "transition_relation": SUCCESSOR_RULES,
            "search": "unreduced breadth-first worklist with exact structural deduplication",
            "property": "point obligations on reachable states and interval obligations on every time-elapse edge",
        },
        "program_sha256": program_sha256(program),
        "resolved_environment_sha256": search_environment_sha256(environment),
        "bounds": {
            "max_states": bounds.max_states,
            "max_transitions": bounds.max_transitions,
            "max_time_ns": bounds.max_time_ns,
            "wall_timeout_s": bounds.wall_timeout_s,
        },
        "limit_policy": "any state, transition, global-time, wall-time, or unresolved property limit returns unknown",
        "point_checker_identity_sha256": point_checker.identity_sha256(),
        "interval_checker_identity_sha256": interval_checker.identity_sha256(),
        "implementation_schema_audit": audit,
    }
