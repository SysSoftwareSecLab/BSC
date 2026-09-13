#!/usr/bin/env python3
"""Read-only recount of the public baseline evidence.

The script deliberately distinguishes row-recomputed results from compact
aggregate-only evidence.  It never executes a verifier, baseline, model, or
oracle and it does not rewrite historical result/status fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
BASELINES = ARTIFACT_ROOT / "baselines"
DATASETS = ARTIFACT_ROOT / "datasets"
SHA256_HEX = set("0123456789abcdef")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, observed {actual!r}")


def require_close(actual: Any, expected: Any, label: str) -> None:
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        fail(f"{label}: expected numeric values")
    if not math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12):
        fail(f"{label}: expected {expected!r}, observed {actual!r}")


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA256_HEX


def check_sha256(value: Any, label: str) -> None:
    if not is_sha256(value):
        fail(f"{label} is not a lowercase SHA-256")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ARTIFACT_ROOT)}: {exc}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    fail(f"blank JSONL line in {path.relative_to(ARTIFACT_ROOT)}:{line_number}")
                row = json.loads(line)
                if not isinstance(row, dict):
                    fail(f"non-object JSONL row in {path.relative_to(ARTIFACT_ROOT)}:{line_number}")
                rows.append(row)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ARTIFACT_ROOT)}: {exc}")
    return rows


def safe_artifact_file(relative: str, label: str) -> Path:
    if not isinstance(relative, str):
        fail(f"{label} is not a path string")
    candidate = (ARTIFACT_ROOT / relative).resolve()
    try:
        candidate.relative_to(ARTIFACT_ROOT.resolve())
    except ValueError:
        fail(f"{label} escapes the artifact root: {relative!r}")
    if not candidate.is_file():
        fail(f"{label} is missing: {relative!r}")
    return candidate


def index_unique(rows: Iterable[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str):
            fail(f"{label} row lacks string {key}")
        if value in indexed:
            fail(f"duplicate {label} {key}: {value}")
        indexed[value] = row
    return indexed


def counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def binary_prediction(value: Any) -> str | None:
    if value in {"safe", "verified-within-bounds"}:
        return "safe"
    if value in {"unsafe", "violated"}:
        return "unsafe"
    return None


def dataset_index(name: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    cohort_root = DATASETS / name
    manifests = index_unique(load_jsonl(cohort_root / "cohort_manifest.jsonl"), "case_id", f"{name} manifest")
    oracles = index_unique(load_jsonl(cohort_root / "oracle_labels.jsonl"), "case_id", f"{name} oracle")
    require(set(manifests), set(oracles), f"{name} manifest/oracle identities")
    for case_id, manifest in manifests.items():
        oracle = oracles[case_id]
        require(oracle.get("source_sha256"), manifest.get("source_sha256"), f"{name} {case_id} oracle/manifest source hash")
        source = safe_artifact_file(manifest.get("program_path"), f"{name} {case_id} source")
        require(sha256_file(source), manifest.get("source_sha256"), f"{name} {case_id} source bytes")
    return manifests, oracles


def method_outcome(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = 0
    predictions: Counter[str] = Counter()
    for row in rows:
        record = row["methods"][method]
        require(record.get("status"), "ok", f"{row['case_id']} {method} status")
        prediction = record.get("prediction")
        predictions[str(prediction)] += 1
        row_correct = binary_prediction(prediction) == row["oracle"]
        if "correct" in record:
            require(record["correct"], row_correct, f"{row['case_id']} {method} correct flag")
        correct += row_correct
    return {"N": len(rows), "correct": correct, "prediction_counts": dict(sorted(predictions.items()))}


def controlled_method_outcome(
    rows: list[dict[str, Any]],
    oracles: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    *,
    prediction_key: str,
) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    correct = false_alarm = false_safe = unknown_or_invalid = 0
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        case_id = row["program_id"]
        oracle = oracles[case_id]["oracle_label"]
        prediction = row[prediction_key]
        predicted = binary_prediction(prediction)
        family = manifests[case_id]["family_id"]
        by_family[family]["N"] += 1
        is_correct = predicted == oracle
        correct += is_correct
        by_family[family]["correct"] += is_correct
        is_false_alarm = oracle == "safe" and predicted == "unsafe"
        is_false_safe = oracle == "unsafe" and predicted == "safe"
        false_alarm += is_false_alarm
        false_safe += is_false_safe
        by_family[family]["false_alarm"] += is_false_alarm
        by_family[family]["false_safe"] += is_false_safe
        if predicted is None:
            unknown_or_invalid += 1
    normalized_family = {
        family: {key: int(values[key]) for key in ("N", "correct", "false_alarm", "false_safe")}
        for family, values in sorted(by_family.items())
    }
    return (
        {
            "N": len(rows),
            "correct": correct,
            "false_alarm": false_alarm,
            "false_safe": false_safe,
            "unknown_or_invalid_predictions": unknown_or_invalid,
        },
        normalized_family,
    )


def audit_rq1() -> dict[str, Any]:
    root = BASELINES / "rq1-runtime-and-gpt"
    results = root / "results"
    reported = load_json(root / "reported-results.json")
    controlled_summary = load_json(results / "controlled-60-summary.json")
    manifests, oracles = dataset_index("controlled-60")

    independent_rows = load_jsonl(results / "controlled-60-independent-records.jsonl")
    gpt_rows = load_jsonl(results / "controlled-60-gpt-records.jsonl")
    independent = index_unique(independent_rows, "program_id", "Controlled-60 independent")
    gpt = index_unique(gpt_rows, "program_id", "Controlled-60 GPT")
    require(set(independent), set(oracles), "Controlled-60 independent identities")
    require(set(gpt), set(oracles), "Controlled-60 GPT identities")

    for case_id in sorted(oracles):
        for method_name, row in (("independent", independent[case_id]), ("GPT", gpt[case_id])):
            require(row.get("status"), "ok", f"Controlled-60 {case_id} {method_name} status")
            require(row.get("source_sha256"), oracles[case_id]["source_sha256"], f"Controlled-60 {case_id} {method_name} source hash")
        row = independent[case_id]
        require(row.get("scheduled_rollouts"), 100, f"Controlled-60 {case_id} scheduled rollouts")
        require(row.get("completed_rollouts"), 100, f"Controlled-60 {case_id} completed rollouts")
        require(row.get("formal_engine_or_property_checker_reused"), False, f"Controlled-60 {case_id} independence flag")
        require(row.get("no_exhaustive_claim"), True, f"Controlled-60 {case_id} sampling boundary")

    independent_outcome, independent_families = controlled_method_outcome(
        independent_rows, oracles, manifests, prediction_key="prediction"
    )
    gpt_outcome, gpt_families = controlled_method_outcome(
        gpt_rows, oracles, manifests, prediction_key="prediction"
    )
    require(independent_outcome, {"N": 60, "correct": 60, "false_alarm": 0, "false_safe": 0, "unknown_or_invalid_predictions": 0}, "Controlled-60 independent recount")
    require(gpt_outcome, {"N": 60, "correct": 58, "false_alarm": 2, "false_safe": 0, "unknown_or_invalid_predictions": 0}, "Controlled-60 GPT recount")

    for key, outcome, families in (
        ("independent_runtime_at_100", independent_outcome, independent_families),
        ("gpt_5_5_semantic_control", gpt_outcome, gpt_families),
    ):
        summary = controlled_summary[key]
        for field, value in outcome.items():
            require(summary[field], value, f"Controlled-60 summary {key} {field}")
        require(summary["per_family"], families, f"Controlled-60 summary {key} per-family")

    scheduled_judgments = completed_judgments = correct_completed_judgments = timeouts = 0
    for row in gpt_rows:
        aggregation = row["aggregation"]
        require(aggregation.get("retry_count"), 0, f"Controlled-60 {row['program_id']} GPT retry count")
        require(aggregation.get("timeout_is_not_judgment_error"), True, f"Controlled-60 {row['program_id']} timeout policy")
        scheduled_judgments += aggregation["scheduled_count"]
        completed_judgments += aggregation["judgment_status_counts"].get("completed", 0)
        timeouts += aggregation["judgment_status_counts"].get("timeout", 0)
        oracle = oracles[row["program_id"]]["oracle_label"]
        expected = "verified-within-bounds" if oracle == "safe" else "violated"
        correct_completed_judgments += aggregation["verdict_counts"].get(expected, 0)
    accounting = controlled_summary["gpt_5_5_semantic_control"]["judgment_accounting"]
    require((scheduled_judgments, completed_judgments, correct_completed_judgments, timeouts), (300, 299, 279, 1), "Controlled-60 GPT judgment recount")
    require(accounting["completed"], completed_judgments, "Controlled-60 GPT summary completed judgments")
    require(accounting["completed_correct"], correct_completed_judgments, "Controlled-60 GPT summary correct judgments")
    require(accounting["timeout"], timeouts, "Controlled-60 GPT summary timeout judgments")
    require_close(accounting["conditional_completed_accuracy"], 279 / 299, "Controlled-60 GPT conditional accuracy")

    require(reported["Controlled-60"]["independent_runtime_at_100"], {"N": 60, "correct": 60}, "reported Controlled-60 independent")
    require(reported["Controlled-60"]["gpt_5_5_semantic_control"], {"N": 60, "correct": 58}, "reported Controlled-60 GPT")

    external_report: dict[str, Any] = {}
    for cohort, expected_total, expected_valid, expected_invalid in (
        ("External-40", 40, 40, 0),
        ("External-25", 29, 25, 4),
    ):
        slug = cohort.lower()
        rows = load_jsonl(results / f"{slug}-per-instance.jsonl")
        indexed = index_unique(rows, "case_id", f"{cohort} baseline")
        valid = [row for row in rows if row.get("oracle") in {"safe", "unsafe"}]
        invalid = [row for row in rows if row.get("oracle") == "invalid"]
        require((len(rows), len(valid), len(invalid)), (expected_total, expected_valid, expected_invalid), f"{cohort} denominator")

        cohort_slug = cohort.lower()
        dataset_manifests, dataset_oracles = dataset_index(cohort_slug)
        require({row["case_id"] for row in valid}, set(dataset_oracles), f"{cohort} valid result/dataset identities")
        for row in valid:
            case_id = row["case_id"]
            require(row["oracle"], dataset_oracles[case_id]["oracle_label"], f"{cohort} {case_id} oracle")
            manifest_origin = dataset_manifests[case_id]["source_origin"]
            normalized_origin = (
                "independent_human"
                if str(manifest_origin).startswith("independent_human")
                else manifest_origin
            )
            require(row.get("source_type"), normalized_origin, f"{cohort} {case_id} source type")
            if cohort == "External-25":
                require(row.get("property"), dataset_manifests[case_id]["property"], f"{cohort} {case_id} property")

        methods: dict[str, Any] = {}
        for method in ("independent_runtime_at_100", "gpt_5_5_semantic_control", "untimed_external"):
            outcome = method_outcome(valid, method)
            methods[method] = outcome
            reported_method = reported[cohort][method]
            require(reported_method["N"], outcome["N"], f"reported {cohort} {method} N")
            require(reported_method["correct"], outcome["correct"], f"reported {cohort} {method} correct")
            require(reported_method["prediction_counts"], outcome["prediction_counts"], f"reported {cohort} {method} prediction counts")
        external_report[cohort] = {
            "rows_retained": len(rows),
            "valid_scored": len(valid),
            "oracle_invalid_retained_unscored": len(invalid),
            "methods": methods,
        }

    require(external_report["External-40"]["methods"]["independent_runtime_at_100"]["correct"], 40, "External-40 independent correct")
    require(external_report["External-40"]["methods"]["gpt_5_5_semantic_control"]["correct"], 36, "External-40 GPT correct")
    require(external_report["External-40"]["methods"]["untimed_external"]["correct"], 18, "External-40 untimed correct")
    require(external_report["External-25"]["methods"]["independent_runtime_at_100"]["correct"], 25, "External-25 independent correct")
    require(external_report["External-25"]["methods"]["gpt_5_5_semantic_control"]["correct"], 24, "External-25 GPT correct")
    require(external_report["External-25"]["methods"]["untimed_external"]["correct"], 10, "External-25 untimed correct")

    return {
        "status": "PASS",
        "evidence_mode": "row_recomputed",
        "Controlled-60": {
            "independent_runtime_at_100": "60/60",
            "gpt_5_5_semantic_control": "58/60",
            "program_rows_each_method": 60,
            "gpt_judgments_completed": "299/300",
        },
        **external_report,
        "boundary": "sampling nondetection is not a certificate; GPT is a semantic-diagnosis control",
    }


def audit_ir_sampler() -> tuple[dict[str, Any], dict[str, Any]]:
    root = BASELINES / "ir-sampler" / "results"
    rows = load_jsonl(root / "ordinary65-per-program.jsonl")
    summary = load_json(root / "summary.json")
    rare = load_json(root / "rare40-summary.json")
    require(len(rows), 65, "IR ordinary row count")
    indexed = index_unique(rows, "case_id", "IR ordinary")

    external40_manifests, external40_oracles = dataset_index("external-40")
    external25_manifests, external25_oracles = dataset_index("external-25")
    dataset_manifests = {**external40_manifests, **external25_manifests}
    dataset_oracles = {**external40_oracles, **external25_oracles}
    require(set(indexed), set(dataset_oracles), "IR ordinary/dataset identities")

    budgets = ("10", "30", "100", "300", "1000")
    recounted: dict[str, dict[str, int]] = {}
    changed_ids: set[str] = set()
    stale_legacy_ids: set[str] = set()
    recompute_modes: Counter[str] = Counter()
    for case_id, row in indexed.items():
        require(row.get("source_program_path"), dataset_manifests[case_id]["program_path"], f"IR {case_id} source path")
        source = safe_artifact_file(row["source_program_path"], f"IR {case_id} source")
        require(sha256_file(source), row.get("source_sha256"), f"IR {case_id} source bytes")
        require(row.get("source_sha256"), dataset_manifests[case_id]["source_sha256"], f"IR {case_id} dataset source hash")
        require(row.get("oracle"), dataset_oracles[case_id]["oracle_label"], f"IR {case_id} oracle")
        check_sha256(row.get("corrected_adapter_sha256"), f"IR {case_id} adapter hash")
        identity = row.get("semantics_identity")
        if not isinstance(identity, dict):
            fail(f"IR {case_id} semantics identity absent")
        require(identity.get("source_sha256"), row["source_sha256"], f"IR {case_id} semantics source hash")
        for key in ("program_sha256", "resolved_environment_sha256", "interval_checker_identity_sha256", "point_checker_identity_sha256"):
            check_sha256(identity.get(key), f"IR {case_id} semantics {key}")
        recompute_modes[str(row.get("recompute_mode"))] += 1

        if row.get("historical_bisafecode_release_verdict") != row.get("bisafecode_release_verdict"):
            changed_ids.add(case_id)
        require(set(row.get("prefixes", {})), set(budgets), f"IR {case_id} budget keys")
        all_prefixes_negative = True
        for budget in budgets:
            prefix = row["prefixes"][budget]
            require(prefix.get("rollouts"), int(budget), f"IR {case_id} budget {budget} rollout count")
            require(prefix.get("is_certificate"), False, f"IR {case_id} budget {budget} certificate boundary")
            outcome_counts = prefix.get("outcome_counts")
            if not isinstance(outcome_counts, dict) or sum(outcome_counts.values()) != int(budget):
                fail(f"IR {case_id} budget {budget} outcome count mismatch")
            if prefix.get("violation_observed"):
                all_prefixes_negative = False
        if (
            all_prefixes_negative
            and row.get("first_detection_rollout") is not None
            and "sampling-violation-observed" in str(row.get("paired_result_at_1000"))
        ):
            stale_legacy_ids.add(case_id)

    require(recompute_modes, Counter({"affected_source_full_1000_paired_replay": 36, "unaffected_source_immutable_prefix_reuse": 29}), "IR recompute modes")
    summary_changed = {item["case_id"] for item in summary["changed_cases"]}
    require(changed_ids, summary_changed, "IR corrected release cases")
    require(stale_legacy_ids, changed_ids, "IR disclosed stale legacy fields")
    require(len(changed_ids), 2, "IR corrected case count")
    require(summary["status"], "COMPLETE_PENDING_COUNTER_AUDIT", "IR historical summary status")
    require(summary["boundary"]["paper_numbers_updated"], False, "IR historical paper-number flag")

    for budget in budgets:
        values = Counter()
        for row in rows:
            if row["bisafecode_release_verdict"] == "unknown":
                category = "unresolved_and_blocked"
            elif row["prefixes"][budget]["violation_observed"]:
                category = "violation_observed"
            else:
                category = "no_violation_observed_not_certificate"
            values[category] += 1
        recounted[budget] = dict(sorted(values.items()))
        require(recounted[budget], summary["ordinary_65_at_each_budget"][budget], f"IR ordinary budget {budget}")
        require(recounted[budget], {"no_violation_observed_not_certificate": 39, "unresolved_and_blocked": 2, "violation_observed": 24}, f"IR ordinary expected budget {budget}")

    require(counter_dict(row["population"] for row in rows), {"external_25": 25, "external_40": 40}, "IR ordinary populations")
    require(summary["population"]["ordinary_total"], 65, "IR ordinary summary total")
    require(summary["population"]["affected_replayed"], 36, "IR affected replay count")
    require(summary["population"]["unaffected_reused"], 29, "IR unaffected reuse count")
    require(summary["population"]["affected_rollouts"], 36000, "IR affected rollout count")
    require({row["corrected_adapter_sha256"] for row in rows}, {summary["input_identity"]["corrected_adapter_sha256"]}, "IR adapter identity")

    ordinary_report = {
        "status": "PASS",
        "evidence_mode": "row_recomputed",
        "rows": 65,
        "budgets": recounted,
        "changed_cases": sorted(changed_ids),
        "legacy_field_notice": (
            "The two declared changed cases retain historical first_detection_rollout and paired_result_at_1000 "
            "values. Corrected counts are recomputed from prefixes plus bisafecode_release_verdict; the legacy "
            "fields are not used."
        ),
        "historical_status_retained": summary["status"],
        "historical_paper_numbers_updated_flag": summary["boundary"]["paper_numbers_updated"],
    }

    require(rare["cohort"], "Rare-40", "IR rare cohort")
    require(rare["exact_exploration"], {"N": 40, "unknown": 0, "verified_within_bounds": 8, "violated": 32}, "IR rare exact aggregate")
    require(rare["ir_sampler"]["budgets"], [10, 30, 100, 300, 1000], "IR rare budgets")
    require(rare["ir_sampler"]["violation_observed"], [15, 19, 23, 26, 29], "IR rare sampler aggregate")
    require(rare["ir_sampler"]["violation_not_observed_at_1000"], 11, "IR rare nondetection aggregate")
    require(rare["ir_sampler"]["exact_search_violations_not_observed_at_1000"], 3, "IR rare exact-missed aggregate")
    require(rare["ir_sampler"]["unresolved_at_1000"], 0, "IR rare unresolved aggregate")
    require(summary["population"]["rare_40_unaffected_and_not_rerun"], True, "IR rare not-rerun boundary")
    rare_report = {
        "status": "PARTIAL",
        "evidence_mode": "aggregate_only",
        "N": 40,
        "exact_exploration": rare["exact_exploration"],
        "violation_observed_by_budget": dict(zip(map(str, rare["ir_sampler"]["budgets"]), rare["ir_sampler"]["violation_observed"])),
        "boundary": "the 40 per-program rare rows and 36,000 rollout rows are not included; aggregate arithmetic only",
    }
    return ordinary_report, rare_report


def audit_spin() -> dict[str, Any]:
    root = BASELINES / "spin-translation-consistency"
    rows = load_jsonl(root / "results" / "per-instance-results.jsonl")
    summary = load_json(root / "results" / "summary.json")
    indexed = index_unique(rows, "case_id", "SPIN")
    require(len(rows), 27, "SPIN row count")
    model_files = {path.stem: path for path in (root / "models").glob("*.pml")}
    require(set(model_files), set(indexed), "SPIN model/case identities")

    for case_id, row in indexed.items():
        source = safe_artifact_file(row["source_program_path"], f"SPIN {case_id} source")
        require(sha256_file(source), row["source_sha256"], f"SPIN {case_id} source hash")
        require(sha256_file(model_files[case_id]), row["promela_sha256"], f"SPIN {case_id} model hash")
        expected_verdict = "verified-within-bounds" if row["oracle"] == "safe" else "violated"
        require(row["spin_verdict"], expected_verdict, f"SPIN {case_id} verdict")
        require(row["spin_matches_oracle"], True, f"SPIN {case_id} match flag")
        require(row["corrected_bisafecode"], expected_verdict, f"SPIN {case_id} BiSafeCode comparison")
        require(row["spin"]["returncode"], 0, f"SPIN {case_id} return code")
        require(row["spin"]["errors"], 0 if row["oracle"] == "safe" else 1, f"SPIN {case_id} errors")

    oracle_counts = counter_dict(row["oracle"] for row in rows)
    verdict_counts = counter_dict(row["spin_verdict"] for row in rows)
    population_counts = counter_dict(row["population"] for row in rows)
    require(oracle_counts, {"safe": 17, "unsafe": 10}, "SPIN oracle counts")
    require(verdict_counts, {"verified-within-bounds": 17, "violated": 10}, "SPIN verdict counts")
    require(population_counts, {"External-25": 13, "External-40": 14}, "SPIN population counts")
    require(summary["subset"]["N"], 27, "SPIN summary N")
    require(summary["subset"]["oracle_counts"], oracle_counts, "SPIN summary oracle counts")
    require(summary["subset"]["by_population"], population_counts, "SPIN summary population counts")
    require(summary["results"]["correct"], 27, "SPIN summary correct")
    require(summary["results"]["corrected_bisafecode_counts"], verdict_counts, "SPIN summary BiSafeCode counts")
    require(summary["results"]["confusion_matrix"], {"oracle_safe__spin_verified-within-bounds": 17, "oracle_unsafe__spin_violated": 10}, "SPIN confusion matrix")

    executed_translator_identity = summary["tool_identity"]["executed_translator_identity"]
    require(
        executed_translator_identity,
        "WITHHELD_DURING_DOUBLE_ANONYMOUS_REVIEW",
        "SPIN executed translator identity boundary",
    )
    public_runner_sha = sha256_file(root / "source" / "run_spin_translation.py")
    boundary = summary["interpretation_boundary"]
    require(boundary, {
        "collision_geometry_tested": False,
        "cross_tool_runtime_claim_authorized": False,
        "equivalent_to_bisafecode": False,
        "four_valued_abstention_tested": False,
        "timed_parallel_semantics_tested": False,
    }, "SPIN interpretation boundary")

    return {
        "status": "PASS",
        "evidence_mode": "row_and_file_hash_recomputed",
        "rows": 27,
        "oracle_counts": oracle_counts,
        "spin_verdict_counts": verdict_counts,
        "source_hashes_verified": 27,
        "model_hashes_verified": 27,
        "executed_translator_identity": executed_translator_identity,
        "public_runner_sha256": public_runner_sha,
        "public_runner_is_byte_exact_executed_translator": False,
        "runner_boundary": (
            "The included runner is a privacy-redacted inspectable projection. The unsalted digest of the "
            "executed original is withheld during double-anonymous review because it could confirm a guessed "
            "private version-control identifier. Packaged models, source hashes, and per-case results remain "
            "independently recountable."
        ),
        "interpretation_boundary": boundary,
    }


def audit_rq2() -> dict[str, Any]:
    root = BASELINES / "rq2-ablations" / "results"
    rows = load_jsonl(root / "affected-pair-transitions.jsonl")
    summary = load_json(root / "summary.json")
    require(len(rows), 90, "RQ2 affected transition rows")
    pairs: set[tuple[str, str]] = set()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_rows_verified = 0
    source_ids_verified: set[str] = set()
    source_ids_unavailable: set[str] = set()
    controlled_programs = {path.stem: path for path in (DATASETS / "controlled-60" / "programs").glob("*.py")}

    for row in rows:
        pair = (row["ablation"], row["program_id"])
        if pair in pairs:
            fail(f"duplicate RQ2 ablation/program pair: {pair}")
        pairs.add(pair)
        groups[row["ablation"]].append(row)
        require(row["full_status"], "ok", f"RQ2 {pair} full status")
        require(row["ablation_status"], "ok", f"RQ2 {pair} ablation status")
        program_id = row["program_id"]
        if program_id in controlled_programs:
            check_sha256(row["source_sha256"], f"RQ2 {pair} source hash")
            require(sha256_file(controlled_programs[program_id]), row["source_sha256"], f"RQ2 {pair} source bytes")
            source_rows_verified += 1
            source_ids_verified.add(program_id)
        else:
            require(
                row.get("source_identity"),
                "WITHHELD_NOT_PUBLICLY_BOUND",
                f"RQ2 {pair} omitted-source boundary",
            )
            require("source_sha256" in row, False, f"RQ2 {pair} private source hash absence")
            source_ids_unavailable.add(program_id)

    expected_sizes = {
        "ablation_non_timed": 60,
        "ablation_separated_state_property": 20,
        "ablation_no_attached_object_geometry": 10,
    }
    require({key: len(value) for key, value in sorted(groups.items())}, dict(sorted(expected_sizes.items())), "RQ2 group sizes")

    recounted: dict[str, Any] = {}
    for ablation, group in sorted(groups.items()):
        oracle_labels = Counter(row["oracle_label"] for row in group)
        categories = Counter(row["paired_category"] for row in group)
        transitions = Counter(
            f"{row['oracle_label']}:{row['full_verdict']}->{row['ablation_verdict']}"
            for row in group
        )
        value = {
            "N_pairs": len(group),
            "full_correct": sum(bool(row["full_correct"]) for row in group),
            "ablation_correct": sum(bool(row["ablation_correct"]) for row in group),
            "full_only_correct": sum(bool(row["full_correct"]) and not bool(row["ablation_correct"]) for row in group),
            "ablation_only_correct": sum(bool(row["ablation_correct"]) and not bool(row["full_correct"]) for row in group),
            "affected_families": sorted({row["family_id"] for row in group}),
            "oracle_labels": dict(sorted(oracle_labels.items())),
            "paired_categories": dict(sorted(categories.items())),
            "verdict_transitions": dict(sorted(transitions.items())),
        }
        recounted[ablation] = value
        require(summary["affected_subset_diagnostics"][ablation], value, f"RQ2 summary {ablation}")

    require(summary["status"], "EVIDENCE_LOCKED_SEMANTIC_NECESSITY_DIAGNOSTIC", "RQ2 summary status")
    return {
        "status": "PASS",
        "evidence_mode": "all_transition_rows_recomputed",
        "rows": len(rows),
        "groups": recounted,
        "source_hash_rows_byte_verified": source_rows_verified,
        "distinct_source_ids_byte_verified": len(source_ids_verified),
        "distinct_source_ids_record_only": len(source_ids_unavailable),
        "source_boundary": (
            "The 20 separated-state source bytes are not included as standalone programs and their private "
            "digests are not published. Their result fields remain recountable. All 60 controlled source "
            "identities are byte-verified."
        ),
        "interpretation_boundary": "pre-specified affected-subset semantic-necessity diagnostics, not population effect sizes",
    }


def wilson95(successes: int, trials: int) -> list[float]:
    # Match the frozen summary's two-sided 95% interval: use the exact
    # standard-normal 97.5th percentile rather than the rounded 1.96.
    z = statistics.NormalDist().inv_cdf(0.975)
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    half = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * trials)) / trials) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def audit_finite_budget() -> tuple[dict[str, Any], dict[str, Any]]:
    root = BASELINES / "finite-budget"
    rows = load_jsonl(root / "results" / "fair_checker_budget_runs.jsonl")
    summary = load_json(root / "results" / "fair_summary.json")
    require(len(rows), 2121, "finite checker-call row count")
    require(summary["checker_row_count"], 2121, "finite summary checker row count")
    require({row["budget_type"] for row in rows}, {"checker_calls"}, "finite included raw budget type")

    groups: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    source_by_workload: dict[str, str] = {}
    program_by_workload: dict[str, str] = {}
    environment_identities: set[str] = set()
    for row in rows:
        workload = row["workload"]
        method = row["method"]
        budget = float(row["budget"])
        groups[(workload, method, budget)].append(row)
        require(row["checker_calls"], row["point_calls"] + row["interval_calls"], f"finite {workload}/{method}/{budget} checker accounting")
        if row["checker_calls"] > row["budget"]:
            fail(f"finite {workload}/{method}/{budget} exceeds checker-call budget")
        source = safe_artifact_file(f"baselines/finite-budget/{row['source_path']}", f"finite {workload} source")
        require(sha256_file(source), row["source_sha256"], f"finite {workload} source hash")
        source_by_workload.setdefault(workload, row["source_sha256"])
        require(source_by_workload[workload], row["source_sha256"], f"finite {workload} source identity consistency")
        program_by_workload.setdefault(workload, row["program_sha256"])
        require(program_by_workload[workload], row["program_sha256"], f"finite {workload} program identity consistency")
        check_sha256(row["program_sha256"], f"finite {workload} program hash")
        require(
            row.get("search_environment_identity"),
            "WITHHELD_NOT_PUBLICLY_BOUND",
            f"finite {workload} search-environment disclosure",
        )
        environment_identities.add(row["search_environment_identity"])
        if not row["detected"]:
            require(row["stop_reason"], "checker-call-budget-exhausted", f"finite {workload}/{method}/{budget} nondetection stop")

    expected_group_keys = {
        (workload, method, float(budget))
        for workload in ("rare2", "rare4", "rare6")
        for method in ("exact-unreduced-bfs", "uniform-random-rollout")
        for budget in (16, 32, 64, 128, 256, 512, 1024)
    }
    require(set(groups), expected_group_keys, "finite checker group identities")
    summary_checker = {
        (item["workload"], item["method"], float(item["budget"])): item
        for item in summary["groups"]
        if item["budget_type"] == "checker_calls"
    }
    require(set(summary_checker), expected_group_keys, "finite summary checker group identities")

    for key, group in sorted(groups.items()):
        workload, method, budget = key
        expected_trials = 1 if method == "exact-unreduced-bfs" else 100
        require(len(group), expected_trials, f"finite {key} trial count")
        require({row["trial"] for row in group}, set(range(1, expected_trials + 1)), f"finite {key} trial identities")
        if method == "uniform-random-rollout":
            require({row["seed"] for row in group}, set(range(1000, 1000 + expected_trials)), f"finite {key} seeds")
        detected = sum(bool(row["detected"]) for row in group)
        expected = summary_checker[key]
        require(expected["trials"], len(group), f"finite {key} summary trials")
        require(expected["detected"], detected, f"finite {key} summary detected")
        require_close(expected["detection_fraction"], detected / len(group), f"finite {key} detection fraction")
        for output_key, row_key in (
            ("median_checker_calls", "checker_calls"),
            ("median_completed_rollouts", "completed_rollouts"),
            ("median_elapsed_ms", "elapsed_ms"),
            ("median_expanded_states", "expanded_states"),
            ("median_successor_candidates", "successor_candidates"),
        ):
            require_close(expected[output_key], statistics.median(row[row_key] for row in group), f"finite {key} {output_key}")
        interval = wilson95(detected, len(group))
        require_close(expected["wilson95"][0], interval[0], f"finite {key} Wilson low")
        require_close(expected["wilson95"][1], interval[1], f"finite {key} Wilson high")

    rare6_exact = groups[("rare6", "exact-unreduced-bfs", 1024.0)]
    rare6_random = groups[("rare6", "uniform-random-rollout", 1024.0)]
    require(len(rare6_exact), 1, "Rare6@1024 exact trials")
    require(sum(bool(row["detected"]) for row in rare6_exact), 0, "Rare6@1024 exact detection")
    require(rare6_exact[0]["checker_calls"], 1024, "Rare6@1024 exact checker calls")
    require(rare6_exact[0]["stop_reason"], "checker-call-budget-exhausted", "Rare6@1024 exact stop")
    require(len(rare6_random), 100, "Rare6@1024 random trials")
    require(sum(bool(row["detected"]) for row in rare6_random), 79, "Rare6@1024 random detection")

    checker_report = {
        "status": "PASS",
        "evidence_mode": "all_checker_call_rows_recomputed",
        "rows": len(rows),
        "groups": len(groups),
        "workloads": sorted(source_by_workload),
        "source_hashes_byte_verified": len(source_by_workload),
        "program_hashes_consistent": len(program_by_workload),
        "search_environment_identities": sorted(environment_identities),
        "Rare6@1024": {
            "exact_unreduced_bfs": "0/1; checker-call-budget-exhausted",
            "uniform_random_rollout": "79/100",
        },
        "boundary": summary["claim_boundary"],
    }

    wall_groups = [item for item in summary["groups"] if item["budget_type"] == "wall_clock_ms"]
    require(len(wall_groups), 30, "finite wall aggregate group count")
    require(sum(item["trials"] for item in wall_groups), summary["wall_row_count"], "finite wall aggregate trial total")
    require(summary["wall_row_count"], 1950, "finite wall aggregate row count")
    wall_report = {
        "status": "PARTIAL",
        "evidence_mode": "aggregate_only",
        "reported_groups": len(wall_groups),
        "reported_rows": summary["wall_row_count"],
        "boundary": "wall-clock raw rows are not included, so reported medians/fractions cannot be independently recomputed from this package",
    }
    return checker_report, wall_report


def main() -> None:
    rq1 = audit_rq1()
    ir_ordinary, ir_rare = audit_ir_sampler()
    spin = audit_spin()
    rq2 = audit_rq2()
    finite_checker, finite_wall = audit_finite_budget()
    components = {
        "rq1_program_level": rq1,
        "ir_sampler_ordinary65": ir_ordinary,
        "ir_sampler_rare40": ir_rare,
        "spin_translation_consistency": spin,
        "rq2_affected_transitions": rq2,
        "finite_budget_checker_calls": finite_checker,
        "finite_budget_wall_clock": finite_wall,
    }
    partial = sorted(name for name, value in components.items() if value["status"] == "PARTIAL")
    report = {
        "schema": "bisafecode.public.baseline-recount/v1",
        "overall_status": "PARTIAL" if partial else "PASS",
        "components": components,
        "partial_components": partial,
        "e07_recommendation": "READY-WITH-BOUNDARY",
        "e07_boundary": (
            "All row-level baseline claims in the compact package recount successfully. Rare-40 sampling and "
            "wall-clock finite-budget statistics remain aggregate-only; SPIN is a restricted translation check; "
            "sampling nondetection is never a certificate."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
