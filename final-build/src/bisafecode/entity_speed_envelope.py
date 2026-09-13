"""Conservative OpenArm entity-speed coefficients from frozen URDF and meshes.

For a revolute ancestor joint, the linear speed of any point on a downstream
collision entity is bounded by ``radius_from_joint * abs(qdot)``.  This module
computes a configuration-independent radius using triangle inequality over
the URDF chain and the farthest collision-mesh vertex.  Every floating result
is rounded upward.  It does not calibrate FCL numerical error or physical
robot/model discrepancy.
"""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
import struct
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence


SCHEMA = "bisafecode.openarm-entity-speed-envelopes/v0.1"
PRIMARY_ARCHIVE_SHA256 = "f461ac02a6b0da76bec2c3af045c8f4518282c609b6e630a456d45a8dfdaa6e7"
SUPPLEMENT_ARCHIVE_SHA256 = "590a2db9d99f50bd41763da8c44fd2dbd9cfd24817a180e36c8a5ea5ef0d88ba"
INVENTORY_FILE_SHA256 = "f16d63b2d7e2688ecc8a15b9db55c252c223c419f7030ea56d1712cacbad03a9"
URDF_MEMBER = "gate_a_probe/outputs/openarm_v1_bimanual_resolved.urdf"
SUPPLEMENT_PREFIX = (
    "bisafecode_gate_a3c_supplement_20260807_actual_oracle_v3/"
    "openarm_description/"
)
ARM_JOINTS = {
    arm: tuple(f"openarm_{arm}_joint{index}" for index in range(1, 8))
    for arm in ("left", "right")
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def _up(value: float) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("speed-envelope value must be finite and non-negative")
    return math.nextafter(value, math.inf)


def _fraction_to_binary64_up(value: Fraction) -> float:
    """Return the least adjacent binary64 value known to be >= ``value``."""

    if value < 0:
        raise ValueError("outward-rounded value must be non-negative")
    candidate = float(value)
    if not math.isfinite(candidate):
        raise ValueError("outward-rounded value must be finite")
    if Fraction.from_float(candidate) < value:
        candidate = math.nextafter(candidate, math.inf)
    if Fraction.from_float(candidate) < value:
        raise AssertionError("one adjacent binary64 step did not enclose rational value")
    return candidate


def _sum_up(values: Iterable[float]) -> float:
    exact = Fraction(0)
    for value in values:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("speed-envelope summand must be finite and non-negative")
        exact += Fraction.from_float(value)
    return _fraction_to_binary64_up(exact)


def _norm(values: Iterable[float]) -> float:
    vector = tuple(float(value) for value in values)
    if len(vector) != 3 or any(not math.isfinite(value) for value in vector):
        raise ValueError("expected one finite 3-vector")
    squared = sum(
        (Fraction.from_float(value) ** 2 for value in vector), Fraction(0)
    )
    candidate = math.sqrt(float(squared))
    while Fraction.from_float(candidate) ** 2 < squared:
        candidate = math.nextafter(candidate, math.inf)
    if not math.isfinite(candidate):
        raise ValueError("vector norm must be finite")
    return candidate


def _xyz(value: str | None) -> tuple[float, float, float]:
    if not value:
        return (0.0, 0.0, 0.0)
    parsed = tuple(float(item) for item in value.split())
    if len(parsed) != 3 or any(not math.isfinite(item) for item in parsed):
        raise ValueError("URDF xyz must contain three finite values")
    return parsed


def stl_vertices(data: bytes) -> tuple[tuple[float, float, float], ...]:
    """Return all binary or ASCII STL vertices without changing float values."""

    if len(data) >= 84:
        triangle_count = struct.unpack_from("<I", data, 80)[0]
        if 84 + triangle_count * 50 == len(data):
            vertices = []
            offset = 84
            for _index in range(triangle_count):
                for vertex_index in range(3):
                    vertices.append(
                        struct.unpack_from("<fff", data, offset + 12 + vertex_index * 12)
                    )
                offset += 50
            return tuple(vertices)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("STL is neither valid binary nor ASCII") from error
    vertices = []
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) == 4 and fields[0].lower() == "vertex":
            vertex = tuple(float(item) for item in fields[1:])
            if any(not math.isfinite(item) for item in vertex):
                raise ValueError("ASCII STL contains non-finite vertex")
            vertices.append(vertex)
    if not vertices:
        raise ValueError("STL contains no vertices")
    return tuple(vertices)


