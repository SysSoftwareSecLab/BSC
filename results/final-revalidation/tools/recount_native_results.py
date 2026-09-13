#!/usr/bin/env python3
"""Recount the public Table III, C115, dense, and historical records."""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics


SERVER_BINARY_SHA256 = "a82194d39f0223380bdabddda0f532ab3225e9aaf041072f1670b7a8ea4fce20"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def robot_is_zero(boundary: dict) -> bool:
    return all(not value for value in boundary.values())


def expected_schedule_sort_key(repetition: int, case_id: str) -> str:
    payload = f"EXP-S4-036|formal-block-{repetition}|{case_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def recount_source_build_binding(root: Path) -> dict:
    record = load_json(root / "final-build/SOURCE_BUILD_BINDING.json")
    require(
        record["status"] == "VERIFIED_RECORDED_SOURCE_TO_BINARY_BINDING",
        "native source-build binding status",
    )
    require(len(record["source_files"]) == 3, "native source-build file count")
    for item in record["source_files"]:
        path = root / item["path"]
        require(path.is_file(), f"native source missing: {item['path']}")
        require(path.stat().st_size == item["bytes"], f"native source size: {item['path']}")
        require(sha256_file(path) == item["sha256"], f"native source hash: {item['path']}")
    build = record["recorded_clean_release_build"]
    require(build["configure"]["exit_code"] == 0, "recorded native configure")
    require(build["build"]["exit_code"] == 0, "recorded native build")
    server = build["server_output"]
    require(server["sha256"] == SERVER_BINARY_SHA256, "recorded native server identity")
    require(
        server["byte_identical_to_all_30_table_iii_executed_binary_identities"],
        "recorded source-to-Table-III binary binding",
    )
    require(not record["redistribution"]["compiled_binary_included"], "compiled binary included")
    return {
        "record_status": "PASS",
        "included_source_files": 3,
        "recorded_configure_exit_code": 0,
        "recorded_build_exit_code": 0,
        "recorded_server_sha256": SERVER_BINARY_SHA256,
        "compiled_binary_included": False,
        "interpretation": "record integrity check; no compilation performed by this recount",
    }


