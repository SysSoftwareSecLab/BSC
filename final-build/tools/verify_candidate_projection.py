#!/usr/bin/env python3
"""Verify the double-anonymous 114-file public build projection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EXPECTED_PUBLIC_ROOT = (
    "28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419"
)
PIPELINE_REDACTIONS = {
    "src/bisafecode/stage4_rare_schedule/pipeline.py",
    "src/bisafecode/stage4_rq3_stress/pipeline.py",
    "src/bisafecode/stage4_baseline_replication/pipeline.py",
    "src/bisafecode/stage4_baseline_correction/pipeline.py",
}
GUIDE_REDACTION = "src/bisafecode/stage4_external_blind/contracts.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest path: {relative}")
    target = root / path
    if target.is_symlink() or not target.is_file():
        raise ValueError(f"missing, non-regular, or symlinked payload: {relative}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()
    artifact_root = args.artifact_root.resolve()
    build_root = artifact_root / "final-build"
    manifest = json.loads(
        (build_root / "BUILD_MANIFEST.json").read_text(encoding="utf-8")
    )

    if manifest.get("schema") != "bisafecode.anonymous-public-build-projection/v1":
        raise ValueError("unexpected public build-manifest schema")
    public = manifest["public_projection"]
    public_records = sorted(public["files"], key=lambda item: item["path"])
    public_paths = [item["path"] for item in public_records]
    if len(public_records) != public["file_count"] or len(public_records) != 114:
        raise ValueError("public candidate file count does not equal 114")
    if len(set(public_paths)) != 114:
        raise ValueError("public candidate paths contain duplicates")

    observed_payload: set[str] = set()
    for subtree in (
        build_root / "src",
        build_root / "03_experiments" / "contracts",
    ):
        for path in subtree.rglob("*"):
            if path.is_symlink():
                raise ValueError(
                    f"symlink in candidate payload: {path.relative_to(build_root)}"
                )
            if path.is_file():
                observed_payload.add(path.relative_to(build_root).as_posix())
    if observed_payload != set(public_paths):
        missing = sorted(set(public_paths) - observed_payload)
        extra = sorted(observed_payload - set(public_paths))
        raise ValueError(f"candidate tree mismatch; missing={missing}; extra={extra}")

    identity_payload = []
    exact: list[str] = []
    redacted: list[str] = []
    for item in public_records:
        target = checked_path(build_root, item["path"])
        observed_size = target.stat().st_size
        observed_sha = sha256_file(target)
        if observed_size != item["size_bytes"] or observed_sha != item["sha256"]:
            raise ValueError(f"public candidate payload mismatch: {item['path']}")
        status = item.get("projection_status")
        if status == "byte_exact":
            exact.append(item["path"])
        elif status == "privacy_redacted":
            redacted.append(item["path"])
        else:
            raise ValueError(f"unknown projection status: {item['path']}")
        identity_payload.append({"path": item["path"], "sha256": observed_sha})

    observed_root = canonical_sha256(identity_payload)
    if observed_root != public["root_sha256"] or observed_root != EXPECTED_PUBLIC_ROOT:
        raise ValueError(
            f"public-projection root mismatch: expected {public['root_sha256']}, "
            f"observed {observed_root}"
        )
    expected_redacted = PIPELINE_REDACTIONS | {GUIDE_REDACTION}
    if set(redacted) != expected_redacted or len(exact) != 109:
        raise ValueError("privacy-projection count or path set changed")

    privacy = json.loads(
        (build_root / "PRIVACY_REDACTIONS.json").read_text(encoding="utf-8")
    )
    if privacy.get("public_projection_root_sha256") != EXPECTED_PUBLIC_ROOT:
        raise ValueError("privacy record identity mismatch")
    recorded_redactions = {
        item["path"]: item for item in privacy["candidate_redactions"]
    }
    if set(recorded_redactions) != expected_redacted:
        raise ValueError("privacy-redaction record does not match projected files")
    if any("original_sha256" in item for item in privacy["candidate_redactions"]):
        raise ValueError("unsalted pre-redaction digest leaked into public record")

    for path in PIPELINE_REDACTIONS:
        text = checked_path(build_root, path).read_text(encoding="utf-8")
        if len(re.findall(r'"reviewer"\s*:\s*"Anonymous User"', text)) != 1:
            raise ValueError(f"anonymous reviewer metadata missing: {path}")
    stress_text = checked_path(
        build_root, "src/bisafecode/stage4_rq3_stress/pipeline.py"
    ).read_text(encoding="utf-8")
    neutral_authorization = (
        "authorization recorded before one-time stress run; source privately retained"
    )
    authorization_matches = re.findall(
        r'"authorization_source"\s*:\s*\(\s*"([^"]+)"\s*\)', stress_text
    )
    if authorization_matches != [neutral_authorization]:
        raise ValueError("neutral public authorization-source metadata is missing")
    guide_text = checked_path(build_root, GUIDE_REDACTION).read_text(encoding="utf-8")
    if guide_text.count("ANONYMOUS_HUMAN_COLLECTION_GUIDE.md") != 1:
        raise ValueError("anonymous guide filename is missing or duplicated")

    import_boundary = privacy["mac_r2_actual_import_boundary"]
    if (
        import_boundary.get("module_count") != 19
        or import_boundary.get("authorization_metadata_pipeline_intersection") != []
        or import_boundary.get("all_candidate_redaction_paths_intersection") != []
    ):
        raise ValueError("Mac R2 actual-import boundary is not closed")
    imports = json.loads(
        (artifact_root / "results/final-revalidation/cohorts/method_imports.json")
        .read_text(encoding="utf-8")
    )
    if (
        imports.get("public_projection_root_sha256") != EXPECTED_PUBLIC_ROOT
        or len(imports.get("imports", [])) != 19
    ):
        raise ValueError("public actual-import record identity mismatch")
    for item in imports["imports"]:
        if item["path"] in expected_redacted:
            raise ValueError(f"redacted module was imported: {item['path']}")
        target = checked_path(build_root, item["path"])
        if sha256_file(target) != item["sha256"]:
            raise ValueError(f"actual-import source mismatch: {item['path']}")

    for duplicate in privacy["published_duplicates"]:
        target = checked_path(artifact_root, duplicate["artifact_path"])
        candidate_source = checked_path(build_root, duplicate["duplicates_candidate_path"])
        if (
            target.stat().st_size != duplicate["size_bytes"]
            or sha256_file(target) != duplicate["public_sha256"]
            or target.read_bytes() != candidate_source.read_bytes()
        ):
            raise ValueError(f"redacted baseline duplicate differs: {duplicate['artifact_path']}")

    mirror = manifest["controlled_contract_mirror"]
    mirror_records = sorted(mirror["files"], key=lambda item: item["path"])
    if len(mirror_records) != mirror["file_count"] or len(mirror_records) != 18:
        raise ValueError("controlled-contract mirror count does not equal 18")
    for item in mirror_records:
        target = checked_path(artifact_root, item["artifact_path"])
        source = checked_path(build_root, item["candidate_path"])
        if (
            target.stat().st_size != item["size_bytes"]
            or sha256_file(target) != item["sha256"]
            or target.read_bytes() != source.read_bytes()
        ):
            raise ValueError(f"controlled-contract mirror mismatch: {item['path']}")

    print(f"PASS: anonymous 114-file projection root {public['root_sha256']}")
    print("PASS: 109 byte-exact files + 5 disclosed privacy redactions")
    print("PASS: 19 imported modules are byte-exact and exclude all redacted paths")
    print("PASS: 2 published baseline duplicates match their redacted source modules")
    print(f"PASS: {len(mirror_records)} controlled-contract mirror files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