def mesh_origin_radius_m(data: bytes, scale: Sequence[float]) -> float:
    if len(scale) != 3:
        raise ValueError("mesh scale must contain three values")
    scale_values = tuple(float(item) for item in scale)
    if any(not math.isfinite(item) or item == 0.0 for item in scale_values):
        raise ValueError("mesh scale must be finite and nonzero")
    maximum = 0.0
    for vertex in stl_vertices(data):
        maximum = max(
            maximum,
            _norm(value * factor for value, factor in zip(vertex, scale_values)),
        )
    return _up(maximum)


@dataclass(frozen=True)
class JointEdge:
    name: str
    dependency_name: str
    joint_type: str
    parent: str
    child: str
    origin_norm_m: float


def parse_urdf_tree(urdf_bytes: bytes) -> tuple[set[str], dict[str, JointEdge]]:
    try:
        root = ET.fromstring(urdf_bytes)
    except ET.ParseError as error:
        raise ValueError("resolved URDF is invalid XML") from error
    if root.tag != "robot":
        raise ValueError("URDF root must be robot")
    links = {element.attrib["name"] for element in root.findall("link")}
    by_child: dict[str, JointEdge] = {}
    names = set()
    for element in root.findall("joint"):
        name = element.attrib.get("name", "")
        joint_type = element.attrib.get("type", "")
        parent_element = element.find("parent")
        child_element = element.find("child")
        if not name or not joint_type or parent_element is None or child_element is None:
            raise ValueError("URDF joint identity is incomplete")
        parent = parent_element.attrib.get("link", "")
        child = child_element.attrib.get("link", "")
        origin = element.find("origin")
        mimic = element.find("mimic")
        dependency_name = mimic.attrib.get("joint", "") if mimic is not None else name
        if not dependency_name:
            raise ValueError("URDF mimic joint has no driving joint")
        edge = JointEdge(
            name,
            dependency_name,
            joint_type,
            parent,
            child,
            _norm(_xyz(origin.attrib.get("xyz") if origin is not None else None)),
        )
        if name in names or child in by_child:
            raise ValueError("URDF joint names and child links must be unique")
        if parent not in links or child not in links:
            raise ValueError("URDF joint references an unknown link")
        names.add(name)
        by_child[child] = edge
    return links, by_child


def chain_to_root(link: str, by_child: Mapping[str, JointEdge]) -> tuple[JointEdge, ...]:
    result = []
    seen = set()
    current = link
    while current in by_child:
        if current in seen:
            raise ValueError("URDF kinematic tree contains a cycle")
        seen.add(current)
        edge = by_child[current]
        result.append(edge)
        current = edge.parent
    result.reverse()
    return tuple(result)


def _entity_collision_radius(
    entity: Mapping[str, object], mesh_loader: Callable[[str], bytes]
) -> tuple[float, list[dict[str, object]]]:
    maximum = 0.0
    evidence = []
    geometries = entity.get("collision_geometries")
    if not isinstance(geometries, list) or not geometries:
        raise ValueError(f"moving entity {entity.get('entity_id')} has no collision geometry")
    for geometry in geometries:
        if geometry.get("shape") != "mesh":
            raise ValueError("moving OpenArm entity must use a frozen mesh geometry")
        uri = str(geometry["mesh_uri"])
        data = mesh_loader(uri)
        actual_hash = sha256_bytes(data)
        if actual_hash != geometry["mesh_sha256"]:
            raise ValueError(f"mesh identity changed: {uri}")
        mesh_radius = mesh_origin_radius_m(data, geometry["mesh_scale"])
        origin_radius = _norm(geometry["origin_xyz_m"])
        combined = _sum_up((mesh_radius, origin_radius))
        maximum = max(maximum, combined)
        evidence.append(
            {
                "mesh_uri": uri,
                "mesh_sha256": actual_hash,
                "mesh_vertex_radius_m": mesh_radius,
                "collision_origin_radius_m": origin_radius,
                "link_origin_collision_radius_m": combined,
            }
        )
    return _up(maximum), evidence


