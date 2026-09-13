"""Fail-closed validation of the Ubuntu per-pair FCL probe output."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Tuple


EXPECTED_HEADER = (
    "scenario",
    "scope",
    "entity_first",
    "entity_second",
    "signed_distance_m",
    "nearest_first_x_m",
    "nearest_first_y_m",
    "nearest_first_z_m",
    "nearest_second_x_m",
    "nearest_second_y_m",
    "nearest_second_z_m",
    "first_body_type",
    "second_body_type",
)


@dataclass(frozen=True)
class ProbeDistance:
    scenario: str
    scope: str
    entity_pair: Tuple[str, str]
    signed_distance_m: float
    nearest_first_m: Tuple[float, float, float]
    nearest_second_m: Tuple[float, float, float]
    body_types: Tuple[str, str]


def _finite(row: Mapping[str, str], names: Iterable[str]) -> Tuple[float, ...]:
    try:
        result = tuple(float(row[name]) for name in names)
    except (KeyError, ValueError) as exc:
        raise ValueError("probe row contains an invalid number") from exc
    if any(not math.isfinite(item) for item in result):
        raise ValueError("probe row contains a non-finite number")
    return result


def read_probe_distances(path: Path) -> Tuple[ProbeDistance, ...]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != EXPECTED_HEADER:
            raise ValueError("probe output header does not match the frozen schema")
        output = []
        seen = set()
        for row in reader:
            first = row["entity_first"]
            second = row["entity_second"]
            if not row["scenario"] or row["scope"] not in ("self", "world") or not first or first >= second:
                raise ValueError("probe output contains a non-canonical identity")
            key = (row["scenario"], row["scope"], first, second)
            if key in seen:
                raise ValueError("probe output contains a duplicate pair")
            seen.add(key)
            distance = _finite(row, ("signed_distance_m",))[0]
            nearest_first = _finite(row, ("nearest_first_x_m", "nearest_first_y_m", "nearest_first_z_m"))
            nearest_second = _finite(row, ("nearest_second_x_m", "nearest_second_y_m", "nearest_second_z_m"))
            types = (row["first_body_type"], row["second_body_type"])
            if any(item not in ("robot-link", "attached-object", "world") for item in types):
                raise ValueError("probe output contains an unknown body type")
            output.append(
                ProbeDistance(row["scenario"], row["scope"], (first, second), distance, nearest_first, nearest_second, types)
            )
    if not output:
        raise ValueError("probe output is empty")
    return tuple(output)


def validate_exact_pair_coverage(
    observations: Iterable[ProbeDistance],
    expected_by_scenario: Mapping[str, Iterable[Tuple[str, str, str]]],
) -> Dict[str, dict]:
    observations = tuple(observations)
    actual_scenarios = {item.scenario for item in observations}
    if actual_scenarios != set(expected_by_scenario):
        raise ValueError("probe scenarios do not match the declared scenarios")
    result = {}
    for scenario, expected in expected_by_scenario.items():
        expected_set = {
            (item[0], *tuple(sorted(item[1:])))
            for item in expected
        }
        if any(item[0] not in ("self", "world") for item in expected_set):
            raise ValueError("expected pair coverage contains an invalid query scope")
        actual = {
            (item.scope, *item.entity_pair)
            for item in observations
            if item.scenario == scenario
        }
        if actual != expected_set:
            raise ValueError(
                f"probe pair coverage mismatch for {scenario}: "
                f"missing={sorted(expected_set - actual)}, extra={sorted(actual - expected_set)}"
            )
        result[scenario] = {
            "expected_pairs": len(expected_set),
            "observed_pairs": len(actual),
            "minimum_signed_distance_m": min(
                item.signed_distance_m for item in observations if item.scenario == scenario
            ),
        }
    return result


def validate_analytic_sign_cases(observations: Iterable[ProbeDistance], tolerance_m: float = 1e-9) -> dict:
    by_scenario = {}
    for item in observations:
        by_scenario.setdefault(item.scenario, []).append(item)
    if set(by_scenario) != {"separated", "touching", "penetrating"}:
        raise ValueError("analytic probe requires separated/touching/penetrating scenarios")
    if any(len(items) != 1 for items in by_scenario.values()):
        raise ValueError("analytic probe requires exactly one pair per scenario")
    values = {name: items[0].signed_distance_m for name, items in by_scenario.items()}
    if not values["separated"] > tolerance_m:
        raise ValueError("separated analytic case is not positive")
    if abs(values["touching"]) > tolerance_m:
        raise ValueError("touching analytic case is not numerically zero")
    if not values["penetrating"] < -tolerance_m:
        raise ValueError("penetrating analytic case is not negative")
    return {"status": "PASS", "tolerance_m": tolerance_m, "signed_distances_m": values}
