"""Registry-aligned Stage 4 correctness metrics.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Main denominators use every oracle-valid program.  A missing, exceptional, or
resource-truncated verifier run therefore remains in the denominator and is
also reported explicitly; generation rejection is a separate engineering
diagnostic and never substitutes for ``invalid_rate``.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import PREP_STATUS
from .schema import validate_run_record


VERDICT_ORDER = (
    "verified-within-bounds",
    "violated",
    "unknown",
    "invalid",
)
SAFE = "verified-within-bounds"
UNSAFE = "violated"
BOOTSTRAP_SEED = 2026081701
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_CONFIDENCE_LEVEL = 0.95


def _wilson(
    numerator: int, denominator: int, z: float = 1.959963984540054
) -> Mapping[str, Any]:
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": None,
            "ci_method": "Wilson 95%",
            "ci_low": None,
            "ci_high": None,
            "zero_denominator": True,
        }
    value = numerator / denominator
    z2 = z * z
    center = (value + z2 / (2 * denominator)) / (1 + z2 / denominator)
    half = (
        z
        * math.sqrt(
            value * (1 - value) / denominator
            + z2 / (4 * denominator * denominator)
        )
        / (1 + z2 / denominator)
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "ci_method": "Wilson 95%",
        "ci_low": max(0.0, center - half),
        "ci_high": min(1.0, center + half),
        "zero_denominator": False,
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _quantile(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _bootstrap_median_ci(values: Sequence[float]) -> Mapping[str, Any]:
    common = {
        "method": "program-level paired bootstrap of median",
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
    }
    if not values:
        return {
            **common,
            "ci_low": None,
            "ci_high": None,
            "zero_denominator": True,
        }
    samples = []
    count = len(values)
    for replicate in range(BOOTSTRAP_REPLICATES):
        sample = []
        for draw in range(count):
            digest = hashlib.sha256(
                "{}:{}:{}".format(BOOTSTRAP_SEED, replicate, draw).encode("ascii")
            ).digest()
            sample.append(values[int.from_bytes(digest[:8], "big") % count])
        samples.append(float(_median(sample)))
    alpha = (1.0 - BOOTSTRAP_CONFIDENCE_LEVEL) / 2.0
    return {
        **common,
        "ci_low": _quantile(samples, alpha),
        "ci_high": _quantile(samples, 1.0 - alpha),
        "zero_denominator": False,
    }


def _numeric_summary(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "q1": None,
            "median": None,
            "q3": None,
            "iqr": None,
            "max": None,
            "values": [],
            "median_bootstrap_ci": _bootstrap_median_ci(values),
        }
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    return {
        "count": len(values),
        "min": min(values),
        "q1": q1,
        "median": _median(values),
        "q3": q3,
        "iqr": q3 - q1,
        "max": max(values),
        "values": list(sorted(values)),
        "median_bootstrap_ci": _bootstrap_median_ci(values),
    }


def _index_records(
    records: Iterable[Mapping[str, Any]], phase: str
) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for record in records:
        errors = validate_run_record(record)
        if errors:
            raise ValueError("invalid {} record: {}".format(phase, "; ".join(errors)))
        if record["phase"] != phase:
            raise ValueError("record in wrong phase collection")
        program_id = str(record["program_id"])
        if program_id in result:
            raise ValueError("duplicate {} record for {}".format(phase, program_id))
        result[program_id] = record
    return result


def analyze_results(
    manifest: Mapping[str, Any],
    oracle_records: Iterable[Mapping[str, Any]],
    verifier_records: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    records = list(manifest["records"])
    accepted = [record for record in records if record["status"] == "accepted"]
    rejected = [record for record in records if record["status"] == "rejected"]
    accepted_ids = {str(record["program_id"]) for record in accepted}
    oracle = _index_records(oracle_records, "oracle")
    verifier = _index_records(verifier_records, "verifier")
    if set(oracle) - accepted_ids or set(verifier) - accepted_ids:
        raise ValueError("run records contain non-admitted program IDs")

    execution_counts = {"oracle": Counter(), "verifier": Counter()}
    failure_reasons = {"oracle": Counter(), "verifier": Counter()}
    runtimes = {"oracle": [], "verifier": []}
    for program_id in sorted(accepted_ids):
        for phase, indexed in (("oracle", oracle), ("verifier", verifier)):
            record = indexed.get(program_id)
            if record is None:
                execution_counts[phase]["missing"] += 1
                failure_reasons[phase]["MISSING_RECORD"] += 1
                continue
            execution_counts[phase][record["status"]] += 1
            if record["status"] != "ok":
                failure_reasons[phase].update(
                    record.get("reason_codes") or [record["status"].upper()]
                )
            runtime_ms = record.get("runtime_ms")
            if isinstance(runtime_ms, (int, float)) and not isinstance(runtime_ms, bool):
                runtimes[phase].append(float(runtime_ms))

    valid_ids = {
        program_id
        for program_id in accepted_ids
        if program_id in oracle
        and oracle[program_id]["status"] == "ok"
        and oracle[program_id]["verdict"] in {SAFE, UNSAFE}
    }
    safe_ids = {program_id for program_id in valid_ids if oracle[program_id]["verdict"] == SAFE}
    unsafe_ids = valid_ids - safe_ids
    verifier_verdict_counts = Counter()
    verifier_nonverdict_counts = Counter()
    verifier_unknown_reasons = Counter()
    for program_id in sorted(valid_ids):
        record = verifier.get(program_id)
        if record is None:
            verifier_nonverdict_counts["missing"] += 1
        elif record["status"] != "ok":
            verifier_nonverdict_counts[record["status"]] += 1
        else:
            verifier_verdict_counts[record["verdict"]] += 1
            if record["verdict"] == "unknown":
                verifier_unknown_reasons.update(
                    record.get("reason_codes") or ["UNSPECIFIED_UNKNOWN"]
                )

    def predicted(program_id: str, verdict: str) -> bool:
        record = verifier.get(program_id)
        return bool(
            record
            and record["status"] == "ok"
            and record["verdict"] == verdict
        )

    decided = verifier_verdict_counts[SAFE] + verifier_verdict_counts[UNSAFE]
    correct_decided = sum(predicted(program_id, SAFE) for program_id in safe_ids) + sum(
        predicted(program_id, UNSAFE) for program_id in unsafe_ids
    )
    violated_on_unsafe = sum(predicted(program_id, UNSAFE) for program_id in unsafe_ids)
    verified_on_unsafe = sum(predicted(program_id, SAFE) for program_id in unsafe_ids)
    violated_on_safe = sum(predicted(program_id, UNSAFE) for program_id in safe_ids)
    verified_on_safe = sum(predicted(program_id, SAFE) for program_id in safe_ids)

    confusion = {
        expected: {observed: 0 for observed in VERDICT_ORDER}
        for expected in VERDICT_ORDER
    }
    paired = []
    oracle_unknown_reasons = Counter()
    for program_id in sorted(accepted_ids):
        oracle_record = oracle.get(program_id)
        verifier_record = verifier.get(program_id)
        if oracle_record and oracle_record["status"] == "ok" and oracle_record["verdict"] == "unknown":
            oracle_unknown_reasons.update(
                oracle_record.get("reason_codes") or ["UNSPECIFIED_UNKNOWN"]
            )
        if not (
            oracle_record
            and verifier_record
            and oracle_record["status"] == "ok"
            and verifier_record["status"] == "ok"
        ):
            continue
        expected = oracle_record["verdict"]
        observed = verifier_record["verdict"]
        confusion[expected][observed] += 1
        paired.append((program_id, expected, observed))

    violated_verifier_records = [
        verifier[program_id]
        for program_id in sorted(valid_ids)
        if predicted(program_id, UNSAFE)
    ]
    replay_success = sum(
        bool(record.get("counterexample", {}).get("replay_success"))
        for record in violated_verifier_records
    )
    location_valid = sum(
        bool(record.get("counterexample", {}).get("source_location_valid"))
        for record in violated_verifier_records
    )
    lengths = [
        float(record["counterexample"]["length_steps"])
        for record in violated_verifier_records
        if isinstance(record.get("counterexample"), dict)
        and isinstance(record["counterexample"].get("length_steps"), int)
    ]
    earliest_steps = [
        float(record["counterexample"]["earliest_violation_step"])
        for record in violated_verifier_records
        if isinstance(record.get("counterexample"), dict)
        and isinstance(record["counterexample"].get("earliest_violation_step"), int)
    ]

    family_coverage = Counter(str(record["family_id"]) for record in accepted)
    syntax_coverage = Counter()
    strata_coverage: Dict[str, Counter] = {
        "length": Counter(),
        "concurrency": Counter(),
        "time_bound": Counter(),
        "loop_bound": Counter(),
    }
    for record in accepted:
        for syntax, count in record["structure"]["action_kind_counts"].items():
            if count:
                syntax_coverage[syntax] += 1
        for syntax in ("if", "loop", "parallel", "barrier"):
            if record["structure"]["{}_count".format(syntax)] > 0:
                syntax_coverage[syntax] += 1
        for dimension in strata_coverage:
            strata_coverage[dimension][record["strata"][dimension]] += 1

    rejection_reasons = Counter(
        reason for record in rejected for reason in record["rejection_reasons"]
    )
    attempted = len(records)
    exact_agreement = sum(expected == observed for _, expected, observed in paired)
    return {
        "evidence_status": PREP_STATUS,
        "schema": "bisafecode.stage4.unseen.metric-summary/v2",
        "paper_result_eligible": False,
        "population": {
            "attempted_generation": attempted,
            "admitted_programs": len(accepted),
            "N_valid": len(valid_ids),
            "N_safe": len(safe_ids),
            "N_unsafe": len(unsafe_ids),
            "admitted_without_valid_oracle": len(accepted) - len(valid_ids),
            "paired_successful_records_secondary_only": len(paired),
        },
        "coverage": {
            **_wilson(decided, len(valid_ids)),
            "formula": "(verified + violated) / N_valid",
        },
        "unknown_rate": {
            **_wilson(verifier_verdict_counts["unknown"], len(valid_ids)),
            "formula": "unknown / N_valid",
            "reason_counts": dict(sorted(verifier_unknown_reasons.items())),
        },
        "invalid_rate": {
            **_wilson(verifier_verdict_counts["invalid"], len(valid_ids)),
            "formula": "invalid / N_valid",
            "not_generation_rejection": True,
        },
        "unsafe_recall": {
            **_wilson(violated_on_unsafe, len(unsafe_ids)),
            "formula": "violated_on_unsafe / N_unsafe",
        },
        "false_safe_rate": {
            **_wilson(verified_on_unsafe, len(unsafe_ids)),
            "formula": "verified_on_unsafe / N_unsafe",
            "definition_frozen_before_results": True,
        },
        "false_alarm_rate": {
            **_wilson(violated_on_safe, len(safe_ids)),
            "formula": "violated_on_safe / N_safe",
        },
        "safe_acceptance": {
            **_wilson(verified_on_safe, len(safe_ids)),
            "formula": "verified_on_safe / N_safe",
        },
        "overall_correct": {
            **_wilson(correct_decided, len(valid_ids)),
            "formula": "(verified_on_safe + violated_on_unsafe) / N_valid",
            "role": "secondary",
        },
        "agreement_on_decided": {
            **_wilson(correct_decided, decided),
            "formula": "correct_decided / decided",
            "must_be_reported_with": "coverage",
        },
        "runtime": {
            "unit": "ms",
            "oracle": _numeric_summary(runtimes["oracle"]),
            "verifier": _numeric_summary(runtimes["verifier"]),
            "timeout_treatment": "retained in execution accounting; included in runtime only when runtime_ms is present",
        },
        "verifier_outcome_accounting_on_N_valid": {
            "four_value_counts": {
                value: verifier_verdict_counts[value] for value in VERDICT_ORDER
            },
            "nonverdict_execution_counts": dict(
                sorted(verifier_nonverdict_counts.items())
            ),
        },
        "execution_accounting_all_admitted": {
            phase: dict(sorted(counts.items()))
            for phase, counts in execution_counts.items()
        },
        "failure_exception_resource_truncation_reasons": {
            phase: dict(sorted(counts.items()))
            for phase, counts in failure_reasons.items()
        },
        "generation_rejection_rate_engineering_diagnostic": {
            **_wilson(len(rejected), attempted),
            "cannot_replace_invalid_rate": True,
            "rejection_reason_counts": dict(sorted(rejection_reasons.items())),
        },
        "four_value_confusion_matrix_secondary_complete_case": {
            "rows": "independent_oracle",
            "columns": "verifier",
            "order": list(VERDICT_ORDER),
            "counts": confusion,
            "denominator": len(paired),
        },
        "exact_four_valued_agreement_rate_secondary_complete_case": _wilson(
            exact_agreement, len(paired)
        ),
        "counterexamples": {
            "verifier_violated_denominator": len(violated_verifier_records),
            "source_level_replay_success_rate": _wilson(
                replay_success, len(violated_verifier_records)
            ),
            "source_location_validity_rate": _wilson(
                location_valid, len(violated_verifier_records)
            ),
            "length_steps": _numeric_summary(lengths),
            "earliest_violation_step": _numeric_summary(earliest_steps),
        },
        "structural_coverage": {
            "family_program_counts": dict(sorted(family_coverage.items())),
            "syntax_program_counts": dict(sorted(syntax_coverage.items())),
            "strata_program_counts": {
                key: dict(sorted(value.items()))
                for key, value in sorted(strata_coverage.items())
            },
        },
        "oracle_nonvalid_label_accounting": {
            "unknown_reason_counts": dict(sorted(oracle_unknown_reasons.items())),
            "all_four_value_counts": dict(
                sorted(
                    Counter(
                        record["verdict"]
                        for record in oracle.values()
                        if record["status"] == "ok"
                    ).items()
                )
            ),
        },
        "paired_analysis_bootstrap": {
            "unit": "program_id",
            "seed": BOOTSTRAP_SEED,
            "replicates": BOOTSTRAP_REPLICATES,
            "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "resampling_rule": "resample frozen program IDs with replacement; keep all method outcomes for each sampled ID together",
        },
    }