def derive_entity_speed_envelopes(
    *,
    urdf_bytes: bytes,
    inventory: Mapping[str, object],
    mesh_loader: Callable[[str], bytes],
    scope_id: str = "robot-fixture-empty",
) -> dict[str, object]:
    links, by_child = parse_urdf_tree(urdf_bytes)
    scopes = [item for item in inventory["pair_scopes"] if item["scope_id"] == scope_id]
    if len(scopes) != 1:
        raise ValueError("geometry inventory scope is missing or duplicated")
    entity_ids = tuple(scopes[0]["entity_ids"])
    by_entity = {item["entity_id"]: item for item in inventory["entities"]}
    if set(entity_ids) - set(by_entity):
        raise ValueError("geometry scope references an unknown entity")

    records = []
    for entity_id in entity_ids:
        entity = by_entity[entity_id]
        motion_source = entity["motion_source"]
        if motion_source == "static":
            records.append(
                {
                    "entity_id": entity_id,
                    "motion_source": motion_source,
                    "left_joint_m_per_rad": [0.0] * 7,
                    "right_joint_m_per_rad": [0.0] * 7,
                    "left_gripper_m_per_m": 0.0,
                    "right_gripper_m_per_m": 0.0,
                    "derivation": "static entity",
                }
            )
            continue
        if entity_id not in links:
            raise ValueError(f"moving inventory entity is not a URDF link: {entity_id}")
        chain = chain_to_root(entity_id, by_child)
        nonfixed_names = {
            edge.dependency_name
            for edge in chain
            if edge.joint_type not in {"fixed", "floating"}
        }
        declared = set(entity["kinematic_dependencies"])
        if nonfixed_names != declared:
            raise ValueError(
                f"kinematic dependency mismatch for {entity_id}: "
                f"URDF={sorted(nonfixed_names)} inventory={sorted(declared)}"
            )
        collision_radius, geometry_evidence = _entity_collision_radius(
            entity, mesh_loader
        )
        coefficients = {name: 0.0 for names in ARM_JOINTS.values() for name in names}
        for index, edge in enumerate(chain):
            if edge.dependency_name not in coefficients:
                continue
            if edge.joint_type not in {"revolute", "continuous"}:
                raise ValueError(f"arm joint is not revolute: {edge.name}")
            downstream = collision_radius
            for later in chain[index + 1 :]:
                downstream = _sum_up((downstream, later.origin_norm_m))
            coefficients[edge.dependency_name] = downstream
        gripper_coefficients = {"left": 0.0, "right": 0.0}
        for edge in chain:
            if "finger_joint" not in edge.dependency_name:
                continue
            if edge.joint_type not in {"prismatic", "revolute", "continuous"}:
                raise ValueError(f"unsupported gripper joint type: {edge.name}")
            arm = "left" if "_left_" in edge.dependency_name else "right"
            # OpenArm exposes finger position as a scalar length-like state in
            # the Stage-2 abstraction.  One prismatic descendant contributes
            # at most one metre per metre of commanded finger displacement.
            if edge.joint_type != "prismatic":
                raise ValueError("RH100 gripper speed coefficient requires prismatic URDF joint")
            gripper_coefficients[arm] = _sum_up(
                (gripper_coefficients[arm], 1.0)
            )
        records.append(
            {
                "entity_id": entity_id,
                "motion_source": motion_source,
                "left_joint_m_per_rad": [coefficients[name] for name in ARM_JOINTS["left"]],
                "right_joint_m_per_rad": [coefficients[name] for name in ARM_JOINTS["right"]],
                "left_gripper_m_per_m": gripper_coefficients["left"],
                "right_gripper_m_per_m": gripper_coefficients["right"],
                "link_origin_collision_radius_m": collision_radius,
                "chain": [
                    {
                        "joint": edge.name,
                        "dependency_joint": edge.dependency_name,
                        "type": edge.joint_type,
                        "parent": edge.parent,
                        "child": edge.child,
                        "origin_norm_m": edge.origin_norm_m,
                    }
                    for edge in chain
                ],
                "geometry_evidence": geometry_evidence,
                "derivation": "triangle inequality over downstream URDF translations plus farthest scaled collision-mesh vertex",
            }
        )
    return {
        "schema": SCHEMA,
        "scope_id": scope_id,
        "resolved_urdf_sha256": sha256_bytes(urdf_bytes),
        "inventory_sha256": inventory.get("inventory_sha256"),
        "entity_count": len(records),
        "entities": records,
        "bound_scope": "configuration-independent upper bound for the frozen URDF collision geometry",
        "not_included": [
            "FCL signed-distance numerical error",
            "physical robot versus mesh approximation error",
            "trajectory interpolation Cartesian error",
        ],
        "paper_result_eligible": False,
    }


