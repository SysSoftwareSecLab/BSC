"""Label-free mechanical admission and opaque identity construction."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from bisafecode.restricted_python import (
    InvalidRestrictedPython,
    ParserContext,
    TrajectoryBinding,
    parse_restricted_python,
)
from bisafecode.stage4_unseen.canonicalize import (
    CanonicalizationError,
    canonicalize_source,
    token_edit_distance,
)
from bisafecode.stage4_unseen.time_bounds import source_time_upper_bound_ns
from bisafecode.timed_ir import ActionKind, ActionNode, FiniteInput


TRAJECTORY_DURATIONS = {
    "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26": 400_000_000,
    "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6": 400_000_000,
    "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609": 400_000_000,
    "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea": 400_000_000,
}
ALLOWED_LITERAL_DURATIONS = {20_000_000, 40_000_000, 80_000_000}
TIMED_LITERAL_ACTIONS = {ActionKind.WAIT, ActionKind.CLOSE, ActionKind.OPEN}


def _environment_hash(root: Path) -> str:
    manifest = (
        root
        / "03_experiments/contracts/EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE/ASSET_MANIFEST.json"
    )
    import json
    return str(json.loads(manifest.read_text(encoding="utf-8"))["asset_root_sha256"])


def parser_context(root: Path, program_name: str = "EXTERNAL-ADMISSION") -> ParserContext:
    return ParserContext(
        program_name=program_name,
        finite_inputs=(
            FiniteInput("mode", ("fast", "safe")),
            FiniteInput("ready", (False, True)),
        ),
        object_ids=("payload_alpha", "payload_beta"),
        resource_ids=("fixture_alpha", "tool_beta"),
        trajectories=tuple(
            TrajectoryBinding(sha256, duration)
            for sha256, duration in sorted(TRAJECTORY_DURATIONS.items())
        ),
        environment_hash=_environment_hash(root),
        max_loop_bound=4,
    )


def source_neutral_opaque_id(source_sha256: str) -> str:
    digest = hashlib.sha256(
        ("bisafecode-external-blind-v1:" + source_sha256).encode("ascii")
    ).hexdigest()
    return "CASE-" + digest[:20].upper()


def _duration_issues(program: Any) -> list[str]:
    issues: list[str] = []
    for node in program.nodes:
        if not isinstance(node, ActionNode):
            continue
        if node.action.kind in TIMED_LITERAL_ACTIONS and node.action.duration_ns not in ALLOWED_LITERAL_DURATIONS:
            issues.append(f"DISALLOWED_LITERAL_DURATION:{node.action.duration_ns}")
    return sorted(set(issues))


def mechanical_admission(source: str, *, root: Path) -> Mapping[str, Any]:
    source_bytes = source.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    issues: list[str] = []
    canonical = None
    program = None
    time_bound = None
    try:
        canonical = canonicalize_source(source)
    except (CanonicalizationError, SyntaxError, ValueError) as error:
        issues.append(f"CANONICAL_OR_GRAMMAR:{type(error).__name__}:{error}")
    try:
        program = parse_restricted_python(
            source,
            filename="external_blind/program.py",
            context=parser_context(root),
        )
    except InvalidRestrictedPython as error:
        issues.extend(f"PARSER:{item.code}" for item in error.issues)
    if canonical is not None:
        action_count = int(canonical.structure["source_action_count"])
        if not 1 <= action_count <= 16:
            issues.append(f"SOURCE_ACTION_COUNT_OUT_OF_RANGE:{action_count}")
    if program is not None:
        issues.extend(_duration_issues(program))
    try:
        time_bound = source_time_upper_bound_ns(source, TRAJECTORY_DURATIONS)
    except ValueError as error:
        issues.append(f"TIME_BOUND:{error}")
    else:
        if not 0 <= time_bound <= 5_000_000_000:
            issues.append(f"TIME_BOUND_OUT_OF_RANGE:{time_bound}")
    issues = sorted(set(issues))
    result: dict[str, Any] = {
        "schema": "bisafecode.stage4.external-blind.mechanical-admission/v1",
        "source_sha256": source_sha,
        "opaque_case_id": source_neutral_opaque_id(source_sha),
        "status": "admitted" if not issues else "invalid_syntax_or_grammar",
        "reasons": issues,
        "computed_structural_time_upper_bound_ns": time_bound,
    }
    if canonical is not None:
        result.update({
            "canonical_ast_hash": canonical.canonical_ast_hash,
            "structural_family_signature_hash": canonical.structural_family_signature_hash,
            "time_shift_structural_signature_hash": canonical.time_shift_structural_signature_hash,
            "action_tokens": list(canonical.action_tokens),
            "structure": dict(canonical.structure),
        })
    return result


def compare_with_prior_catalog(
    admission: Mapping[str, Any],
    prior_records: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    exact: list[str] = []
    structural: list[str] = []
    near: list[Mapping[str, Any]] = []
    tokens = tuple(str(item) for item in admission.get("action_tokens", []))
    for record in prior_records:
        record_id = str(record.get("reference_id", "UNKNOWN"))
        if admission.get("source_sha256") == record.get("source_sha256"):
            exact.append(record_id + ":source")
        if admission.get("canonical_ast_hash") == record.get("canonical_ast_hash"):
            exact.append(record_id + ":canonical_ast")
        if admission.get("structural_family_signature_hash") == record.get("structural_family_signature_hash"):
            structural.append(record_id)
        other_tokens = tuple(str(item) for item in record.get("action_tokens", []))
        if tokens and other_tokens:
            distance = token_edit_distance(tokens, other_tokens)
            threshold = max(1, len(other_tokens) // 5)
            if distance <= threshold:
                near.append({"reference_id": record_id, "distance": distance, "threshold": threshold})
    return {
        "exact_prior_duplicate": sorted(set(exact)),
        "structural_overlap_warning": sorted(set(structural)),
        "near_structure_warning": sorted(near, key=lambda item: (item["reference_id"], item["distance"])),
        "correctness_eligible": admission.get("status") == "admitted" and not exact,
    }
