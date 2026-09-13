"""Registry-aligned multi-method statistics for future EXP-S4-002 records.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The functions are deterministic derivations over sealed records.  They do not
run a method or oracle and never delete timeout, missing, exception, resource
truncation, unknown, or invalid outcomes from the applicable denominator.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from . import DERIVED_STATUS
from .methods import method_ids
from .records import FOUR_VERDICTS, validate_method_record


BOOTSTRAP_SEED = 2026081701
BOOTSTRAP_REPLICATES = 10_000
CONFIDENCE_LEVEL = 0.95
Z_95 = 1.959963984540054


def _wilson(numerator: int, denominator: int) -> Mapping[str, Any]:
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
    scale = 1 + z2 / denominator
    center = (value + z2 / (2 * denominator)) / scale
    half = (
        Z_95
        * math.sqrt(value * (1 - value) / denominator + z2 / (4 * denominator**2))
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
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _median(values: Sequence[float]) -> Optional[float]:
    return _quantile(values, 0.5)


def _bootstrap_median(values: Sequence[float]) -> Mapping[str, Any]:
    if not values:
        return {
            "seed": BOOTSTRAP_SEED,
            "replicates": BOOTSTRAP_REPLICATES,
            "confidence_level": CONFIDENCE_LEVEL,
            "ci_low": None,
            "ci_high": None,
            "zero_denominator": True,
        }
    count = len(values)
    medians = []
    for replicate in range(BOOTSTRAP_REPLICATES):
        sample = []
        for draw in range(count):
            digest = hashlib.sha256(
                f"{BOOTSTRAP_SEED}:{replicate}:{draw}".encode("ascii")
            ).digest()
            sample.append(values[int.from_bytes(digest[:8], "big") % count])
        medians.append(float(_median(sample)))
    return {
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "confidence_level": CONFIDENCE_LEVEL,
        "ci_low": _quantile(medians, 0.025),
        "ci_high": _quantile(medians, 0.975),
        "zero_denominator": False,
    }


def _runtime(values: Sequence[float]) -> Mapping[str, Any]:
    q1, q3 = _quantile(values, 0.25), _quantile(values, 0.75)
    return {
        "count": len(values),
        "unit": "ms",
        "median": _median(values),
        "q1": q1,
        "q3": q3,
        "iqr": None if q1 is None or q3 is None else q3 - q1,
        "median_bootstrap_ci": _bootstrap_median(values),
    }


def _index_method_records(
    records: Iterable[Mapping[str, Any]], method_id: str
) -> Dict[str, Mapping[str, Any]]:
    indexed = {}
    for record in records:
        errors = validate_method_record(record)
        if errors:
            raise ValueError("invalid method record: " + "; ".join(errors))
        if record["method_id"] != method_id:
            raise ValueError("record supplied to wrong method collection")
        program_id = str(record["program_id"])
        if program_id in indexed:
            raise ValueError("duplicate method record for " + program_id)
        indexed[program_id] = record
    return indexed


def analyze_method(
    *,
    method_id: str,
    admitted_program_ids: Sequence[str],
    oracle_labels: Mapping[str, str],
    method_records: Iterable[Mapping[str, Any]],
    oracle_outcomes: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Compute one method's main metrics on the oracle-valid population."""

    if method_id not in set(method_ids()):
        raise ValueError("unknown method")
    admitted = set(admitted_program_ids)
    if set(oracle_labels) - admitted:
        raise ValueError("oracle labels contain non-admitted program")
    if any(value not in {"safe", "unsafe"} for value in oracle_labels.values()):
        raise ValueError("oracle_labels contains a nonvalid ground-truth label")
    records = _index_method_records(method_records, method_id)
    if set(records) - admitted:
        raise ValueError("method records contain non-admitted program")
    valid = set(oracle_labels)
    safe = {program_id for program_id, value in oracle_labels.items() if value == "safe"}
    unsafe = valid - safe
    verdict_counts = Counter()
    nonverdict = Counter()
    unknown_reasons = Counter()
    runtimes = []
    for program_id in sorted(valid):
        record = records.get(program_id)
        if record is None:
            nonverdict["missing"] += 1
            continue
        if isinstance(record.get("runtime_ms"), (int, float)) and not isinstance(
            record.get("runtime_ms"), bool
        ):
            runtimes.append(float(record["runtime_ms"]))
        if record["status"] != "ok":
            nonverdict[record["status"]] += 1
            continue
        verdict_counts[record["verdict"]] += 1
        if record["verdict"] == "unknown":
            unknown_reasons.update(record.get("reason_codes") or ["UNSPECIFIED_UNKNOWN"])

    def predicts(program_id: str, verdict: str) -> bool:
        record = records.get(program_id)
        return bool(
            record and record["status"] == "ok" and record["verdict"] == verdict
        )

    verified_safe = sum(predicts(program_id, "verified-within-bounds") for program_id in safe)
    violated_safe = sum(predicts(program_id, "violated") for program_id in safe)
    verified_unsafe = sum(predicts(program_id, "verified-within-bounds") for program_id in unsafe)
    violated_unsafe = sum(predicts(program_id, "violated") for program_id in unsafe)
    decided = verdict_counts["verified-within-bounds"] + verdict_counts["violated"]
    correct_decided = verified_safe + violated_unsafe
    n_valid, n_safe, n_unsafe = len(valid), len(safe), len(unsafe)
    four_oracle = dict(oracle_outcomes or oracle_labels)
    if set(four_oracle) - admitted or any(
        value not in {"safe", "unsafe", "unknown", "invalid"}
        for value in four_oracle.values()
    ):
        raise ValueError("oracle_outcomes contains an invalid four-valued outcome")
    target_verdict = {
        "safe": "verified-within-bounds",
        "unsafe": "violated",
        "unknown": "unknown",
        "invalid": "invalid",
    }
    confusion = {
        label: {
            verdict: sum(
                predicts(program_id, verdict)
                for program_id, oracle_label in four_oracle.items()
                if oracle_label == label
            )
            for verdict in sorted(FOUR_VERDICTS)
        }
        for label in ("safe", "unsafe", "unknown", "invalid")
    }
    exact_four_correct = sum(
        predicts(program_id, target_verdict[label])
        for program_id, label in four_oracle.items()
    )
    result = {
        "method_id": method_id,
        "population": {
            "N_valid": n_valid,
            "N_safe": n_safe,
            "N_unsafe": n_unsafe,
            "admitted_without_valid_oracle": len(admitted) - n_valid,
        },
        "coverage": {**_wilson(decided, n_valid), "formula": "(verified + violated) / N_valid"},
        "unknown_rate": {
            **_wilson(verdict_counts["unknown"], n_valid),
            "formula": "unknown / N_valid",
            "reason_counts": dict(sorted(unknown_reasons.items())),
        },
        "invalid_rate": {**_wilson(verdict_counts["invalid"], n_valid), "formula": "invalid / N_valid"},
        "unsafe_recall": {**_wilson(violated_unsafe, n_unsafe), "formula": "violated_on_unsafe / N_unsafe"},
        "false_safe_rate": {**_wilson(verified_unsafe, n_unsafe), "formula": "verified_on_unsafe / N_unsafe"},
        "false_alarm_rate": {**_wilson(violated_safe, n_safe), "formula": "violated_on_safe / N_safe"},
        "safe_acceptance": {**_wilson(verified_safe, n_safe), "formula": "verified_on_safe / N_safe"},
        "overall_correct": {
            **_wilson(correct_decided, n_valid),
            "formula": "(verified_on_safe + violated_on_unsafe) / N_valid",
            "role": "secondary",
        },
        "agreement_on_decided": {
            **_wilson(correct_decided, decided),
            "formula": "correct_decided / decided",
            "must_report_with": "coverage",
        },
        "four_value_counts": {value: verdict_counts[value] for value in sorted(FOUR_VERDICTS)},
        "four_value_confusion_matrix": {
            "rows": ["safe", "unsafe", "unknown", "invalid"],
            "columns": sorted(FOUR_VERDICTS),
            "counts": confusion,
            "note": "non-ok execution outcomes remain outside verdict cells but inside N_valid main denominators and the failure counts",
        },
        "exact_four_valued_agreement": {
            **_wilson(exact_four_correct, len(four_oracle)),
            "formula": "exact mapped method verdict / N_with_four_valued_oracle_outcome",
            "mapping": target_verdict,
            "role": "secondary",
        },
        "nonverdict_execution_counts": dict(sorted(nonverdict.items())),
        "runtime": _runtime(runtimes),
    }
    if method_id == "llm_as_judge":
        judgment_statuses = Counter()
        judgment_verdicts = Counter()
        completed_correct = 0
        completed_incorrect = 0
        location_schema_valid = 0
        location_reported = 0
        location_spans = 0
        for program_id in sorted(valid):
            record = records.get(program_id)
            accounting = (
                record.get("llm_judgment_accounting")
                if isinstance(record, Mapping)
                else None
            )
            if not isinstance(accounting, Mapping):
                continue
            statuses = accounting.get("judgment_status_counts", {})
            verdicts = accounting.get("verdict_counts", {})
            if isinstance(statuses, Mapping):
                judgment_statuses.update(
                    {str(key): int(value) for key, value in statuses.items()}
                )
            if isinstance(verdicts, Mapping):
                judgment_verdicts.update(
                    {str(key): int(value) for key, value in verdicts.items()}
                )
                target = (
                    "verified-within-bounds"
                    if oracle_labels[program_id] == "safe"
                    else "violated"
                )
                completed_correct += int(verdicts.get(target, 0))
                completed_incorrect += sum(
                    int(verdicts.get(verdict, 0))
                    for verdict in FOUR_VERDICTS
                    if verdict != target
                )
            location_schema_valid += int(
                accounting.get("source_location_schema_valid_count", 0)
            )
            location_reported += int(
                accounting.get("source_location_report_count", 0)
            )
            location_spans += int(
                accounting.get("source_location_span_count", 0)
            )
        scheduled_judgments = 5 * n_valid
        completed_judgments = (
            judgment_statuses["completed"] + judgment_statuses["ok"]
        )
        result["llm_judgment_accounting"] = {
            "scheduled_judgments": scheduled_judgments,
            "retained_status_counts": dict(sorted(judgment_statuses.items())),
            "retained_verdict_counts": dict(sorted(judgment_verdicts.items())),
            "completed_correct": completed_correct,
            "completed_incorrect": completed_incorrect,
            "conditional_judgment_accuracy": {
                **_wilson(
                    completed_correct, completed_correct + completed_incorrect
                ),
                "formula": "completed_correct / (completed_correct + completed_incorrect)",
                "timeout_excluded": True,
            },
            "completion_coverage": {
                **_wilson(completed_judgments, scheduled_judgments),
                "formula": "schema_valid_completed / scheduled_judgments",
            },
            "timeout_rate": {
                **_wilson(judgment_statuses["timeout"], scheduled_judgments),
                "formula": "retained_timeout / scheduled_judgments",
                "counted_as_judgment_error": False,
            },
            "provider_failure_rate": {
                **_wilson(
                    judgment_statuses["provider_failure"], scheduled_judgments
                ),
                "formula": "retained_provider_failure / scheduled_judgments",
                "counted_as_judgment_error": False,
            },
            "refusal_rate": {
                **_wilson(judgment_statuses["refusal"], scheduled_judgments),
                "formula": "retained_refusal / scheduled_judgments",
                "counted_as_judgment_error": False,
            },
            "malformed_response_rate": {
                **_wilson(
                    judgment_statuses["malformed_response"], scheduled_judgments
                ),
                "formula": "retained_malformed_response / scheduled_judgments",
                "counted_as_judgment_error": False,
            },
            "cli_or_service_error_rate": {
                **_wilson(
                    judgment_statuses["cli_or_service_error"],
                    scheduled_judgments,
                ),
                "formula": "retained_cli_or_service_error / scheduled_judgments",
                "counted_as_judgment_error": False,
            },
            "quota_window_interruption_count": judgment_statuses[
                "quota_window_interruption"
            ],
            "completed_nonbinary_judgments": judgment_verdicts["unknown"]
            + judgment_verdicts["invalid"],
            "source_location_schema_validity": {
                **_wilson(location_schema_valid, completed_judgments),
                "formula": "schema-valid source-location field / completed judgments",
            },
            "source_location_reporting_rate": {
                **_wilson(location_reported, completed_judgments),
                "formula": "completed judgments with at least one source span / completed judgments",
                "source_location_span_count": location_spans,
            },
        }
        result["llm_judgment_accounting"]["completed_binary_judgment_accuracy"] = dict(
            result["llm_judgment_accounting"]["conditional_judgment_accuracy"]
        )
    return result