def recount_table(root: Path) -> dict:
    base = root / "results/final-revalidation/table-iii"
    slots = [load_json(path) for path in sorted((base / "slots").glob("*.json"))]
    require(len(slots) == 30, "Table III must contain exactly 30 slots")
    require(len({row["slot_id"] for row in slots}) == 30, "Table III slot IDs must be unique")
    require(
        sorted(row["formal_identity"]["sequence"] for row in slots) == list(range(1, 31)),
        "Table III sequence must be exactly 1..30",
    )

    schedule = load_json(
        root
        / "contracts-and-trajectories/table-iii/inputs/FORMAL_BENCHMARK_SCHEDULE.json"
    )
    require(schedule["formal_denominator"] == 30, "Table III frozen schedule denominator")
    require(schedule["formal_repetitions_per_case"] == 5, "Table III frozen repetitions")
    require(schedule["schedule_status"] == "FROZEN_BEFORE_FORMAL_EXECUTION", "Table III freeze status")
    require(schedule["warmup_denominator"] == 0, "Table III warmups entered denominator")
    require(len(schedule["formal_schedule"]) == 30, "Table III frozen schedule rows")
    schedule_by_sequence = {
        row["sequence"]: row for row in schedule["formal_schedule"]
    }
    require(len(schedule_by_sequence) == 30, "Table III duplicate schedule sequence")

    case_inputs = load_json(
        root
        / "contracts-and-trajectories/table-iii/inputs/CASE_INPUT_BINDINGS.json"
    )
    require(case_inputs["case_count"] == 6, "Table III case-input binding count")
    inputs_by_case = {row["case_id"]: row for row in case_inputs["cases"]}
    require(len(inputs_by_case) == 6, "Table III duplicate case-input binding")
    for item in case_inputs["shared_inputs"].values():
        require(
            item["public_file_sha256"] == sha256_file(root / item["path"]),
            f"Table III shared input: {item['path']}",
        )
    for case_id, item in inputs_by_case.items():
        require(
            item["source"]["public_file_sha256"]
            == sha256_file(root / item["source"]["path"]),
            f"Table III case source: {case_id}",
        )
        for trajectory in item["accepted_loaded_registry_files"]:
            trajectory_path = root / trajectory["path"]
            require(
                trajectory["public_file_sha256"]
                == sha256_file(trajectory_path),
                f"Table III trajectory file: {trajectory['path']}",
            )
            trajectory_json = load_json(trajectory_path)
            require(
                trajectory_json["trajectory_content_id"]
                == trajectory["retained_reference_trajectory_content_id"],
                f"Table III trajectory content ID: {trajectory['path']}",
            )
        require(
            set(item["accepted_loaded_registry_content_ids"])
            == {
                row["retained_reference_trajectory_content_id"]
                for row in item["accepted_loaded_registry_files"]
            },
            f"Table III trajectory semantic join: {case_id}",
        )
        source_text = (root / item["source"]["path"]).read_text(encoding="utf-8")
        source_ids = set(
            re.findall(r'move\([^\n]*["\']([0-9a-f]{64})["\']', source_text)
        )
        required_ids = set(item["accepted_loaded_registry_content_ids"])
        require(source_ids <= required_ids, f"Table III source trajectory IDs: {case_id}")
        require(
            sorted(source_ids) == item["source_referenced_trajectory_content_ids"],
            f"Table III recorded source trajectory IDs: {case_id}",
        )
        require(
            item["accepted_loaded_registry_content_id_count"] == len(required_ids),
            f"Table III registry content-ID count: {case_id}",
        )
        require(
            item["accepted_loaded_registry_file_count"]
            == len(item["accepted_loaded_registry_files"]),
            f"Table III registry file count: {case_id}",
        )
        expected_registry_counts = (
            (2, 2) if case_id.startswith("I4F-C-") else (16, 14)
        )
        require(
            (
                item["accepted_loaded_registry_file_count"],
                item["accepted_loaded_registry_content_id_count"],
            )
            == expected_registry_counts,
            f"Table III accepted registry shape: {case_id}",
        )
        expected_reference_counts = {
            "I4F-C-SAFE-T1": 2,
            "I4F-C-UNSAFE-T0": 2,
            "I4F-H-SAFE-T3": 4,
            "I4F-H-UNSAFE-T2": 2,
            "I4F-R-SAFE-T4": 4,
            "I4F-R-UNSAFE-T0": 2,
        }
        require(
            len(source_ids) == expected_reference_counts[case_id],
            f"Table III source-referenced trajectory count: {case_id}",
        )
        if case_id.startswith("I4F-C-"):
            require(source_ids == required_ids, f"Table III collision source exact trajectory IDs: {case_id}")

    repetitions: dict[str, set[int]] = defaultdict(set)
    verdicts = Counter()
    decisions = Counter()
    total_wall_ns = 0
    case_rss: dict[str, list[int]] = defaultdict(list)
    case_wall: dict[str, list[int]] = defaultdict(list)
    source_map = {
        "I4F-C-SAFE-T1": "contracts-and-trajectories/table-iii/inputs/collision/safe/source.py",
        "I4F-C-UNSAFE-T0": "contracts-and-trajectories/table-iii/inputs/collision/unsafe/source.py",
        "I4F-H-SAFE-T3": "contracts-and-trajectories/table-iii/inputs/sources/H_SAFE_HANDOVER.py",
        "I4F-H-UNSAFE-T2": "contracts-and-trajectories/table-iii/inputs/sources/H_UNSAFE_EARLY_RELEASE.py",
        "I4F-R-SAFE-T4": "contracts-and-trajectories/table-iii/inputs/sources/R_SAFE_SEQUENTIAL_RESOURCE.py",
        "I4F-R-UNSAFE-T0": "contracts-and-trajectories/table-iii/inputs/sources/R_UNSAFE_NO_OWNER.py",
    }
    public_server_source = root / "final-build/native-probe-source/openarm_fcl_pair_probe_server.cpp"
    public_server_source_sha = sha256_file(public_server_source)
    policy_sha = sha256_file(
        root / "contracts-and-trajectories/qualification/reference-contract/geometry_policy.json"
    )
    for row in slots:
        identity = row["formal_identity"]
        case_id = identity["case_id"]
        frozen = schedule_by_sequence[identity["sequence"]]
        require(frozen["case_id"] == case_id, f"{row['slot_id']}: frozen case join")
        require(
            frozen["repetition"] == identity["repetition"],
            f"{row['slot_id']}: frozen repetition join",
        )
        require(
            frozen["sort_key"]
            == expected_schedule_sort_key(identity["repetition"], case_id),
            f"{row['slot_id']}: deterministic schedule key",
        )
        repetitions[case_id].add(identity["repetition"])
        require(identity["formal_denominator_member"], f"{row['slot_id']}: not formal")
        require(identity["fresh_process"], f"{row['slot_id']}: not fresh-process")
        require(not identity["replacement_allowed"], f"{row['slot_id']}: replacement allowed")
        require(row["process_result"]["returncode"] == 0, f"{row['slot_id']}: nonzero return")
        require(row["process_result"]["slot_status"] == "completed", f"{row['slot_id']}: incomplete")
        require(not row["process_result"]["timed_out"], f"{row['slot_id']}: timed out")
        require(row["actual"]["verdict"] == row["expected"]["verdict"], f"{row['slot_id']}: verdict mismatch")
        require(
            row["actual"]["deployment_decision"] == row["expected"]["deployment_decision"],
            f"{row['slot_id']}: decision mismatch",
        )
        expected_decision = "RELEASE" if row["actual"]["verdict"] == "verified-within-bounds" else "BLOCK"
        require(row["actual"]["deployment_decision"] == expected_decision, f"{row['slot_id']}: fail-closed map")
        require(row["actual"]["release_iff_verified_gate_passed"], f"{row['slot_id']}: release gate")
        require(robot_is_zero(row["robot_boundary"]), f"{row['slot_id']}: nonzero robot boundary")
        binding = row["public_binding"]
        require(binding["executed_probe_binary_sha256"] == SERVER_BINARY_SHA256, f"{row['slot_id']}: server binary")
        require(binding["public_probe_source_sha256"] == public_server_source_sha, f"{row['slot_id']}: server source")
        require(binding["collision_error_bound_m"] == 1e-12, f"{row['slot_id']}: comparator")
        require(binding["collision_error_evidence_sha256"] == policy_sha, f"{row['slot_id']}: policy identity")
        require(binding["source_sha256"] == sha256_file(root / source_map[case_id]), f"{row['slot_id']}: source identity")
        semantic_ids = binding["retained_reference_semantic_ids"]
        case_input = inputs_by_case[case_id]
        require(
            case_input["expected_verdict"] == row["expected"]["verdict"],
            f"{row['slot_id']}: case-input expected verdict",
        )
        require(
            case_input["expected_deployment_decision"]
            == row["expected"]["deployment_decision"],
            f"{row['slot_id']}: case-input expected decision",
        )
        require(
            semantic_ids["program_content_id"]
            == case_input["source"]["retained_reference_program_content_id"],
            f"{row['slot_id']}: program semantic join",
        )
        require(
            set(semantic_ids["trajectory_content_ids"])
            == set(case_input["accepted_loaded_registry_content_ids"]),
            f"{row['slot_id']}: trajectory semantic join",
        )
        require(
            len(semantic_ids["program_content_id"]) == 64,
            f"{row['slot_id']}: retained program semantic ID",
        )
        require(
            all(len(value) == 64 for value in semantic_ids["trajectory_content_ids"]),
            f"{row['slot_id']}: retained trajectory semantic IDs",
        )
        for module in binding["effective_final_modules"]:
            if module["module"] == "bisafecode":
                path = root / "final-build/src/bisafecode/__init__.py"
            else:
                path = root / "final-build/src" / (module["module"].replace(".", "/") + ".py")
            require(module["sha256"] == sha256_file(path), f"{row['slot_id']}: module {module['module']}")
        expected_counters = row["expected"]["deterministic_structural_counters"]
        actual_counters = row["structural_and_resource_counters"]
        for key, expected_value in expected_counters.items():
            require(
                actual_counters.get(key) == expected_value,
                f"{row['slot_id']}: structural counter {key}",
            )
        verdicts[row["actual"]["verdict"]] += 1
        decisions[row["actual"]["deployment_decision"]] += 1
        wall = row["timing_ns"]["end_to_end_including_model_load_wall_ns"]
        rss = row["structural_and_resource_counters"]["peak_rss_bytes"]
        total_wall_ns += wall
        case_wall[case_id].append(wall)
        case_rss[case_id].append(rss)

    require(set(repetitions) == set(source_map), "Table III must contain the six frozen workloads")
    require(all(value == set(range(1, 6)) for value in repetitions.values()), "each workload needs repetitions 1..5")
    require(verdicts == {"verified-within-bounds": 15, "violated": 15}, "Table III verdict totals")
    require(decisions == {"RELEASE": 15, "BLOCK": 15}, "Table III decision totals")

    summary = load_json(base / "summary.json")
    require(summary["fixed_formal_denominator"] == 30, "Table III summary denominator")
    require(summary["formal_cases"] == 6, "Table III summary workload count")
    require(summary["formal_repetitions_per_case"] == 5, "Table III summary repetitions")
    require(summary["completed_slots"] == 30, "Table III summary completed slots")
    require(summary["verdict_counts"] == dict(verdicts), "Table III summary verdict totals")
    require(
        summary["deployment_decision_counts"] == dict(decisions),
        "Table III summary decision totals",
    )
    require(summary["vwb_release_slots"] == 15, "Table III summary VWB releases")
    require(summary["vio_block_slots"] == 15, "Table III summary VIO blocks")
    require(summary["unknown_slots"] == 0 and summary["timeout_slots"] == 0, "Table III summary failures")
    require(summary["runner_error_slots"] == 0 and summary["replacement_slots"] == 0, "Table III errors/replacements")
    require(summary["fresh_process_per_slot"], "Table III summary fresh-process flag")
    require(summary["warmups_excluded"], "Table III summary warmup boundary")
    require(summary["collision_error_bound_m"] == 1e-12, "Table III summary comparator")
    require(summary["total_primary_wall_ns"] == total_wall_ns, "Table III total wall time")
    listed_slots = {item["slot_id"]: item["path"] for item in summary["slot_files"]}
    require(set(listed_slots) == {row["slot_id"] for row in slots}, "Table III summary slot index")
    for slot_id, relative in listed_slots.items():
        require((base / relative).is_file(), f"Table III indexed slot missing: {slot_id}")
    reported = {item["case_id"]: item for item in summary["case_summaries"]}
    for case_id in source_map:
        require(reported[case_id]["peak_rss_bytes"]["median"] == int(statistics.median(case_rss[case_id])), f"{case_id}: RSS median")
        require(reported[case_id]["primary_wall_ns"]["median"] == int(statistics.median(case_wall[case_id])), f"{case_id}: wall median")
        selected = [row for row in slots if row["formal_identity"]["case_id"] == case_id]
        expected_actual = {
            key: selected[0]["structural_and_resource_counters"][key]
            for key in sorted(selected[0]["expected"]["deterministic_structural_counters"])
        }
        require(
            all(
                {
                    key: row["structural_and_resource_counters"][key]
                    for key in sorted(row["expected"]["deterministic_structural_counters"])
                }
                == expected_actual
                for row in selected
            ),
            f"{case_id}: structural counters vary across repetitions",
        )
        require(
            reported[case_id]["actual_structural_counters"] == expected_actual,
            f"{case_id}: summary structural counters",
        )
        require(
            reported[case_id]["structural_counters_constant_across_repetitions"],
            f"{case_id}: structural constancy flag",
        )
    return {
        "slots": 30,
        "workloads": 6,
        "verdicts": dict(verdicts),
        "decisions": dict(decisions),
        "unknown": 0,
        "timeouts": 0,
        "runner_errors": 0,
        "replacements": 0,
        "total_primary_wall_s": total_wall_ns / 1e9,
        "rss_medians_bytes": {case: int(statistics.median(values)) for case, values in sorted(case_rss.items())},
    }


