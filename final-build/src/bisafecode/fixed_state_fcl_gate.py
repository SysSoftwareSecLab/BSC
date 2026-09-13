"""Fail-closed input binding for a fixed-state attached-object FCL gate."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any, Mapping

from .geometry_inventory import canonical_json_bytes


SCHEMA = "bisafecode.fixed-state-carried-fcl-gate/v0.1"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{context} keys differ: {sorted(set(value) ^ expected)}")


def _repo_file(repo_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError(f"invalid repository-relative path: {relative}")
    lexical = repo_root.joinpath(*pure.parts)
    if lexical.is_symlink() or not lexical.is_file():
        raise ValueError(f"repository input is not a regular non-symlink file: {relative}")
    resolved_root = repo_root.resolve()
    resolved = lexical.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError(f"repository input escapes root: {relative}")
    return lexical


def _read_single_tsv(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    with path.open(newline="", encoding="ascii") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    if not header or len(rows) != 1 or any(value is None for value in rows[0].values()):
        raise ValueError(f"expected exactly one complete TSV row: {path.name}")
    return header, rows[0]


def _load_inventory(path: Path) -> dict[str, Any]:
    inventory = json.loads(path.read_text(encoding="ascii"))
    declared = inventory.pop("inventory_sha256")
    recomputed = hashlib.sha256(canonical_json_bytes(inventory)).hexdigest()
    inventory["inventory_sha256"] = declared
    if recomputed != declared:
        raise ValueError("inventory content hash does not match its declaration")
    return inventory


def _validate_archive_members(
    archive_path: Path,
    declarations: list[dict[str, Any]],
    repo_by_role: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    with tarfile.open(archive_path, "r:gz") as archive:
        names = [member.name for member in archive.getmembers()]
        if len(names) != len(set(names)):
            raise ValueError("provenance archive contains duplicate member names")
        for declaration in declarations:
            _require_keys(
                declaration,
                {"member_path", "sha256", "size_bytes", "matches_repo_role"},
                "archive member declaration",
            )
            member_path = declaration["member_path"]
            pure = PurePosixPath(member_path)
            if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
                raise ValueError(f"unsafe archive member path: {member_path}")
            member = archive.getmember(member_path)
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError(f"archive provenance member is not a regular file: {member_path}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot read archive provenance member: {member_path}")
            content = stream.read()
            actual_hash = hashlib.sha256(content).hexdigest()
            if member.size != declaration["size_bytes"] or actual_hash != declaration["sha256"]:
                raise ValueError(f"archive provenance member identity mismatch: {member_path}")
            role = declaration["matches_repo_role"]
            if role is not None:
                if role not in repo_by_role:
                    raise ValueError(f"archive member references unknown repository role: {role}")
                repo_path = repo_by_role[role]["absolute_path"]
                if content != repo_path.read_bytes():
                    raise ValueError(f"archive member differs from repository role {role}")
            output.append(
                {
                    "member_path": member_path,
                    "sha256": actual_hash,
                    "size_bytes": member.size,
                    "matches_repo_role": role,
                }
            )
    return output


def load_fixed_state_gate(repo_root: Path, spec_path: Path) -> dict[str, Any]:
    """Validate repository inputs and return a resolved immutable gate binding."""

    repo_root = repo_root.resolve()
    if spec_path.is_symlink() or not spec_path.is_file():
        raise ValueError("gate specification must be a regular non-symlink file")
    spec = json.loads(spec_path.read_text(encoding="ascii"))
    _require_keys(
        spec,
        {
            "schema",
            "experiment_id",
            "purpose",
            "scenario",
            "scope_id",
            "expected_checked_pairs",
            "probe_contract",
            "repo_inputs",
            "archive_provenance",
            "runtime_inputs",
            "acceptance",
            "prohibited_operations",
            "claim_boundary",
        },
        "gate specification",
    )
    if spec["schema"] != SCHEMA or spec["experiment_id"] != "EXP-S2-028":
        raise ValueError("gate schema or experiment identity differs")
    if spec["expected_checked_pairs"] != 365 or spec["scope_id"] != "robot-fixture-carried-base":
        raise ValueError("gate scope or checked-pair count differs")
    if spec["scenario"] != "left_operating":
        raise ValueError("gate scenario differs")
    _require_keys(
        spec["probe_contract"],
        {"request_type", "signed_distance", "nearest_points", "max_contacts_per_body"},
        "probe contract",
    )
    if spec["probe_contract"] != {
        "request_type": "SINGLE",
        "signed_distance": True,
        "nearest_points": True,
        "max_contacts_per_body": 1,
    }:
        raise ValueError("probe contract differs")

    repo_by_role: dict[str, dict[str, Any]] = {}
    repo_paths: set[str] = set()
    for declaration in spec["repo_inputs"]:
        _require_keys(declaration, {"role", "path", "sha256", "size_bytes"}, "repository input")
        role = declaration["role"]
        relative = declaration["path"]
        if role in repo_by_role or relative in repo_paths:
            raise ValueError("duplicate repository input role or path")
        path = _repo_file(repo_root, relative)
        if path.stat().st_size != declaration["size_bytes"] or sha256_path(path) != declaration["sha256"]:
            raise ValueError(f"repository input identity mismatch: {relative}")
        repo_by_role[role] = {**declaration, "absolute_path": path}
        repo_paths.add(relative)

    required_roles = {
        "state",
        "world",
        "attachment",
        "inventory",
        "probe_source",
        "probe_cmake",
        "coverage_validator",
        "gate_loader",
        "input_validator",
        "screening_binding",
        "source_archive",
    }
    if set(repo_by_role) != required_roles:
        raise ValueError(f"repository input roles differ: {sorted(set(repo_by_role) ^ required_roles)}")

    state_header, state = _read_single_tsv(repo_by_role["state"]["absolute_path"])
    if state_header[0] != "scenario" or state["scenario"] != spec["scenario"]:
        raise ValueError("fixed-state TSV scenario differs")
    if len(state_header) != 19:
        raise ValueError("fixed-state TSV must contain scenario plus 18 OpenArm variables")
    for name in state_header[1:]:
        try:
            value = float(state[name])
        except ValueError as exc:
            raise ValueError(f"invalid fixed-state value: {name}") from exc
        if not math.isfinite(value):
            raise ValueError(f"non-finite fixed-state value: {name}")

    _, attachment = _read_single_tsv(repo_by_role["attachment"]["absolute_path"])
    if (
        attachment.get("scenario") != "*"
        or attachment.get("body_id") != "carried_base"
        or attachment.get("parent_link") != "openarm_left_hand"
    ):
        raise ValueError("attachment semantic identity differs")

    inventory = _load_inventory(repo_by_role["inventory"]["absolute_path"])
    scopes = {item["scope_id"]: item for item in inventory["pair_scopes"]}
    scope = scopes.get(spec["scope_id"])
    if scope is None:
        raise ValueError("inventory does not contain the fixed gate scope")
    checked = [item for item in scope["pairs"] if item["disposition"] == "checked"]
    if len(checked) != spec["expected_checked_pairs"]:
        raise ValueError("inventory checked-pair count differs")

    _require_keys(spec["archive_provenance"], {"archive_role", "members"}, "archive provenance")
    archive_role = spec["archive_provenance"]["archive_role"]
    if archive_role != "source_archive":
        raise ValueError("archive provenance role differs")
    members = _validate_archive_members(
        repo_by_role[archive_role]["absolute_path"],
        spec["archive_provenance"]["members"],
        repo_by_role,
    )

    return {
        "spec": spec,
        "spec_path": spec_path.resolve(),
        "spec_sha256": sha256_path(spec_path),
        "repo_root": repo_root,
        "repo_inputs": repo_by_role,
        "archive_members": members,
        "inventory": inventory,
        "checked_pair_count": len(checked),
    }


def validate_runtime_geometry_inputs(
    binding: Mapping[str, Any],
    urdf_path: Path,
    srdf_path: Path,
    openarm_description_root: Path,
) -> dict[str, Any]:
    """Verify runtime model and every referenced OpenArm collision mesh by content."""

    runtime = binding["spec"]["runtime_inputs"]
    _require_keys(runtime, {"urdf", "srdf", "openarm_description_root"}, "runtime inputs")
    output: dict[str, Any] = {}
    for role, path in (("urdf", urdf_path), ("srdf", srdf_path)):
        declaration = runtime[role]
        _require_keys(declaration, {"expected_path", "sha256", "size_bytes"}, f"runtime {role}")
        if str(path) != declaration["expected_path"]:
            raise ValueError(f"runtime {role} path differs")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime {role} is not a regular non-symlink file")
        if path.stat().st_size != declaration["size_bytes"] or sha256_path(path) != declaration["sha256"]:
            raise ValueError(f"runtime {role} identity mismatch")
        output[role] = {"path": str(path), "sha256": declaration["sha256"], "size_bytes": declaration["size_bytes"]}

    root_declaration = runtime["openarm_description_root"]
    _require_keys(root_declaration, {"expected_path", "package_prefix"}, "description root")
    if str(openarm_description_root) != root_declaration["expected_path"] or not openarm_description_root.is_dir():
        raise ValueError("OpenArm description root differs or is absent")
    prefix = root_declaration["package_prefix"]
    mesh_expectations: dict[str, tuple[int, str]] = {}
    for entity in binding["inventory"]["entities"]:
        for geometry in entity["collision_geometries"]:
            uri = geometry.get("mesh_uri")
            if uri is None:
                continue
            if not uri.startswith(prefix):
                raise ValueError(f"unsupported collision mesh URI: {uri}")
            expectation = (geometry["mesh_size_bytes"], geometry["mesh_sha256"])
            if uri in mesh_expectations and mesh_expectations[uri] != expectation:
                raise ValueError(f"inconsistent inventory identity for mesh: {uri}")
            mesh_expectations[uri] = expectation
    if not mesh_expectations:
        raise ValueError("inventory declares no collision meshes")

    meshes = []
    for uri, (expected_size, expected_hash) in sorted(mesh_expectations.items()):
        relative = PurePosixPath(uri[len(prefix) :])
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError(f"unsafe collision mesh URI: {uri}")
        lexical = openarm_description_root.joinpath(*relative.parts)
        if not lexical.is_file():
            raise ValueError(f"runtime collision mesh is absent: {uri}")
        actual_size = lexical.stat().st_size
        actual_hash = sha256_path(lexical)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise ValueError(f"runtime collision mesh identity mismatch: {uri}")
        meshes.append(
            {
                "uri": uri,
                "path": str(lexical),
                "resolved_path": str(lexical.resolve()),
                "is_symlink": lexical.is_symlink(),
                "size_bytes": actual_size,
                "sha256": actual_hash,
            }
        )
    output["openarm_description_root"] = str(openarm_description_root)
    output["collision_meshes"] = meshes
    output["collision_mesh_count"] = len(meshes)
    return output
