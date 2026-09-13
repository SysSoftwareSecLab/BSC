"""Paired analysis and mechanical fairness checks for Stage 4 RQ2.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import json
import hashlib
import math
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from . import PREP_STATUS, RQ2_VARIANTS


FAMILY_SUBSETS = {
    "ablation_non_timed": (
        "CTL_HANDOVER_BRANCH_SYNC_V1",
        "CTL_HANDOVER_LOOP_SYNC_V1",
        "CTL_RESOURCE_BRANCH_INTERLEAVE_V1",
        "CTL_RESOURCE_PARALLEL_RELEASE_V1",
        "CTL_COLLISION_GUARDED_MOVE_V1",
        "CTL_COLLISION_PARALLEL_MOVE_V1",
    ),
    "ablation_separated_state_property": (
        "RQ2_GRASP_AUTH_LOCAL_V1",
        "RQ2_GRASP_AUTH_CROSSCHECK_V1",
    ),
    "ablation_no_attached_object_geometry": (
        "CTL_COLLISION_PARALLEL_MOVE_V1",
    ),
}

RUN_STATUSES = {
    "ok",
    "timeout",
    "provider_failure",
    "missing",
    "exception",
    "resource_truncated",
}
VERDICTS = {"verified-within-bounds", "violated", "unknown", "invalid"}
Z_95 = 1.959963984540054
PAIRED_BOOTSTRAP_REPLICATES = 10_000
PAIRED_BOOTSTRAP_SEED = 2026081802
FORBIDDEN_RAW_KEYS = {
    "oracle_label",
    "expected_verdict",
    "target_class",
    "target_verdict",
}


def _forbidden_paths(value: Any, path: str = "record") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_RAW_KEYS:
                found.append(child)
            found.extend(_forbidden_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_paths(item, f"{path}[{index}]"))
    return found


def _ratio(numerator: int, denominator: int) -> Mapping[str, Any]:
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": None,
            "ci_low": None,
            "ci_high": None,
            "zero_denominator": True,
        }
    value = numerator / denominator
    z2 = Z_95 * Z_95
    scale = 1.0 + z2 / denominator
    center = (value + z2 / (2.0 * denominator)) / scale
    half = (
        Z_95
        * math.sqrt(value * (1.0 - value) / denominator + z2 / (4.0 * denominator**2))
        / scale
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "ci_low": max(0.0, center - half),
        "ci_high": min(1.0, center + half),
        "zero_denominator": False,
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _paired_difference_ci(differences: Sequence[int]) -> Mapping[str, Any]:
    if not differences:
        return {
            "estimate": None,
            "ci_low": None,
            "ci_high": None,
            "seed": PAIRED_BOOTSTRAP_SEED,
            "replicates": PAIRED_BOOTSTRAP_REPLICATES,
        }
    estimates = []
    count = len(differences)
    for replicate in range(PAIRED_BOOTSTRAP_REPLICATES):
        total = 0
        for draw in range(count):
            digest = hashlib.sha256(
                f"{PAIRED_BOOTSTRAP_SEED}:{replicate}:{draw}".encode("ascii")
            ).digest()
            total += differences[int.from_bytes(digest[:8], "big") % count]
        estimates.append(total / count)
    return {
        "estimate": sum(differences) / count,
        "ci_low": _quantile(estimates, 0.025),
        "ci_high": _quantile(estimates, 0.975),
        "seed": PAIRED_BOOTSTRAP_SEED,
        "replicates": PAIRED_BOOTSTRAP_REPLICATES,
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _index_records(
    records: Iterable[Mapping[str, Any]],
    *,
    program_metadata: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    indexed: dict[str, dict[str, Mapping[str, Any]]] = {
        variant: {} for variant in RQ2_VARIANTS
    }
    for record in records:
        forbidden = _forbidden_paths(record)
        if forbidden:
            raise ValueError(
                "RQ2 raw variant record contains hidden label fields: "
                + ",".join(sorted(forbidden))
            )
        variant = record.get("variant_id")
        program_id = record.get("program_id")
        if variant not in indexed or program_id not in program_metadata:
            raise ValueError("RQ2 record has an unknown variant or program")
        if program_id in indexed[variant]:
            raise ValueError("duplicate RQ2 program×variant record")
        metadata = program_metadata[program_id]
        if record.get("source_sha256") != metadata.get("source_sha256"):
            raise ValueError("RQ2 record/program SHA-256 mismatch")
        if record.get("family_id") != metadata.get("family_id"):
            raise ValueError("RQ2 record/family mismatch")
        status, verdict = record.get("status"), record.get("verdict")
        if status not in RUN_STATUSES:
            raise ValueError("RQ2 record status is invalid")
        if status == "ok" and verdict not in VERDICTS:
            raise ValueError("RQ2 ok record requires a four-valued verdict")
        if status != "ok" and verdict is not None:
            raise ValueError("RQ2 non-ok record verdict must be null")
        if not isinstance(record.get("budget"), Mapping):
            raise ValueError("RQ2 record lacks the common budget")
        indexed[str(variant)][str(program_id)] = record

    expected_programs = set(program_metadata)
    for variant in RQ2_VARIANTS:
        if set(indexed[variant]) != expected_programs:
            raise ValueError("RQ2 requires the exact same programs for every variant")
    for program_id in sorted(expected_programs):
        budgets = {
            _canonical(indexed[variant][program_id]["budget"])
            for variant in RQ2_VARIANTS
        }
        if len(budgets) != 1:
            raise ValueError("RQ2 paired variants have unequal budgets")
    return indexed


def _correct(record: Mapping[str, Any], label: str) -> bool:
    if record.get("status") != "ok":
        return False
    return (label == "safe" and record.get("verdict") == "verified-within-bounds") or (
        label == "unsafe" and record.get("verdict") == "violated"
    )


def summarize_variant(
    records: Mapping[str, Mapping[str, Any]],
    *,
    oracle_labels: Mapping[str, str],
    program_ids: Sequence[str],
) -> Mapping[str, Any]:
    statuses = Counter()
    verdicts = Counter()
    safe = [program_id for program_id in program_ids if oracle_labels[program_id] == "safe"]
    unsafe = [program_id for program_id in program_ids if oracle_labels[program_id] == "unsafe"]
    for program_id in program_ids:
        record = records[program_id]
        statuses[str(record["status"])] += 1
        if record["status"] == "ok":
            verdicts[str(record["verdict"])] += 1
    decided = verdicts["verified-within-bounds"] + verdicts["violated"]
    violated_unsafe = sum(records[value].get("status") == "ok" and records[value].get("verdict") == "violated" for value in unsafe)
    verified_unsafe = sum(records[value].get("status") == "ok" and records[value].get("verdict") == "verified-within-bounds" for value in unsafe)
    violated_safe = sum(records[value].get("status") == "ok" and records[value].get("verdict") == "violated" for value in safe)
    verified_safe = sum(records[value].get("status") == "ok" and records[value].get("verdict") == "verified-within-bounds" for value in safe)
    correct = sum(_correct(records[value], oracle_labels[value]) for value in program_ids)
    return {
        "N": len(program_ids),
        "N_safe": len(safe),
        "N_unsafe": len(unsafe),
        "status_counts": dict(sorted(statuses.items())),
        "four_value_counts": dict(sorted(verdicts.items())),
        "coverage": _ratio(decided, len(program_ids)),
        "unsafe_recall": _ratio(violated_unsafe, len(unsafe)),
        "false_safe_rate": _ratio(verified_unsafe, len(unsafe)),
        "false_alarm_rate": _ratio(violated_safe, len(safe)),
        "safe_acceptance": _ratio(verified_safe, len(safe)),
        "overall_correct": _ratio(correct, len(program_ids)),
    }


def exact_mcnemar_p(full_only_correct: int, ablation_only_correct: int) -> float:
    """Two-sided exact McNemar/binomial p-value for discordant pairs."""

    if min(full_only_correct, ablation_only_correct) < 0:
        raise ValueError("McNemar counts must be nonnegative")
    discordant = full_only_correct + ablation_only_correct
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, value)
        for value in range(min(full_only_correct, ablation_only_correct) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def _paired_comparison(
    full: Mapping[str, Mapping[str, Any]],
    ablation: Mapping[str, Mapping[str, Any]],
    *,
    oracle_labels: Mapping[str, str],
    program_ids: Sequence[str],
) -> Mapping[str, Any]:
    full_only_correct = 0
    ablation_only_correct = 0
    both_correct = 0
    both_incorrect = 0
    full_detects_ablation_misses = 0
    ablation_false_safe_when_full_detects = 0
    safe_false_alarm_added = 0
    correctness_differences = []
    for program_id in program_ids:
        label = oracle_labels[program_id]
        full_correct = _correct(full[program_id], label)
        ablation_correct = _correct(ablation[program_id], label)
        correctness_differences.append(int(full_correct) - int(ablation_correct))
        if full_correct and ablation_correct:
            both_correct += 1
        elif full_correct:
            full_only_correct += 1
        elif ablation_correct:
            ablation_only_correct += 1
        else:
            both_incorrect += 1
        if label == "unsafe" and full[program_id].get("status") == "ok" and full[program_id].get("verdict") == "violated":
            if not (ablation[program_id].get("status") == "ok" and ablation[program_id].get("verdict") == "violated"):
                full_detects_ablation_misses += 1
            if ablation[program_id].get("status") == "ok" and ablation[program_id].get("verdict") == "verified-within-bounds":
                ablation_false_safe_when_full_detects += 1
        if label == "safe" and full[program_id].get("status") == "ok" and full[program_id].get("verdict") == "verified-within-bounds":
            if ablation[program_id].get("status") == "ok" and ablation[program_id].get("verdict") == "violated":
                safe_false_alarm_added += 1
    return {
        "N_pairs": len(program_ids),
        "both_correct": both_correct,
        "full_only_correct": full_only_correct,
        "ablation_only_correct": ablation_only_correct,
        "both_incorrect": both_incorrect,
        "full_detects_ablation_misses": full_detects_ablation_misses,
        "ablation_false_safe_when_full_detects": ablation_false_safe_when_full_detects,
        "safe_false_alarm_added": safe_false_alarm_added,
        "paired_correctness_difference_full_minus_ablation": _paired_difference_ci(
            correctness_differences
        ),
        "mcnemar_exact_p_unadjusted": exact_mcnemar_p(
            full_only_correct, ablation_only_correct
        ),
    }


def _holm_adjust(values: Mapping[str, float]) -> Mapping[str, float]:
    ordered = sorted(values, key=lambda key: (values[key], key))
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, key in enumerate(ordered):
        candidate = min(1.0, (count - index) * values[key])
        running = max(running, candidate)
        adjusted[key] = running
    return adjusted


def analyze_rq2(
    *,
    records: Iterable[Mapping[str, Any]],
    program_metadata: Mapping[str, Mapping[str, Any]],
    oracle_labels: Mapping[str, str],
    evidence_status: str = PREP_STATUS,
    paper_result_eligible: bool = False,
) -> Mapping[str, Any]:
    if set(oracle_labels) != set(program_metadata):
        raise ValueError("RQ2 requires one independent safe/unsafe label per program")
    if any(value not in {"safe", "unsafe"} for value in oracle_labels.values()):
        raise ValueError("RQ2 oracle labels must be safe or unsafe")
    indexed = _index_records(records, program_metadata=program_metadata)
    all_programs = tuple(sorted(program_metadata))
    summaries = {
        variant: summarize_variant(
            indexed[variant], oracle_labels=oracle_labels, program_ids=all_programs
        )
        for variant in RQ2_VARIANTS
    }
    comparisons = {}
    unadjusted = {}
    for ablation in RQ2_VARIANTS[1:]:
        families = set(FAMILY_SUBSETS[ablation])
        subset = tuple(
            program_id
            for program_id in all_programs
            if program_metadata[program_id]["family_id"] in families
        )
        if not subset:
            raise ValueError(
                f"RQ2 affected-family subset is empty for {ablation}"
            )
        comparison = _paired_comparison(
            indexed["bisafecode_full"],
            indexed[ablation],
            oracle_labels=oracle_labels,
            program_ids=subset,
        )
        comparison["affected_families"] = sorted(families)
        comparison["full_subset_metrics"] = summarize_variant(
            indexed["bisafecode_full"],
            oracle_labels=oracle_labels,
            program_ids=subset,
        )
        comparison["ablation_subset_metrics"] = summarize_variant(
            indexed[ablation], oracle_labels=oracle_labels, program_ids=subset
        )
        comparisons[ablation] = comparison
        unadjusted[ablation] = float(comparison["mcnemar_exact_p_unadjusted"])
    adjusted = _holm_adjust(unadjusted)
    for ablation, value in adjusted.items():
        comparisons[ablation]["mcnemar_exact_p_holm"] = value
    return {
        "schema": "bisafecode.stage4.rq2-paired-ablation-summary/v2",
        "evidence_status": evidence_status,
        "paper_result_eligible": paper_result_eligible,
        "unit": "program_id",
        "variants": summaries,
        "paired_comparisons": comparisons,
        "multiple_comparison_rule": "Holm correction over the three preregistered affected-subset McNemar tests",
    }