def recount_c115(root: Path) -> dict:
    base = root / "results/final-revalidation/c115"
    slots = [load_json(path) for path in sorted((base / "slots").glob("*.json"))]
    require(len(slots) == 15, "C115 must contain exactly 15 slots")
    require(len({row["official_slot_id"] for row in slots}) == 15, "C115 slot IDs must be unique")
    require(sorted(row["sequence"] for row in slots) == list(range(1, 16)), "C115 sequence must be 1..15")
    families = Counter(row["family"] for row in slots)
    require(families == {"C": 5, "H": 5, "R": 5}, "C115 family denominator")
    require(all(row["final_offline_revalidation"] == "PASS" for row in slots), "C115 offline slot failure")
    require(all(robot_is_zero(row["robot_boundary"]) for row in slots), "C115 nonzero robot boundary")

    schedule = load_json(
        root
        / "contracts-and-trajectories/c115/inputs/campaign/TRIAL_SCHEDULE_15.json"
    )
    require(schedule["fixed_denominator"] == 15, "C115 frozen schedule denominator")
    require(schedule["no_replacement"], "C115 frozen schedule permits replacement")
    require(
        schedule["status"]
        == "FROZEN_15_IDENTITIES_AND_CONDITIONS__ZERO_OF_FIFTEEN__PENDING_LIVE_ZERO_SEND_AND_USER_AUTHORIZATION",
        "C115 frozen schedule status",
    )
    require(len(schedule["slots"]) == 15, "C115 frozen schedule row count")
    schedule_by_slot = {row["official_slot_id"]: row for row in schedule["slots"]}
    require(len(schedule_by_slot) == 15, "C115 duplicate schedule slot")
    for row in slots:
        frozen = schedule_by_slot[row["official_slot_id"]]
        require(row["sequence"] == frozen["sequence"], f"{row['official_slot_id']}: frozen sequence")
        require(
            row["run_instance_id"] == frozen["run_instance_id"],
            f"{row['official_slot_id']}: frozen run identity",
        )
        require(
            row["frozen_condition_variant"] == frozen["condition_variant"],
            f"{row['official_slot_id']}: frozen condition",
        )
        require(
            row["frozen_schedule_status_at_freeze"] == frozen["status"],
            f"{row['official_slot_id']}: frozen pre-start status",
        )

    input_bindings = load_json(
        root / "contracts-and-trajectories/c115/C115_INPUT_BINDINGS.json"
    )
    require(input_bindings["fixed_slot_denominator"] == 15, "C115 input-binding denominator")

    def verify_file_records(value) -> None:
        if isinstance(value, list):
            for item in value:
                verify_file_records(item)
        elif isinstance(value, dict):
            if "path" in value and "public_file_sha256" in value:
                require(
                    value["public_file_sha256"] == sha256_file(root / value["path"]),
                    f"C115 public input binding: {value['path']}",
                )
            for item in value.values():
                verify_file_records(item)

    verify_file_records(input_bindings)
    require(
        input_bindings["schedule"]["path"]
        == "contracts-and-trajectories/c115/inputs/campaign/TRIAL_SCHEDULE_15.json",
        "C115 input-binding schedule path",
    )
    require(
        {key: value["slot_count"] for key, value in input_bindings["families"].items()}
        == {"C": 5, "H": 5, "R": 5},
        "C115 input-binding family counts",
    )
    require(
        input_bindings["families"]["H"]["table_iii_case_inputs"]["case_id"]
        == "I4F-H-SAFE-T3",
        "C115 H input case",
    )
    require(
        input_bindings["families"]["R"]["table_iii_case_inputs"]["case_id"]
        == "I4F-R-SAFE-T4",
        "C115 R input case",
    )

    controlled = load_json(base / "unsafe-controls/controlled-core.json")
    require(len(controlled["records"]) == 3, "C115 controlled-core record count")
    negatives = [row for row in controlled["records"] if row["role"] == "C115_C_UNSAFE_NEGATIVE_CONTROL"]
    require(len(negatives) == 2, "C115 controlled negative count")
    require(all(row["verdict"] == "violated" and row["deployment_decision"] == "BLOCK" for row in negatives), "C115 controlled negatives")
    for row in controlled["records"]:
        source = root / "contracts-and-trajectories/c115/inputs/controlled-programs" / f"{row['program_id']}.py"
        require(row["source_sha256"] == sha256_file(source), f"C115 source {row['program_id']}")
        require(row["expected_outcome_passed"], f"C115 expected outcome {row['program_id']}")
        require(row["release_iff_verified_gate_passed"], f"C115 release gate {row['program_id']}")
        require(robot_is_zero(row["robot_boundary"]), f"C115 controlled robot boundary {row['program_id']}")

    native = load_json(base / "unsafe-controls/native-negative-controls.json")
    require(native["slot_count"] == 15 and len(native["slots"]) == 15, "native negative count")
    require(Counter(row["case_id"].split("-")[1] for row in native["slots"]) == {"C": 5, "H": 5, "R": 5}, "native negative families")
    require(all(row["actual_verdict"] == "violated" and row["deployment_decision"] == "BLOCK" for row in native["slots"]), "native negative outcomes")
    require(all(robot_is_zero(row["robot_boundary"]) for row in native["slots"]), "native negative robot boundary")
    table_slots = {
        row["slot_id"]: row
        for row in (
            load_json(path)
            for path in sorted(
                (root / "results/final-revalidation/table-iii/slots").glob("*.json")
            )
        )
    }
    for row in native["slots"]:
        require(row["table_iii_slot_id"] in table_slots, "native negative Table III join")
        table = table_slots[row["table_iii_slot_id"]]
        identity = table["formal_identity"]
        require(row["case_id"] == identity["case_id"], f"{row['table_iii_slot_id']}: negative case join")
        require(row["repetition"] == identity["repetition"], f"{row['table_iii_slot_id']}: negative repetition join")
        require(row["sequence"] == identity["sequence"], f"{row['table_iii_slot_id']}: negative sequence join")
        require(row["actual_verdict"] == table["actual"]["verdict"], f"{row['table_iii_slot_id']}: negative verdict join")
        require(row["deployment_decision"] == table["actual"]["deployment_decision"], f"{row['table_iii_slot_id']}: negative decision join")
        require(row["returncode"] == table["process_result"]["returncode"], f"{row['table_iii_slot_id']}: negative return-code join")
        require(row["timed_out"] == table["process_result"]["timed_out"], f"{row['table_iii_slot_id']}: negative timeout join")
        require(row["robot_boundary"] == table["robot_boundary"], f"{row['table_iii_slot_id']}: negative robot-boundary join")
        require(row["source_sha256"] == table["public_binding"]["source_sha256"], f"{row['table_iii_slot_id']}: negative source join")
        require(
            row["program_content_id"]
            == table["public_binding"]["retained_reference_semantic_ids"]["program_content_id"],
            f"{row['table_iii_slot_id']}: negative program join",
        )

    dense = load_json(base / "dense-summary/summary.json")
    chunks = load_jsonl(base / "dense-summary/chunks.jsonl")
    require(
        dense["evidence_status"]
        == "ACCEPTED_FINITE_MODEL_RELATIVE_DENSE_REFERENCE",
        "dense evidence status",
    )
    require(dense["reference_run_report"] == "ZERO_NEGATIVE_SELF_AND_WORLD_DISTANCES", "dense reference status")
    require(dense["public_aggregate_recount_status"] == "PASS_20_OF_20_CHUNKS", "dense public aggregate status")
    require(dense["row_level_disclosure"] == "NOT_PUBLIC__COMPACT_AGGREGATES_ONLY", "dense row-level boundary")
    require(robot_is_zero(dense["robot_boundary"]), "dense nonzero robot boundary")
    require(len(chunks) == dense["chunk_count"] == 20, "dense chunk count")
    states = sum(row["state_count"] for row in chunks)
    self_rows = sum(row["scope_row_counts"]["self"] for row in chunks)
    world_rows = sum(row["scope_row_counts"]["world"] for row in chunks)
    negative_self = sum(row["scope_negative_distance_counts"]["self"] for row in chunks)
    negative_world = sum(row["scope_negative_distance_counts"]["world"] for row in chunks)
    require(states == dense["dense_state_count"] == 1932, "dense state count")
    require(self_rows == dense["self_distance_row_count"] == 434700, "dense self rows")
    require(world_rows == dense["world_distance_row_count"] == 44436, "dense world rows")
    require(self_rows + world_rows == dense["all_distance_row_count"] == 479136, "dense total rows")
    require(negative_self == dense["negative_self_distance_count"] == 0, "dense self negatives")
    require(negative_world == dense["negative_world_distance_count"] == 0, "dense world negatives")
    require(all(row["exit_code"] == 0 for row in chunks), "dense chunk exit code")
    require(
        all(
            row["distance_row_count"]
            == row["scope_row_counts"]["self"] + row["scope_row_counts"]["world"]
            for row in chunks
        ),
        "dense per-chunk row accounting",
    )
    minimum_self = min(row["scope_minimum_signed_distance_m"]["self"] for row in chunks)
    require(abs(minimum_self - 0.005403543151862089) < 1e-18, "dense minimum self distance")

    qualification = load_json(base / "dense-summary/distance-error-qualification.json")
    for name in ("summary", "chunks"):
        item = qualification["dense_evidence"]
        path = root / item[f"{name}_path"]
        require(item[f"{name}_sha256"] == sha256_file(path), f"dense {name} identity")
    policy = qualification["policy_input"]
    require(policy["sha256"] == sha256_file(root / policy["path"]), "comparator policy identity")
    require(qualification["candidate_and_reference_bound_m"] == 1e-12, "comparator bound")
    require(not qualification["negative_controls"]["missing_bound"]["release_allowed"], "missing comparator must block")
    require(not qualification["negative_controls"]["underbound_zero"]["release_allowed"], "underbound comparator must block")

    final_qualification = load_json(
        root / "oracles/qualification/FINAL_CONTRACT_QUALIFICATION.json"
    )
    require(
        final_qualification["public_record_role"] == "ACCEPTED_REFERENCE_QUALIFICATION",
        "final qualification record role",
    )
    require(
        final_qualification["public_projection_reexecution_status"]
        == "NOT_RUN__REFERENCE_RECORD_ONLY",
        "final qualification re-execution boundary",
    )
    require(
        all(
            item["status"] == "admitted" and item["release_allowed"]
            for item in final_qualification["admissions"].values()
        ),
        "accepted final qualification admissions",
    )
    require(
        all(not item["release_allowed"] for item in final_qualification["negative_controls"].values()),
        "accepted final qualification negative controls",
    )
    distance_contract = final_qualification["distance_error_contract"]
    require(distance_contract["candidate_and_reference_bound_m"] == 1e-12, "accepted qualification comparator")
    require(
        distance_contract["evidence_sha256"]
        == sha256_file(root / distance_contract["evidence_path"]),
        "accepted qualification policy binding",
    )
    for module in final_qualification["effective_final_modules"]:
        require(
            module["sha256"] == sha256_file(root / module["path"]),
            f"accepted qualification module: {module['module']}",
        )

    zero_send = load_json(base / "zero-send/zero-send.json")
    require(zero_send["status"] == "PASS_ZERO_SEND_OFFLINE_ONLY", "zero-send status")
    require(all(not zero_send[key] for key in (
        "action_client_created", "can_contacted", "controller_contacted", "goal_send_count",
        "gripper_command_count", "hardware_trial_count", "motion_command_count", "rclpy_imported",
        "ros_graph_contacted", "trajectory_published",
    )), "zero-send boundary")

    summary = load_json(base / "summary.json")
    require(summary["fixed_slot_denominator"] == 15, "C115 summary denominator")
    require(summary["final_offline_slot_results"] == {"PASS": 15, "FAIL": 0, "UNKNOWN": 0}, "C115 summary outcomes")
    require(
        summary["unsafe_controls"]["status"]
        == "ALL_RETAINED_UNSAFE_CONTROLS_BLOCKED_ZERO_SEND",
        "C115 summary unsafe-control status",
    )
    listed_slots = {
        item["slot_id"]: item["path"] for item in summary["slot_files"]
    }
    require(set(listed_slots) == {row["official_slot_id"] for row in slots}, "C115 summary slot index")
    for slot_id, relative in listed_slots.items():
        require((base / relative).is_file(), f"C115 indexed slot missing: {slot_id}")
    return {
        "slots": 15,
        "families": dict(families),
        "offline_pass": 15,
        "controlled_unsafe_blocked": 2,
        "native_unsafe_blocked": 15,
        "dense_states": states,
        "dense_self_rows": self_rows,
        "dense_world_rows": world_rows,
        "dense_total_rows": self_rows + world_rows,
        "dense_negative_self": negative_self,
        "dense_negative_world": negative_world,
        "dense_minimum_self_m": minimum_self,
        "zero_send": "PASS",
    }


