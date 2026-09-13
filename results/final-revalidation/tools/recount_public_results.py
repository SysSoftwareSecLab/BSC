#!/usr/bin/env python3
"""Recompute the portable cohort counts from reviewer-facing records."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


COHORTS = {
    "Controlled-60": ("controlled-60", 60),
    "External-40": ("external-40", 40),
    "External-25": ("external-25", 25),
    "Rare-40": ("rare-40", 40),
}
EXPECTED_PUBLIC_ROOT = "28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise AssertionError(f"invalid JSON: {path}:{line_number}: {exc}") from exc
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    results_root = root / "results" / "final-revalidation" / "cohorts"
    summary = json.loads((results_root / "summary.json").read_text(encoding="utf-8"))
    build_manifest = json.loads(
        (root / "final-build" / "BUILD_MANIFEST.json").read_text(encoding="utf-8")
    )
    require(
        build_manifest["public_projection"]["root_sha256"] == EXPECTED_PUBLIC_ROOT,
        "build-manifest public projection root mismatch",
    )
    require(
        summary["public_projection_root_sha256"] == EXPECTED_PUBLIC_ROOT,
        "result public-projection root mismatch",
    )
    public_environment = root / "environment" / "mac" / "environment.json"
    require(public_environment.is_file(), "public Mac environment record is missing")
    require(
        summary.get("public_environment_sha256") == sha256(public_environment),
        "result public-environment identity mismatch",
    )
    require(
        "environment_identity_sha256" not in summary,
        "omitted private environment identity must not be published",
    )

    total_verdicts: Counter[str] = Counter()
    total_cases = 0
    total_oracle_matches = 0
    total_reference_matches = 0

    for cohort, (slug, expected_count) in COHORTS.items():
        result_rows = read_jsonl(results_root / f"{slug}.jsonl")
        manifest_rows = read_jsonl(root / "datasets" / slug / "cohort_manifest.jsonl")
        oracle_rows = read_jsonl(root / "datasets" / slug / "oracle_labels.jsonl")

        require(len(result_rows) == expected_count, f"{cohort}: result count")
        require(len(manifest_rows) == expected_count, f"{cohort}: manifest count")
        require(len(oracle_rows) == expected_count, f"{cohort}: oracle count")

        manifests = {row["case_id"]: row for row in manifest_rows}
        oracles = {row["case_id"]: row for row in oracle_rows}
        require(len(manifests) == expected_count, f"{cohort}: duplicate manifest case")
        require(len(oracles) == expected_count, f"{cohort}: duplicate oracle case")

        seen: set[str] = set()
        verdicts: Counter[str] = Counter()
        deployments: Counter[str] = Counter()
        oracle_matches = 0
        reference_matches = 0
        trace_records = 0
        qualified_counterexamples = 0
        unresolved_diagnostic_traces = 0

        for row in result_rows:
            case_id = row["case_id"]
            require(case_id not in seen, f"{cohort}: duplicate result {case_id}")
            seen.add(case_id)
            require(row["cohort"] == cohort, f"{case_id}: cohort mismatch")
            require(case_id in manifests and case_id in oracles, f"{case_id}: missing cross-record")

            program_path = root / row["program_path"]
            require(program_path.is_file(), f"{case_id}: missing program")
            source_digest = sha256(program_path)
            require(source_digest == row["source_sha256"], f"{case_id}: result source hash")
            require(source_digest == manifests[case_id]["source_sha256"], f"{case_id}: manifest source hash")
            require(source_digest == oracles[case_id]["source_sha256"], f"{case_id}: oracle source hash")
            require(row["oracle_label"] == oracles[case_id]["oracle_label"], f"{case_id}: oracle label")

            implied = "verified-within-bounds" if row["oracle_label"] == "safe" else "violated"
            require(
                oracles[case_id]["oracle_implied_binary_verdict"] == implied,
                f"{case_id}: oracle-implied binary verdict",
            )

            verdict = row["verifier_verdict"]
            decision = row["deployment_decision"]
            expected_decision = "RELEASE" if verdict == "verified-within-bounds" else "BLOCK"
            require(decision == expected_decision, f"{case_id}: fail-closed decision mismatch")
            require(row["oracle_consumed_by_method"] is False,
                    f"{case_id}: oracle-consumption boundary")
            require(row["source_identity_status"] == "PASS",
                    f"{case_id}: source identity status")

            computed_oracle_match = verdict == implied
            require(
                row["binary_oracle_match"] == computed_oracle_match,
                f"{case_id}: stored binary-oracle match disagrees with recomputation",
            )
            computed_reference_match = verdict == row["frozen_reference_verdict"]
            require(
                row["verdict_match_to_frozen_reference"] == computed_reference_match,
                f"{case_id}: stored frozen-reference match disagrees with recomputation",
            )

            trace = row.get("counterexample", {})
            if trace.get("present"):
                trace_records += 1
                if verdict == "violated":
                    require(
                        trace.get("qualification") == "qualified_violation_counterexample",
                        f"{case_id}: qualified counterexample label",
                    )
                    qualified_counterexamples += 1
                elif verdict == "unknown":
                    require(
                        trace.get("qualification") == "unresolved_diagnostic_trace",
                        f"{case_id}: unresolved diagnostic trace label",
                    )
                    unresolved_diagnostic_traces += 1
                else:
                    raise AssertionError(f"{case_id}: trace attached to {verdict}")

            verdicts[verdict] += 1
            deployments[decision] += 1
            oracle_matches += int(computed_oracle_match)
            reference_matches += int(computed_reference_match)

        reported = summary["cohorts"][cohort]
        require(dict(verdicts) == reported["verdict_counts"], f"{cohort}: verdict summary")
        require(dict(deployments) == reported["deployment_counts"], f"{cohort}: decision summary")
        require(oracle_matches == reported["oracle_matches"], f"{cohort}: oracle summary")
        require(reference_matches == reported["verdict_matches"], f"{cohort}: reference summary")
        require(trace_records == reported["trace_evidence_records"], f"{cohort}: trace summary")
        require(
            qualified_counterexamples == reported["qualified_violation_counterexamples"],
            f"{cohort}: qualified counterexample summary",
        )
        require(
            unresolved_diagnostic_traces == reported["unresolved_diagnostic_traces"],
            f"{cohort}: unresolved diagnostic trace summary",
        )
        require(seen == set(manifests) == set(oracles),
                f"{cohort}: result/frozen identity set mismatch")

        total_cases += len(result_rows)
        total_oracle_matches += oracle_matches
        total_reference_matches += reference_matches
        total_verdicts.update(verdicts)
        print(f"PASS {cohort}: {len(result_rows)} cases; {dict(verdicts)}")

    totals = summary["totals"]
    require(total_cases == totals["executed_N"] == totals["selected_N"], "total case count")
    require(total_oracle_matches == totals["oracle_matches"], "total oracle agreement")
    require(total_reference_matches == totals["verdict_matches"], "total reference agreement")
    require(dict(total_verdicts) == totals["verdict_counts"], "total verdict summary")

    print(
        "PASS TOTAL: "
        f"{total_reference_matches}/{total_cases} frozen-verdict agreement; "
        f"{total_oracle_matches}/{total_cases} binary-oracle agreement; "
        f"{dict(total_verdicts)}"
    )


if __name__ == "__main__":
    main()
