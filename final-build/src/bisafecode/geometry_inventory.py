"""Content-addressed OpenArm collision inventory and pair-scope construction.

This module deliberately stops before producing a paper-eligible geometry
manifest.  It inventories the real URDF/SRDF/mesh/fixture assets and creates
complete case-scoped pair universes, while marking speed envelopes and error
bounds as unresolved obligations.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


class GeometryInventoryError(ValueError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _numbers(value: Optional[str], length: int, default: Sequence[float]) -> Tuple[float, ...]:
    if value is None:
        result = tuple(default)
    else:
        try:
            result = tuple(float(item) for item in value.split())
        except ValueError as exc:
            raise GeometryInventoryError(f"invalid numeric vector: {value}") from exc
    if len(result) != length or any(not math.isfinite(item) for item in result):
        raise GeometryInventoryError(f"expected {length} finite values, got {value}")
    return result


def _geometry_record(
    collision: ET.Element,
    mesh_loader: Callable[[str], bytes],
) -> dict:
    origin = collision.find("origin")
    geometry = collision.find("geometry")
    if geometry is None or len(geometry) != 1:
        raise GeometryInventoryError("each collision element must contain exactly one geometry")
    shape = geometry[0]
    record = {
        "collision_name": collision.get("name", ""),
        "origin_xyz_m": list(_numbers(origin.get("xyz") if origin is not None else None, 3, (0.0,) * 3)),
        "origin_rpy_rad": list(_numbers(origin.get("rpy") if origin is not None else None, 3, (0.0,) * 3)),
        "shape": shape.tag,
    }
    if shape.tag == "mesh":
        uri = shape.get("filename")
        if not uri:
            raise GeometryInventoryError("mesh collision is missing filename")
        content = mesh_loader(uri)
        record.update(
            {
                "mesh_uri": uri,
                "mesh_scale": list(_numbers(shape.get("scale"), 3, (1.0,) * 3)),
                "mesh_size_bytes": len(content),
                "mesh_sha256": sha256_bytes(content),
            }
        )
    elif shape.tag == "box":
        record["size_m"] = list(_numbers(shape.get("size"), 3, ()))
    elif shape.tag == "sphere":
        record["radius_m"] = float(shape.get("radius", "nan"))
    elif shape.tag == "cylinder":
        record["radius_m"] = float(shape.get("radius", "nan"))
        record["length_m"] = float(shape.get("length", "nan"))
    else:
        raise GeometryInventoryError(f"unsupported collision geometry: {shape.tag}")
    for key in ("radius_m", "length_m"):
        if key in record and (not math.isfinite(record[key]) or record[key] <= 0.0):
            raise GeometryInventoryError(f"invalid {key}")
    record["geometry_sha256"] = sha256_bytes(canonical_json_bytes(record))
    return record


def _box_entity(entity_id: str, center: Sequence[float], size: Sequence[float], *, role: str) -> dict:
    center_tuple = _numbers(" ".join(str(item) for item in center), 3, ())
    size_tuple = _numbers(" ".join(str(item) for item in size), 3, ())
    if any(value <= 0.0 for value in size_tuple):
        raise GeometryInventoryError(f"world box {entity_id} has non-positive size")
    geometry = {
        "shape": "box",
        "center_m": list(center_tuple),
        "size_m": list(size_tuple),
    }
    return {
        "entity_id": entity_id,
        "kind": "world",
        "motion_source": "static",
        "role": role,
        "collision_geometries": [geometry],
        "geometry_sha256": sha256_bytes(canonical_json_bytes(geometry)),
        "kinematic_dependencies": [],
        "speed_envelope_status": "not-applicable-static",
    }


def _fixture_entities(scene_spec: Mapping[str, object]) -> Tuple[dict, ...]:
    try:
        fixture = scene_spec["fixture"]
        base = fixture["base"]
        socket = fixture["socket"]
        base_center = tuple(base["center_m"])
        socket_center = tuple(socket["center_m"])
        inner = tuple(socket["inner_size_m"])
        thickness = float(socket["wall_thickness_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryInventoryError("fixture scene is missing required dimensions") from exc
    if thickness <= 0.0:
        raise GeometryInventoryError("fixture wall thickness must be positive")
    x_offset = inner[0] / 2.0 + thickness / 2.0
    y_offset = inner[1] / 2.0 + thickness / 2.0
    height = inner[2]
    entities = [
        _box_entity("fixture_base", base_center, tuple(base["size_m"]), role="fixture"),
        _box_entity(
            "socket_wall_x_minus",
            (socket_center[0] - x_offset, socket_center[1], socket_center[2]),
            (thickness, inner[1] + 2.0 * thickness, height),
            role="fixture",
        ),
        _box_entity(
            "socket_wall_x_plus",
            (socket_center[0] + x_offset, socket_center[1], socket_center[2]),
            (thickness, inner[1] + 2.0 * thickness, height),
            role="fixture",
        ),
        _box_entity(
            "socket_wall_y_minus",
            (socket_center[0], socket_center[1] - y_offset, socket_center[2]),
            (inner[0], thickness, height),
            role="fixture",
        ),
        _box_entity(
            "socket_wall_y_plus",
            (socket_center[0], socket_center[1] + y_offset, socket_center[2]),
            (inner[0], thickness, height),
            role="fixture",
        ),
        _box_entity("seated_base", (0.2, 0.0, 0.105), (0.08, 0.06, 0.03), role="seated-object"),
    ]
    return tuple(sorted(entities, key=lambda item: item["entity_id"]))


def _attached_entity() -> dict:
    geometry = {
        "shape": "box",
        "size_m": [0.08, 0.06, 0.03],
        "attachment_parent": "openarm_left_hand",
        "attachment_transform_status": "case-specific-pending",
    }
    return {
        "entity_id": "carried_base",
        "kind": "attached-object",
        "motion_source": "left-chain",
        "role": "carried-object",
        "collision_geometries": [geometry],
        "geometry_sha256": sha256_bytes(canonical_json_bytes(geometry)),
        "kinematic_dependencies": [
            *(f"openarm_left_joint{index}" for index in range(1, 8)),
        ],
        "touch_links": [
            "openarm_left_hand",
            "openarm_left_left_finger",
            "openarm_left_right_finger",
        ],
        "speed_envelope_status": "pending-audited-coefficients",
    }


def _robot_entities(
    root: ET.Element,
    mesh_loader: Callable[[str], bytes],
) -> Tuple[dict, ...]:
    parent_joint: Dict[str, ET.Element] = {}
    for joint in root.findall("joint"):
        child = joint.find("child")
        if child is None or not child.get("link"):
            raise GeometryInventoryError("joint is missing a child link")
        parent_joint[child.get("link")] = joint

    def dependencies(link_name: str) -> Tuple[str, ...]:
        values = []
        seen = set()
        current = link_name
        while current in parent_joint:
            joint = parent_joint[current]
            name = joint.get("name")
            kind = joint.get("type")
            if kind != "fixed":
                mimic = joint.find("mimic")
                dependency = mimic.get("joint") if mimic is not None else name
                if not dependency or dependency in seen:
                    raise GeometryInventoryError(f"invalid kinematic dependency for {link_name}")
                seen.add(dependency)
                values.append(dependency)
            parent = joint.find("parent")
            if parent is None or not parent.get("link"):
                raise GeometryInventoryError("joint is missing a parent link")
            current = parent.get("link")
        return tuple(sorted(values))

    entities = []
    for link in root.findall("link"):
        collisions = link.findall("collision")
        if not collisions:
            continue
        name = link.get("name")
        if not name:
            raise GeometryInventoryError("collision-bearing link is unnamed")
        if name == "openarm_body_link0":
            kind = "fixed-robot"
            motion_source = "static"
        elif name.startswith("openarm_left_"):
            kind = "left-link"
            motion_source = "left-chain"
        elif name.startswith("openarm_right_"):
            kind = "right-link"
            motion_source = "right-chain"
        else:
            raise GeometryInventoryError(f"unclassified collision-bearing link: {name}")
        geometries = tuple(_geometry_record(item, mesh_loader) for item in collisions)
        dependency_values = dependencies(name)
        if kind == "fixed-robot" and dependency_values:
            raise GeometryInventoryError("fixed robot body unexpectedly depends on a movable joint")
        entity_record = {
            "entity_id": name,
            "kind": kind,
            "motion_source": motion_source,
            "role": "robot-link",
            "collision_geometries": list(geometries),
            "geometry_sha256": sha256_bytes(canonical_json_bytes(list(geometries))),
            "kinematic_dependencies": list(dependency_values),
            "speed_envelope_status": (
                "not-applicable-static" if motion_source == "static" else "pending-audited-coefficients"
            ),
        }
        entities.append(entity_record)
    return tuple(sorted(entities, key=lambda item: item["entity_id"]))


def _disabled_pairs(srdf_root: ET.Element) -> Dict[Tuple[str, str], dict]:
    result = {}
    for element in srdf_root.findall("disable_collisions"):
        first = element.get("link1")
        second = element.get("link2")
        if not first or not second or first == second:
            raise GeometryInventoryError("invalid SRDF disabled-collision pair")
        pair = tuple(sorted((first, second)))
        if pair in result:
            raise GeometryInventoryError(f"duplicate SRDF disabled pair: {pair}")
        result[pair] = {
            "reason": element.get("reason", "unspecified"),
            "evidence_ref": "active-srdf:disable_collisions",
        }
    return result


def _pair_scope(
    name: str,
    entities: Iterable[dict],
    disabled: Mapping[Tuple[str, str], dict],
    touch_pairs: Iterable[Tuple[str, str]] = (),
) -> dict:
    by_id = {item["entity_id"]: item for item in entities}
    touch = {tuple(sorted(item)) for item in touch_pairs}
    pairs = []
    for first, second in itertools.combinations(sorted(by_id), 2):
        if by_id[first]["kind"] == "world" and by_id[second]["kind"] == "world":
            continue
        pair = (first, second)
        if pair in disabled:
            disposition = "excluded"
            rationale = f"SRDF:{disabled[pair]['reason']}"
            evidence_ref = disabled[pair]["evidence_ref"]
        elif pair in touch:
            disposition = "excluded"
            rationale = "case-scoped attached-body touch relation"
            evidence_ref = "A3-C:carried_base:touch_links"
        else:
            disposition = "checked"
            rationale = "complete non-world-world pair universe"
            evidence_ref = "generated:complete-pair-enumeration"
        pairs.append(
            {
                "entity_pair": [first, second],
                "query_scope": "world" if "world" in (by_id[first]["kind"], by_id[second]["kind"]) else "self",
                "disposition": disposition,
                "rationale": rationale,
                "evidence_ref": evidence_ref,
            }
        )
    checked = sum(item["disposition"] == "checked" for item in pairs)
    excluded = len(pairs) - checked
    return {
        "scope_id": name,
        "entity_ids": sorted(by_id),
        "pairs": pairs,
        "pair_census": {
            "total_non_world_world": len(pairs),
            "checked": checked,
            "excluded": excluded,
        },
    }


def build_openarm_asset_inventory(
    *,
    urdf_bytes: bytes,
    srdf_bytes: bytes,
    scene_spec: Mapping[str, object],
    replay_probe_bytes: bytes,
    mesh_loader: Callable[[str], bytes],
    source_identity: Mapping[str, object],
) -> dict:
    try:
        urdf_root = ET.fromstring(urdf_bytes)
        srdf_root = ET.fromstring(srdf_bytes)
    except ET.ParseError as exc:
        raise GeometryInventoryError("URDF/SRDF XML parse failure") from exc
    robot = _robot_entities(urdf_root, mesh_loader)
    if len(robot) != 23:
        raise GeometryInventoryError(f"expected 23 OpenArm collision-bearing robot links, got {len(robot)}")
    fixture = _fixture_entities(scene_spec)
    attached = _attached_entity()
    disabled = _disabled_pairs(srdf_root)
    robot_ids = {item["entity_id"] for item in robot}
    unknown_disabled = sorted({name for pair in disabled for name in pair if name not in robot_ids})
    if unknown_disabled:
        raise GeometryInventoryError(f"SRDF excludes unknown collision links: {unknown_disabled}")
    empty_world = tuple(item for item in fixture if item["entity_id"] != "seated_base")
    seated_world = fixture
    touch_pairs = tuple((attached["entity_id"], item) for item in attached["touch_links"])
    scopes = (
        _pair_scope("robot-fixture-empty", (*robot, *empty_world), disabled),
        _pair_scope("robot-fixture-seated", (*robot, *seated_world), disabled),
        _pair_scope("robot-fixture-carried-base", (*robot, *empty_world, attached), disabled, touch_pairs),
    )
    record = {
        "schema": "bisafecode.openarm-geometry-asset-inventory/v0.1",
        "status": "STRUCTURAL_ASSET_INVENTORY_COMPLETE_NUMERIC_CERTIFICATE_INPUTS_PENDING",
        "paper_result_eligible": False,
        "source_identity": dict(source_identity),
        "source_artifacts": {
            "resolved_urdf_sha256": sha256_bytes(urdf_bytes),
            "active_srdf_sha256": sha256_bytes(srdf_bytes),
            "fixture_scene_spec_sha256": sha256_bytes(canonical_json_bytes(scene_spec)),
            "a3c_replay_probe_sha256": sha256_bytes(replay_probe_bytes),
        },
        "entities": [*robot, *fixture, attached],
        "entity_census": {
            "robot_collision_links": len(robot),
            "fixed_robot": sum(item["kind"] == "fixed-robot" for item in robot),
            "left_links": sum(item["kind"] == "left-link" for item in robot),
            "right_links": sum(item["kind"] == "right-link" for item in robot),
            "fixture_world_objects": len(fixture) - 1,
            "conditional_seated_world_objects": 1,
            "candidate_attached_objects": 1,
            "unique_referenced_robot_meshes": len(
                {
                    geometry["mesh_sha256"]
                    for entity in robot
                    for geometry in entity["collision_geometries"]
                    if geometry["shape"] == "mesh"
                }
            ),
        },
        "srdf_disabled_pairs": [
            {"entity_pair": list(pair), **details} for pair, details in sorted(disabled.items())
        ],
        "pair_scopes": list(scopes),
        "unresolved_certificate_inputs": [
            "case-specific C1-C4 trajectory and attached-body transform",
            "audited per-entity joint/gripper speed-envelope coefficients",
            "MoveIt/FCL numerical signed-distance error bound",
            "collision-mesh geometric approximation error bound",
            "task clearance policy",
            "runtime proof that DistanceRequestType::SINGLE returns the exact checked pair set",
        ],
    }
    record["inventory_sha256"] = sha256_bytes(canonical_json_bytes(record))
    return record
