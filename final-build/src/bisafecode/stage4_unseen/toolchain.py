"""Content-addressed identity for the complete Stage 4 prep toolchain.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


TOOLCHAIN_PATHS = (
    "src/bisafecode/stage4_unseen/__init__.py",
    "src/bisafecode/stage4_unseen/canonicalize.py",
    "src/bisafecode/stage4_unseen/generator.py",
    "src/bisafecode/stage4_unseen/leakage.py",
    "src/bisafecode/stage4_unseen/metrics.py",
    "src/bisafecode/stage4_unseen/pipeline.py",
    "src/bisafecode/stage4_unseen/schema.py",
    "src/bisafecode/stage4_unseen/time_bounds.py",
    "src/bisafecode/stage4_unseen/toolchain.py",
    "03_experiments/contracts/PROGRAM_GRAMMAR_AND_FAMILY_SPEC.json",
    "03_experiments/contracts/DATA_SPLIT_AND_LEAKAGE_POLICY.json",
    "03_experiments/results/EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP/schemas/program_record.schema.json",
    "03_experiments/results/EXP-S4-001_UNSEEN_PROGRAM_CORRECTNESS_PREP/schemas/run_record.schema.json",
    "tools/stage4_unseen_analyze.py",
    "tools/stage4_unseen_check_leakage.py",
    "tools/stage4_unseen_prepare_fixture.py",
    "tools/stage4_unseen_validate.py",
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def compute_toolchain_identity(
    root: Optional[Path] = None, paths: Sequence[str] = TOOLCHAIN_PATHS
) -> Mapping[str, Any]:
    """Hash every bound file and then hash their canonical manifest."""

    root = (root or repository_root()).resolve()
    files = []
    for relative in sorted(paths):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError("toolchain identity input missing: {}".format(relative))
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    document = {
        "algorithm": "sha256-canonical-file-manifest/v1",
        "files": files,
    }
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "schema": "bisafecode.stage4.unseen.toolchain-identity/v1",
        "root_sha256": hashlib.sha256(payload).hexdigest(),
        "files": files,
    }
