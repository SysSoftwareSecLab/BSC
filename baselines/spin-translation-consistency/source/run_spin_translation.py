#!/usr/bin/env python3
"""Executable SPIN baseline for the preregistered sequential discrete H/R subset."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "03_experiments/contracts/EXP-S4-021_MENTOR3_SPIN_COMMON_SUBSET_BASELINE_MAC.json"
OUTPUT = ROOT / "03_experiments/results/EXP-S4-021_MENTOR3_SPIN_COMMON_SUBSET_BASELINE_MAC"
EXTERNAL40_SOURCES = ROOT / "03_experiments/raw/EXP-S4-008_RQ1_BLIND_EXTERNAL_VALIDITY/source_seal_r3_40/sealed_sources"
EXTERNAL40_DERIVED = ROOT / "03_experiments/derived/EXP-S4-008_RQ1_BLIND_EXTERNAL_VALIDITY_R4_CORRECTED/per_instance_results.jsonl"
EXTERNAL25_COMMIT = "ANONYMIZED_PRIVATE_FREEZE_REFERENCE"
EXTERNAL25_SOURCE_ROOT = "03_experiments/raw/EXP-S4-012_PROPERTY_STRATIFIED_EXTERNAL_REPLICATION/source_seal_human20_llm9/sealed_sources"
EXTERNAL25_DERIVED = "03_experiments/derived/EXP-S4-012_PROPERTY_STRATIFIED_EXTERNAL_REPLICATION/human20_llm9/per_instance_results.jsonl"
CORRECTED_RESULTS = ROOT / "03_experiments/results/EXP-S4-020_MENTOR3_CORRECTED_METHOD_EXTERNAL_RECOMPUTE_MAC/per_instance_results.jsonl"
SUPPORTED_CALLS = {
    "wait", "close", "open", "transfer_authority", "acquire", "release", "range"
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_bytes(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)


def git_paths(commit: str, prefix: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, prefix], cwd=ROOT, text=True
    )
    return sorted(path for path in output.splitlines() if path.endswith(".py"))


def jsonl_bytes(value: bytes) -> dict[str, dict[str, Any]]:
    return {
        item["opaque_case_id"]: item
        for item in (json.loads(line) for line in value.decode("utf-8").splitlines())
    }


def call_names(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def uses_input(tree: ast.AST, name: str) -> bool:
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(tree))


def literal(node: ast.AST) -> Any:
    if not isinstance(node, ast.Constant):
        raise ValueError(f"SPIN subset requires literal arguments, got {ast.dump(node)}")
    return node.value


def promela_arm(value: str) -> str:
    return {"left": "LEFT", "right": "RIGHT"}[value]


def promela_object(value: str) -> str:
    return {"payload_alpha": "PAYLOAD_ALPHA", "payload_beta": "PAYLOAD_BETA"}[value]


def promela_resource(value: str) -> str:
    return {"fixture_alpha": "FIXTURE_ALPHA", "tool_beta": "TOOL_BETA"}[value]


def range_count(node: ast.For) -> int:
    iterator = node.iter
    if not isinstance(iterator, ast.Call) or not isinstance(iterator.func, ast.Name) or iterator.func.id != "range":
        raise ValueError("SPIN subset supports only literal range loops")
    values = [int(literal(item)) for item in iterator.args]
    return len(range(*values))


def render_condition(node: ast.AST) -> str:
    if isinstance(node, ast.Name) and node.id == "ready":
        return "ready == 1"
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "mode"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and literal(node.comparators[0]) == "fast"
    ):
        return "mode == MODE_FAST"
    raise ValueError(f"unsupported finite-input condition: {ast.dump(node)}")


def render_block(statements: Iterable[ast.stmt], indent: str = "    ") -> list[str]:
    lines: list[str] = []
    for statement in statements:
        if isinstance(statement, ast.If):
            condition = render_condition(statement.test)
            lines.extend([
                indent + "if",
                indent + f":: {condition} ->",
                *render_block(statement.body, indent + "    "),
                indent + ":: else ->",
                *render_block(statement.orelse, indent + "    "),
                indent + "fi;",
            ])
            continue
        if isinstance(statement, ast.For):
            for _ in range(range_count(statement)):
                lines.extend(render_block(statement.body, indent))
            continue
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            raise ValueError(f"unsupported statement: {ast.dump(statement)}")
        call = statement.value
        if not isinstance(call.func, ast.Name):
            raise ValueError("SPIN subset supports only direct calls")
        name = call.func.id
        args = [literal(item) for item in call.args]
        if name == "wait":
            lines.append(indent + "skip;")
        elif name == "close":
            lines.append(indent + f"do_close({promela_arm(str(args[0]))}, {promela_object(str(args[1]))});")
        elif name == "open":
            lines.append(indent + f"do_open({promela_arm(str(args[0]))}, {promela_object(str(args[1]))});")
        elif name == "transfer_authority":
            lines.append(indent + f"do_transfer({promela_object(str(args[0]))}, {promela_arm(str(args[1]))}, {promela_arm(str(args[2]))});")
        elif name == "acquire":
            lines.append(indent + f"do_acquire({promela_arm(str(args[0]))}, {promela_resource(str(args[1]))});")
        elif name == "release":
            lines.append(indent + f"do_release({promela_arm(str(args[0]))}, {promela_resource(str(args[1]))});")
        else:
            raise ValueError(f"unsupported call in SPIN subset: {name}")
    return lines or [indent + "skip;"]


PROMELA_PREAMBLE = r'''#define LEFT 1
#define RIGHT 2
#define PAYLOAD_ALPHA 0
#define PAYLOAD_BETA 1
#define FIXTURE_ALPHA 0
#define TOOL_BETA 1
#define MODE_FAST 0
#define MODE_SAFE 1

byte ready;
byte mode;
byte holders[2];
byte authority[2];
byte sender[2];
byte resource_owner[2];
bool bad = false;

inline fail_now() {
    bad = true;
    assert(bad == false)
}

inline do_close(arm, object_id) {
    atomic {
        if
        :: holders[object_id] == 0 ->
            holders[object_id] = arm;
            authority[object_id] = arm;
            sender[object_id] = arm
        :: (holders[object_id] & arm) != 0 ->
            fail_now()
        :: else ->
            /* No-move subset: the frozen initial tools are outside the handover zone. */
            fail_now()
        fi
    }
}

inline do_open(arm, object_id) {
    atomic {
        if
        :: (holders[object_id] & arm) == 0 ->
            fail_now()
        :: holders[object_id] == arm ->
            holders[object_id] = 0;
            authority[object_id] = 0;
            sender[object_id] = 0
        :: else ->
            /* A dual grasp cannot be legal in this no-move spatial subset. */
            fail_now()
        fi
    }
}

inline do_transfer(object_id, from_arm, to_arm) {
    atomic {
        /* Any no-move transfer violates the frozen handover-zone obligation. */
        fail_now()
    }
}

inline do_acquire(arm, resource_id) {
    atomic {
        if
        :: resource_owner[resource_id] == 0 -> resource_owner[resource_id] = arm
        :: else -> fail_now()
        fi
    }
}

inline do_release(arm, resource_id) {
    atomic {
        if
        :: resource_owner[resource_id] == arm -> resource_owner[resource_id] = 0
        :: else -> fail_now()
        fi
    }
}
'''


def render_promela(source: str, case_id: str) -> str:
    tree = ast.parse(source)
    names = call_names(tree)
    if "move" in names or "parallel" in names or not names <= SUPPORTED_CALLS:
        raise ValueError(f"{case_id}: outside preregistered SPIN subset: {sorted(names)}")
    task = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "task"
    )
    body = ["init {"]
    if uses_input(task, "ready"):
        body.extend(["    if", "    :: ready = 0", "    :: ready = 1", "    fi;"])
    if uses_input(task, "mode"):
        body.extend(["    if", "    :: mode = MODE_FAST", "    :: mode = MODE_SAFE", "    fi;"])
    body.extend(render_block(task.body))
    body.extend([
        "    assert(resource_owner[FIXTURE_ALPHA] == 0);",
        "    assert(resource_owner[TOOL_BETA] == 0);",
        "    assert(bad == false);",
        "}",
    ])
    return PROMELA_PREAMBLE + "\n" + f"/* {case_id} */\n" + "\n".join(body) + "\n"


def corrected_method_records() -> dict[str, dict[str, Any]]:
    return {
        item["case_id"]: item
        for item in (
            json.loads(line)
            for line in CORRECTED_RESULTS.read_text(encoding="utf-8").splitlines()
            if '"population": "External-' in line
        )
    }


def candidate_records() -> tuple[list[dict[str, Any]], dict[str, int]]:
    corrected = corrected_method_records()
    external40 = jsonl_bytes(EXTERNAL40_DERIVED.read_bytes())
    external25_bytes = git_bytes(EXTERNAL25_COMMIT, EXTERNAL25_DERIVED)
    external25 = jsonl_bytes(external25_bytes)
    candidates: list[dict[str, Any]] = []
    exclusions = Counter()
    for source_path in sorted(EXTERNAL40_SOURCES.glob("CASE-*.py")):
        source_bytes = source_path.read_bytes()
        case_id = source_path.stem
        record = external40[case_id]
        tree = ast.parse(source_bytes.decode("utf-8"))
        names = call_names(tree)
        if "move" in names:
            exclusions["External-40:move"] += 1
            continue
        if "parallel" in names:
            exclusions["External-40:parallel"] += 1
            continue
        if not names <= SUPPORTED_CALLS:
            exclusions["External-40:unsupported-call"] += 1
            continue
        candidates.append({
            "population": "External-40",
            "case_id": case_id,
            "source": source_bytes.decode("utf-8"),
            "source_sha256": sha256_bytes(source_bytes),
            "source_origin": source_path.relative_to(ROOT).as_posix(),
            "oracle": record["oracle_verdict"],
            "corrected_bisafecode": corrected[case_id]["corrected_verdict"],
        })
    for path in git_paths(EXTERNAL25_COMMIT, EXTERNAL25_SOURCE_ROOT):
        source_bytes = git_bytes(EXTERNAL25_COMMIT, path)
        case_id = Path(path).stem
        record = external25[case_id]
        if record["oracle"] not in {"safe", "unsafe"}:
            exclusions["External-25:oracle-invalid"] += 1
            continue
        if record["property"] not in {"H", "R"}:
            exclusions["External-25:C-property"] += 1
            continue
        tree = ast.parse(source_bytes.decode("utf-8"))
        names = call_names(tree)
        if "move" in names:
            exclusions["External-25:move"] += 1
            continue
        if "parallel" in names:
            exclusions["External-25:parallel"] += 1
            continue
        if not names <= SUPPORTED_CALLS:
            exclusions["External-25:unsupported-call"] += 1
            continue
        candidates.append({
            "population": "External-25",
            "case_id": case_id,
            "source": source_bytes.decode("utf-8"),
            "source_sha256": sha256_bytes(source_bytes),
            "source_origin": f"{EXTERNAL25_COMMIT}:{path}",
            "property": record["property"],
            "oracle": record["oracle"],
            "corrected_bisafecode": corrected[case_id]["corrected_verdict"],
        })
    return sorted(candidates, key=lambda item: (item["population"], item["case_id"])), dict(sorted(exclusions.items()))


def parse_spin_output(output: str) -> Mapping[str, int | None]:
    def value(pattern: str) -> int | None:
        match = re.search(pattern, output)
        return int(match.group(1)) if match else None
    return {
        "errors": value(r"errors:\s*(\d+)"),
        "stored_states": value(r"(\d+)\s+states, stored"),
        "matched_states": value(r"(\d+)\s+states, matched"),
        "transitions": value(r"(\d+)\s+transitions"),
        "memory_mb": None,
    }


def run_spin_model(promela: str) -> Mapping[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bisafecode-spin-") as directory:
        root = Path(directory)
        model = root / "model.pml"
        model.write_text(promela, encoding="utf-8")
        start = time.perf_counter_ns()
        completed = subprocess.run(
            ["spin", "-run", "-m100000", "model.pml"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=120,
        )
        elapsed_ns = time.perf_counter_ns() - start
    combined = completed.stdout + completed.stderr
    parsed = dict(parse_spin_output(combined))
    if completed.returncode != 0 or parsed["errors"] is None:
        raise RuntimeError(f"SPIN execution failed ({completed.returncode}):\n{combined}")
    return {
        "returncode": completed.returncode,
        "elapsed_ns": elapsed_ns,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        **parsed,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"create-once output exists: {OUTPUT}")
    version = subprocess.check_output(["spin", "-V"], text=True).strip()
    if "Spin Version 6.5.2" not in version:
        raise RuntimeError(f"unexpected SPIN identity: {version}")
    candidates, exclusions = candidate_records()
    if len(candidates) != 27:
        raise ValueError(f"preregistered subset size changed: {len(candidates)}")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    model_root = OUTPUT / "models"
    log_root = OUTPUT / "logs"
    model_root.mkdir()
    log_root.mkdir()
    records: list[dict[str, Any]] = []
    for item in candidates:
        promela = render_promela(item["source"], item["case_id"])
        model_path = model_root / f"{item['case_id']}.pml"
        model_path.write_text(promela, encoding="utf-8")
        run = dict(run_spin_model(promela))
        log_path = log_root / f"{item['case_id']}.txt"
        log_path.write_text(run.pop("stdout") + run.pop("stderr"), encoding="utf-8")
        verdict = "violated" if int(run["errors"]) > 0 else "verified-within-bounds"
        expected = "violated" if item["oracle"] == "unsafe" else "verified-within-bounds"
        records.append({
            key: value for key, value in item.items() if key != "source"
        } | {
            "promela_sha256": sha256_file(model_path),
            "spin_verdict": verdict,
            "spin_matches_oracle": verdict == expected,
            "spin": run,
            "runtime_boundary": "environment-specific descriptive metadata; not a cross-tool speed comparison",
        })
    with (OUTPUT / "per_instance_results.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    confusion = Counter((item["oracle"], item["spin_verdict"]) for item in records)
    summary = {
        "schema": "bisafecode.mentor3.spin-common-subset-baseline/v1",
        "experiment_id": "EXP-S4-021_MENTOR3_SPIN_COMMON_SUBSET_BASELINE_MAC",
        "status": "COMPLETE_PENDING_COUNTER_AUDIT",
        "contract_sha256": sha256_file(CONTRACT),
        "tool_identity": {
            "spin_version": version,
            "spin_path": shutil.which("spin"),
            "translator_sha256": sha256_file(Path(__file__)),
        },
        "subset": {
            "N": len(records),
            "by_population": dict(sorted(Counter(item["population"] for item in records).items())),
            "oracle_counts": dict(sorted(Counter(item["oracle"] for item in records).items())),
            "exclusions": exclusions,
        },
        "results": {
            "correct": sum(item["spin_matches_oracle"] for item in records),
            "confusion_matrix": {
                f"oracle_{oracle}__spin_{verdict}": count
                for (oracle, verdict), count in sorted(confusion.items())
            },
            "corrected_bisafecode_counts": dict(sorted(Counter(
                item["corrected_bisafecode"] for item in records
            ).items())),
        },
        "interpretation_boundary": {
            "equivalent_to_bisafecode": False,
            "collision_geometry_tested": False,
            "timed_parallel_semantics_tested": False,
            "four_valued_abstention_tested": False,
            "cross_tool_runtime_claim_authorized": False,
        },
    }
    write_json(OUTPUT / "summary.json", summary)
    files = []
    for path in sorted(item for item in OUTPUT.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(OUTPUT).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        })
    write_json(OUTPUT / "manifest.json", {
        "schema": "bisafecode.mentor3.spin-common-subset-baseline-manifest/v1",
        "files": files,
        "file_count": len(files),
        "paper_result_eligible": False,
        "reason": "requires counter-audit and capability-wording lock",
    })
    print(json.dumps(summary["results"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