def derive_from_frozen_archives(
    *,
    primary_archive_path: Path,
    supplement_archive_path: Path,
    inventory_path: Path,
) -> dict[str, object]:
    """Rebuild the envelope artifact without extracting archive trees to disk."""

    for path, expected, label in (
        (primary_archive_path, PRIMARY_ARCHIVE_SHA256, "primary archive"),
        (supplement_archive_path, SUPPLEMENT_ARCHIVE_SHA256, "supplement archive"),
        (inventory_path, INVENTORY_FILE_SHA256, "geometry inventory"),
    ):
        if sha256_path(path) != expected:
            raise ValueError(f"{label} identity changed")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    with tarfile.open(primary_archive_path, "r:gz") as primary, tarfile.open(
        supplement_archive_path, "r:gz"
    ) as supplement:
        urdf_info = primary.getmember(URDF_MEMBER)
        if not urdf_info.isfile():
            raise ValueError("resolved URDF archive member is not a regular file")
        urdf_stream = primary.extractfile(urdf_info)
        if urdf_stream is None:
            raise ValueError("resolved URDF archive member cannot be read")
        urdf_bytes = urdf_stream.read()
        cache: dict[str, bytes] = {}

        def mesh_loader(uri: str) -> bytes:
            prefix = "package://openarm_description/"
            if not uri.startswith(prefix):
                raise ValueError(f"unsupported OpenArm mesh URI: {uri}")
            member_name = SUPPLEMENT_PREFIX + uri[len(prefix) :]
            if member_name not in cache:
                info = supplement.getmember(member_name)
                if not info.isfile():
                    raise ValueError("collision mesh archive member is not a regular file")
                stream = supplement.extractfile(info)
                if stream is None:
                    raise ValueError("collision mesh archive member cannot be read")
                cache[member_name] = stream.read()
            return cache[member_name]

        result = derive_entity_speed_envelopes(
            urdf_bytes=urdf_bytes,
            inventory=inventory,
            mesh_loader=mesh_loader,
        )
    result["provenance"] = {
        "primary_archive_sha256": PRIMARY_ARCHIVE_SHA256,
        "supplement_archive_sha256": SUPPLEMENT_ARCHIVE_SHA256,
        "geometry_inventory_file_sha256": INVENTORY_FILE_SHA256,
        "resolved_urdf_member": URDF_MEMBER,
        "resolved_urdf_sha256": sha256_bytes(urdf_bytes),
        "loaded_collision_mesh_count": len(cache),
    }
    result["artifact_identity_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result
