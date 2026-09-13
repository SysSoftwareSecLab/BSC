"""Content-addressed EXP-S4-002 contracts, assets, toolchain, and frozen core.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


CONTRACT_DIR = "03_experiments/contracts/EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE"

CONTRACT_PATHS = (
    f"{CONTRACT_DIR}/ASSET_MANIFEST.json",
    f"{CONTRACT_DIR}/BLIND_PHASE_AND_DATA_PATH_CONTRACT.json",
    f"{CONTRACT_DIR}/FORMAL_COMMANDS.json",
    f"{CONTRACT_DIR}/GENERATION_SCHEDULE.json",
    f"{CONTRACT_DIR}/GRAMMAR_AND_FAMILY_SPEC.json",
    f"{CONTRACT_DIR}/MAC_APPROVAL_TEMPLATE.json",
    f"{CONTRACT_DIR}/METHOD_AND_BASELINE_BUDGET_CONTRACT.json",
    f"{CONTRACT_DIR}/METRIC_AND_STOP_RULE_CONTRACT.json",
    f"{CONTRACT_DIR}/ORACLE_AND_ASSET_CONTRACT.json",
    f"{CONTRACT_DIR}/SCIENTIFIC_CONTRACT.json",
    f"{CONTRACT_DIR}/schemas/approval.schema.json",
    f"{CONTRACT_DIR}/schemas/attempt_record.schema.json",
    f"{CONTRACT_DIR}/schemas/method_record.schema.json",
    f"{CONTRACT_DIR}/schemas/oracle_record.schema.json",
    f"{CONTRACT_DIR}/schemas/phase_seal.schema.json",
    f"{CONTRACT_DIR}/schemas/run_manifest.schema.json",
    f"{CONTRACT_DIR}/schemas/evidence_lifecycle.schema.json",
    f"{CONTRACT_DIR}/llm/AGGREGATION_POLICY.json",
    f"{CONTRACT_DIR}/llm/LLM_JUDGE_PROMPT.txt",
    f"{CONTRACT_DIR}/llm/cli_invocation.schema.json",
    f"{CONTRACT_DIR}/llm/judge_output.schema.json",
)

ASSET_PATHS = (
    f"{CONTRACT_DIR}/assets/geometry_policy.json",
    f"{CONTRACT_DIR}/assets/logical_event_schema.json",
    f"{CONTRACT_DIR}/assets/logical_oracle_rules.json",
    f"{CONTRACT_DIR}/assets/model.json",
    f"{CONTRACT_DIR}/assets/object_resource_semantics.json",
    f"{CONTRACT_DIR}/assets/scene.json",
    f"{CONTRACT_DIR}/assets/trajectories/left_approach.json",
    f"{CONTRACT_DIR}/assets/trajectories/left_approach.sidecar.json",
    f"{CONTRACT_DIR}/assets/trajectories/left_retreat.json",
    f"{CONTRACT_DIR}/assets/trajectories/left_retreat.sidecar.json",
    f"{CONTRACT_DIR}/assets/trajectories/right_approach.json",
    f"{CONTRACT_DIR}/assets/trajectories/right_approach.sidecar.json",
    f"{CONTRACT_DIR}/assets/trajectories/right_retreat.json",
    f"{CONTRACT_DIR}/assets/trajectories/right_retreat.sidecar.json",
)

FROZEN_CORE_IDENTITIES: Mapping[str, str] = {
    "src/bisafecode/restricted_python.py": "f7db4a4890602cf0e78f7b4b7cf74feffed9127a049858ca403d998a556310e9",
    "src/bisafecode/timed_ir.py": "fe6289f30b5d3b00608ad916627f43ca365218ab38afb400cdd1dd29e3e16c20",
    "src/bisafecode/explicit_state.py": "112e9b8cb1d13fb18d71f60c802927b2fc8938a59597552b7daca977dd2bb295",
    "src/bisafecode/model_checking.py": "e1bd7a0d39a628e093fdeea1c807d0b8f353539455dc0b6bf6dfd1c544712ff6",
    "src/bisafecode/trajectory.py": "4a9011fd4385e9cc9adfdde039132a7847bfc1c46aa70bb33702a9883cd2b0ff",
    "src/bisafecode/verdicts.py": "911be219c52e08d92530bb07ca8fb456b9eecfe40d468e6407fdf939e4011dda",
    "src/bisafecode/continuous_collision.py": "110f43ccff62c304288f759f8cd0261e3e3160d7f6d16b16dcfdc549b6925fbe",
    "src/bisafecode/geometry_contract.py": "2bd1ea2c02b4c8a01e0cf602ab9b50ac7f7177d3f43abdb5bd69fcd150bea7a6",
    "src/bisafecode/resource_monitor.py": "a5205e9e1fe761d25bb8eb661e73e81e1a7f0ca4a7129d49f002655480a6987f",
    "src/bisafecode/handover_oracle.py": "79afb411342950589a0fb603408bbcb18196a94e8f3e956fda50c654e5423856",
    "src/bisafecode/resource_oracle.py": "33240528cc0c6d4f2f79a14f0757b8a053932120c59515c369594978fafb434b",
    "tools/run_c3_v2_mujoco_oracle.py": "098f3cb164fbe7cb2fc17d73a12618e488a6641241db447d00249aad0098aee1",
    "03_experiments/results/EXP-S2-053_STAGE2_CANONICAL_12_SCIENTIFIC_CLOSURE/scripts/p6r2_evidence_adapter.py": "c8ea74f4c7d08b2f192833e8ef8ee42b5c82d6e898536a6bfee631caa13a4b22",
    "03_experiments/results/EXP-S2-053_STAGE2_CANONICAL_12_SCIENTIFIC_CLOSURE/scripts/run_p6r3.py": "1eb98443e90c6ff90c53251962700cf3499ed6c9f27e9de10fc5f4374b72e52f",
}

TOOLCHAIN_PATHS = tuple(sorted(set(
    CONTRACT_PATHS
    + ASSET_PATHS
    + tuple(FROZEN_CORE_IDENTITIES)
    + (
        "src/bisafecode/stage4_unseen/__init__.py",
        "src/bisafecode/stage4_unseen/canonicalize.py",
        "src/bisafecode/stage4_unseen/leakage.py",
        "src/bisafecode/stage4_unseen/metrics.py",
        "src/bisafecode/stage4_unseen/time_bounds.py",
        "src/bisafecode/stage4_controlled/__init__.py",
        "src/bisafecode/stage4_controlled/admission.py",
        "src/bisafecode/stage4_controlled/gate.py",
        "src/bisafecode/stage4_controlled/generator.py",
        "src/bisafecode/stage4_controlled/geometry_oracle.py",
        "src/bisafecode/stage4_controlled/identity.py",
        "src/bisafecode/stage4_controlled/isolation.py",
        "src/bisafecode/stage4_controlled/llm_adapter.py",
        "src/bisafecode/stage4_controlled/codex_cli_adapter.py",
        "src/bisafecode/stage4_controlled/logical_oracle.py",
        "src/bisafecode/stage4_controlled/metrics.py",
        "src/bisafecode/stage4_controlled/methods.py",
        "src/bisafecode/stage4_controlled/oracle.py",
        "src/bisafecode/stage4_controlled/pipeline.py",
        "src/bisafecode/stage4_controlled/property_adapters.py",
        "src/bisafecode/stage4_controlled/provenance.py",
        "src/bisafecode/stage4_controlled/records.py",
        "src/bisafecode/stage4_controlled/schedule.py",
        "src/bisafecode/stage4_controlled/seals.py",
        "src/bisafecode/stage4_controlled/trace_extractor.py",
        "src/bisafecode/stage4_controlled/worker.py",
        "tools/exp_s4_002_controlled.py",
        "tools/exp_s4_002_freeze_audit.py",
        "tools/exp_s4_002_openai_preflight.py",
        "tools/exp_s4_002_codex_cli_preflight.py",
        "tools/exp_s4_002_metrics.py",
    )
)))


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_sha256(items: Sequence[tuple[str, str]]) -> str:
    payload = json.dumps(
        [{"path": path, "sha256": value} for path, value in sorted(items)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def expected_logical_oracle_identities(
    root: Optional[Path] = None,
) -> Mapping[str, str]:
    """Return exact live hashes required by the independent logical oracle."""

    root = (root or repository_root()).resolve()
    relative = {
        "event_schema_sha256": f"{CONTRACT_DIR}/assets/logical_event_schema.json",
        "logical_rules_sha256": f"{CONTRACT_DIR}/assets/logical_oracle_rules.json",
        "object_resource_semantics_sha256": f"{CONTRACT_DIR}/assets/object_resource_semantics.json",
        "scene_sha256": f"{CONTRACT_DIR}/assets/scene.json",
    }
    return {key: sha256_file(root / path) for key, path in relative.items()}


def expected_geometry_oracle_identities(
    root: Optional[Path] = None,
) -> Mapping[str, str]:
    """Return exact model/scene/policy and trajectory/sidecar identities."""

    root = (root or repository_root()).resolve()
    trajectory_paths = [path for path in ASSET_PATHS if "/trajectories/" in path and path.endswith(".json") and not path.endswith(".sidecar.json")]
    sidecar_paths = [path for path in ASSET_PATHS if path.endswith(".sidecar.json")]
    return {
        "model_sha256": sha256_file(root / f"{CONTRACT_DIR}/assets/model.json"),
        "scene_sha256": sha256_file(root / f"{CONTRACT_DIR}/assets/scene.json"),
        "geometry_policy_sha256": sha256_file(root / f"{CONTRACT_DIR}/assets/geometry_policy.json"),
        "trajectory_manifest_sha256": _manifest_sha256(
            [(path, sha256_file(root / path)) for path in trajectory_paths]
        ),
        "sidecar_manifest_sha256": _manifest_sha256(
            [(path, sha256_file(root / path)) for path in sidecar_paths]
        ),
        "object_resource_semantics_sha256": sha256_file(
            root / f"{CONTRACT_DIR}/assets/object_resource_semantics.json"
        ),
    }


def _identity(root: Path, paths: Sequence[str], schema: str) -> Mapping[str, Any]:
    files = []
    for relative in sorted(paths):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"identity input missing: {relative}")
        files.append({"path": relative, "sha256": sha256_file(path)})
    document = {"algorithm": "sha256-canonical-file-manifest/v1", "files": files}
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "schema": schema,
        "algorithm": document["algorithm"],
        "root_sha256": hashlib.sha256(payload).hexdigest(),
        "files": files,
    }


def compute_asset_identity(root: Optional[Path] = None) -> Mapping[str, Any]:
    return _identity(
        (root or repository_root()).resolve(),
        ASSET_PATHS,
        "bisafecode.stage4.controlled.asset-identity/v1",
    )


def compute_contract_identity(root: Optional[Path] = None) -> Mapping[str, Any]:
    return _identity(
        (root or repository_root()).resolve(),
        CONTRACT_PATHS,
        "bisafecode.stage4.controlled.contract-identity/v1",
    )


def compute_toolchain_identity(root: Optional[Path] = None) -> Mapping[str, Any]:
    return _identity(
        (root or repository_root()).resolve(),
        TOOLCHAIN_PATHS,
        "bisafecode.stage4.controlled.toolchain-identity/v1",
    )


def verify_frozen_core(root: Optional[Path] = None) -> Mapping[str, Any]:
    root = (root or repository_root()).resolve()
    observed = {
        relative: sha256_file(root / relative)
        for relative in FROZEN_CORE_IDENTITIES
    }
    mismatches = {
        relative: {"expected": expected, "observed": observed[relative]}
        for relative, expected in FROZEN_CORE_IDENTITIES.items()
        if observed[relative] != expected
    }
    return {
        "schema": "bisafecode.stage4.controlled.frozen-core-audit/v1",
        "expected_count": len(FROZEN_CORE_IDENTITIES),
        "matching_count": len(FROZEN_CORE_IDENTITIES) - len(mismatches),
        "mismatches": mismatches,
        "passed": not mismatches,
        "files": [
            {
                "path": relative,
                "expected_sha256": FROZEN_CORE_IDENTITIES[relative],
                "observed_sha256": observed[relative],
            }
            for relative in sorted(FROZEN_CORE_IDENTITIES)
        ],
    }