def analyze_all_methods(
    *,
    admitted_program_ids: Sequence[str],
    oracle_labels: Mapping[str, str],
    records_by_method: Mapping[str, Iterable[Mapping[str, Any]]],
    oracle_outcomes: Mapping[str, str] | None = None,
    generator_family_by_program: Mapping[str, str] | None = None,
    property_stratum_by_program: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    if set(records_by_method) != set(method_ids()):
        raise ValueError("records must contain full method plus exactly three baselines")
    indexed = {
        method_id: _index_method_records(records_by_method[method_id], method_id)
        for method_id in method_ids()
    }
    result = {
        "evidence_status": DERIVED_STATUS,
        "schema": "bisafecode.stage4.controlled.metric-summary/v1",
        "paper_result_eligible": False,
        "methods": {
            method_id: analyze_method(
                method_id=method_id,
                admitted_program_ids=admitted_program_ids,
                oracle_labels=oracle_labels,
                method_records=indexed[method_id].values(),
                oracle_outcomes=oracle_outcomes,
            )
            for method_id in method_ids()
        },
        "paired_analysis": analyze_paired_differences(
            oracle_labels=oracle_labels,
            indexed_records_by_method=indexed,
        ),
    }
    for output_key, grouping in (
        ("by_generator_family", generator_family_by_program),
        ("by_property_stratum", property_stratum_by_program),
    ):
        if grouping is None:
            continue
        if set(grouping) != set(admitted_program_ids):
            raise ValueError(f"{output_key} must cover the admitted population exactly")
        result[output_key] = {}
        for group in sorted(set(grouping.values())):
            group_ids = tuple(
                program_id
                for program_id in admitted_program_ids
                if grouping[program_id] == group
            )
            group_labels = {
                program_id: oracle_labels[program_id]
                for program_id in group_ids
                if program_id in oracle_labels
            }
            group_outcomes = {
                program_id: (oracle_outcomes or oracle_labels)[program_id]
                for program_id in group_ids
                if program_id in (oracle_outcomes or oracle_labels)
            }
            result[output_key][group] = {
                method_id: analyze_method(
                    method_id=method_id,
                    admitted_program_ids=group_ids,
                    oracle_labels=group_labels,
                    method_records=(
                        record
                        for program_id, record in indexed[method_id].items()
                        if program_id in set(group_ids)
                    ),
                    oracle_outcomes=group_outcomes,
                )
                for method_id in method_ids()
            }
    return result


PAIRED_METRICS = (
    "coverage",
    "unknown_rate",
    "invalid_rate",
    "unsafe_recall",
    "false_safe_rate",
    "false_alarm_rate",
    "safe_acceptance",
    "overall_correct",
    "agreement_on_decided",
)


def _metric_ratio(
    metric: str,
    program_ids: Sequence[str],
    labels: Mapping[str, str],
    records: Mapping[str, Mapping[str, Any]],
) -> tuple[Optional[float], int, int]:
    numerator = 0
    denominator = 0
    for program_id in program_ids:
        label = labels[program_id]
        record = records.get(program_id)
        decided = bool(
            record
            and record.get("status") == "ok"
            and record.get("verdict") in {"verified-within-bounds", "violated"}
        )
        verdict = record.get("verdict") if record and record.get("status") == "ok" else None
        correct = (label == "safe" and verdict == "verified-within-bounds") or (
            label == "unsafe" and verdict == "violated"
        )
        if metric == "coverage":
            denominator += 1
            numerator += decided
        elif metric == "unknown_rate":
            denominator += 1
            numerator += verdict == "unknown"
        elif metric == "invalid_rate":
            denominator += 1
            numerator += verdict == "invalid"
        elif metric in {"unsafe_recall", "false_safe_rate"} and label == "unsafe":
            denominator += 1
            numerator += verdict == ("violated" if metric == "unsafe_recall" else "verified-within-bounds")
        elif metric in {"false_alarm_rate", "safe_acceptance"} and label == "safe":
            denominator += 1
            numerator += verdict == ("violated" if metric == "false_alarm_rate" else "verified-within-bounds")
        elif metric == "overall_correct":
            denominator += 1
            numerator += correct
        elif metric == "agreement_on_decided" and decided:
            denominator += 1
            numerator += correct
    return (None if denominator == 0 else numerator / denominator, numerator, denominator)


def _paired_one(
    metric: str,
    *,
    baseline: str,
    program_ids: Sequence[str],
    labels: Mapping[str, str],
    full_records: Mapping[str, Mapping[str, Any]],
    baseline_records: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    full, full_num, full_den = _metric_ratio(metric, program_ids, labels, full_records)
    other, other_num, other_den = _metric_ratio(metric, program_ids, labels, baseline_records)
    if full is None or other is None:
        return {
            "full_numerator": full_num,
            "full_denominator": full_den,
            "baseline_numerator": other_num,
            "baseline_denominator": other_den,
            "difference_full_minus_baseline": None,
            "ci_low": None,
            "ci_high": None,
            "zero_denominator": True,
        }
    differences = []
    count = len(program_ids)
    for replicate in range(BOOTSTRAP_REPLICATES):
        sample = []
        for draw in range(count):
            digest = hashlib.sha256(
                f"{BOOTSTRAP_SEED}:{baseline}:{metric}:{replicate}:{draw}".encode("ascii")
            ).digest()
            sample.append(program_ids[int.from_bytes(digest[:8], "big") % count])
        sampled_full, _num, _den = _metric_ratio(metric, sample, labels, full_records)
        sampled_other, _num2, _den2 = _metric_ratio(metric, sample, labels, baseline_records)
        if sampled_full is not None and sampled_other is not None:
            differences.append(sampled_full - sampled_other)
    return {
        "full_numerator": full_num,
        "full_denominator": full_den,
        "baseline_numerator": other_num,
        "baseline_denominator": other_den,
        "difference_full_minus_baseline": full - other,
        "ci_low": _quantile(differences, 0.025),
        "ci_high": _quantile(differences, 0.975),
        "bootstrap_valid_replicates": len(differences),
        "zero_denominator": not differences,
    }


def analyze_paired_differences(
    *,
    oracle_labels: Mapping[str, str],
    indexed_records_by_method: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Mapping[str, Any]:
    """Compute actual program-level paired differences and 10,000-replicate CIs."""

    if set(indexed_records_by_method) != set(method_ids()):
        raise ValueError("paired analysis requires all four methods")
    program_ids = tuple(sorted(oracle_labels))
    comparisons = {}
    full = indexed_records_by_method["bisafecode_full"]
    for baseline in method_ids()[1:]:
        comparisons[f"bisafecode_full-minus-{baseline}"] = {
            metric: _paired_one(
                metric,
                baseline=baseline,
                program_ids=program_ids,
                labels=oracle_labels,
                full_records=full,
                baseline_records=indexed_records_by_method[baseline],
            )
            for metric in PAIRED_METRICS
        }
    return {
        "unit": "program_id",
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "confidence_level": CONFIDENCE_LEVEL,
        "rule": "resample program IDs and keep all four method outcomes together",
        "comparisons": comparisons,
    }