def recount_historical(root: Path) -> dict:
    summary = load_json(root / "hardware-evidence/HISTORICAL_DEPLOYMENT_SUMMARY.json")
    families = summary["families"]
    require(summary["fixed_primary_denominator"] == 15, "historical denominator")
    require((families["H"]["pass_count"], families["H"]["unknown_count"]) == (5, 0), "historical H")
    require((families["R"]["pass_count"], families["R"]["unknown_count"]) == (5, 0), "historical R")
    require((families["C"]["pass_count"], families["C"]["unknown_count"]) == (3, 2), "historical C")
    unknown = families["C"]["unknown_slots"]
    require(unknown["C-04"]["maximum_motion_sample_gap_ms"] == 41.547412, "C04 gap")
    require(unknown["C-05"]["maximum_motion_sample_gap_ms"] == 61.317265, "C05 gap")
    require(families["C"]["coverage_rule_ms"] == 25.0, "historical coverage rule")
    clearance_link = families["R"]["model_relative_clearance_record"]
    clearance_path = root / clearance_link["path"]
    clearance = load_json(clearance_path)
    exact_clearance_m = 7.825518167755598e-06
    exact_clearance_mm = 0.007825518167755598
    require(
        clearance["minimum_continuous_residual_adjusted_lower_bound_m"]
        == exact_clearance_m,
        "historical R minimum model-relative clearance",
    )
    require(
        clearance["minimum_continuous_residual_adjusted_lower_bound_mm"]
        == exact_clearance_mm,
        "historical R minimum model-relative clearance in mm",
    )
    require(exact_clearance_mm == exact_clearance_m * 1000, "historical R m-to-mm conversion")
    require(clearance["display_value_mm"] == round(exact_clearance_mm, 5), "historical R display clearance")
    require(clearance["continuous_interval_count"] == 71828, "historical R interval count")
    require(clearance["continuous_unresolved_interval_count"] == 0, "historical R unresolved intervals")
    require(clearance["frozen_residual_m"] == 0.001, "historical R frozen residual")
    require(clearance["passed"], "historical R clearance rule")
    require(
        clearance_link["minimum_continuous_residual_adjusted_lower_bound_m"]
        == exact_clearance_m,
        "historical R clearance summary join",
    )
    require(clearance_link["display_value_mm"] == 0.00783, "historical R display summary join")
    rows = load_jsonl(root / "hardware-evidence/HISTORICAL_SLOT_INDEX.jsonl")
    require(len(rows) == 15, "historical slot index")
    chain = load_json(root / "hardware-evidence/C_DISPATCH_BOUNDARY.json")
    require(chain["counts"]["direct_per_slot_verdict_release_send_bindings_found"] == 0, "historical C trace boundary")
    require(chain["counts"]["retained_non_vwb_or_block_to_send_records_found"] == 0, "historical non-VWB send record")
    return {
        "primary_slots": 15,
        "H": {"PASS": 5, "UNKNOWN": 0},
        "R": {"PASS": 5, "UNKNOWN": 0},
        "C": {"PASS": 3, "UNKNOWN": 2},
        "C04_gap_ms": 41.547412,
        "C05_gap_ms": 61.317265,
        "coverage_rule_ms": 25.0,
        "R_minimum_model_relative_clearance_m": exact_clearance_m,
        "R_display_clearance_mm": 0.00783,
        "C_dispatch_trace": "SUPPORTED_AT_CAMPAIGN_LEVEL__PER_DISPATCH_TRACEABILITY_INCOMPLETE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    root = args.artifact_root.resolve()
    report = {
        "schema": "bisafecode.public.native-recount/v1",
        "status": "PASS",
        "source_build_binding": recount_source_build_binding(root),
        "table_iii": recount_table(root),
        "c115": recount_c115(root),
        "historical_hardware": recount_historical(root),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
