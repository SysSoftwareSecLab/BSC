"""Immutable cubic/quintic joint trajectories for the Timed Bimanual IR.

This module only defines and validates the frozen trajectory representation.  It
does not plan a path, execute a controller, or claim collision safety.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Iterable, Optional, Tuple


DOF_PER_ARM = 7
TRAJECTORY_SCHEMA_VERSION = "bisafecode.frozen-joint-trajectory.v1"
GRIPPER_TRAJECTORY_SCHEMA_VERSION = "bisafecode.frozen-gripper-trajectory.v1"
POLYNOMIAL_EVALUATION_RULE = "binary64-explicit-horner-v1"


class InvalidTrajectoryError(ValueError):
    """Raised when an input does not belong to the frozen trajectory language."""


class InterpolationOrder(str, Enum):
    CUBIC = "cubic"
    QUINTIC = "quintic"


JointVector = Tuple[float, ...]


def _evaluate_binary64_horner(coefficients: Tuple[float, ...], t: float) -> float:
    """Evaluate one polynomial with a frozen binary64 operation order.

    CPython changed the floating-point algorithm used by ``sum`` in 3.12.
    Therefore a generator expression passed to ``sum`` is not a stable
    trajectory semantics across supported runtimes. Explicit Horner steps
    make every multiplication and addition, and their order, part of the
    verifier definition. This is deterministic arithmetic, not an error bound
    or a continuous-motion certificate.
    """

    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * t + coefficient
    return result


def _validated_vector(name: str, values: Iterable[float], size: int) -> JointVector:
    vector = tuple(float(value) for value in values)
    if len(vector) != size:
        raise InvalidTrajectoryError(
            f"{name} must contain {size} values, got {len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise InvalidTrajectoryError(f"{name} contains a non-finite value")
    return vector


@dataclass(frozen=True)
class TrajectoryPoint:
    time_ns: int
    positions: JointVector
    velocities: JointVector
    accelerations: Optional[JointVector] = None

    @classmethod
    def create(
        cls,
        *,
        time_ns: int,
        positions: Iterable[float],
        velocities: Iterable[float],
        accelerations: Optional[Iterable[float]] = None,
        dof: int = DOF_PER_ARM,
    ) -> "TrajectoryPoint":
        if isinstance(time_ns, bool) or not isinstance(time_ns, int):
            raise InvalidTrajectoryError("time_ns must be an integer number of ns")
        if time_ns < 0:
            raise InvalidTrajectoryError("time_ns must be non-negative")
        return cls(
            time_ns=time_ns,
            positions=_validated_vector("positions", positions, dof),
            velocities=_validated_vector("velocities", velocities, dof),
            accelerations=(
                None
                if accelerations is None
                else _validated_vector("accelerations", accelerations, dof)
            ),
        )


@dataclass(frozen=True)
class PolynomialSegment:
    start_ns: int
    end_ns: int
    order: InterpolationOrder
    # Per joint, coefficients c_0...c_n for q(t)=sum(c_k*t**k), t in seconds.
    coefficients: Tuple[Tuple[float, ...], ...]
    source_point_indices: Tuple[int, int]

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns

    @property
    def duration_s(self) -> float:
        return self.duration_ns * 1e-9

    def evaluate(self, local_time_ns: int) -> Tuple[JointVector, JointVector, JointVector]:
        if isinstance(local_time_ns, bool) or not isinstance(local_time_ns, int):
            raise ValueError("local_time_ns must be an integer")
        if not 0 <= local_time_ns <= self.duration_ns:
            raise ValueError("local_time_ns lies outside the segment")
        t = local_time_ns * 1e-9
        positions = []
        velocities = []
        accelerations = []
        for coeffs in self.coefficients:
            velocity_coeffs = tuple(
                degree * coefficient
                for degree, coefficient in enumerate(coeffs[1:], start=1)
            )
            acceleration_coeffs = tuple(
                degree * (degree - 1) * coefficient
                for degree, coefficient in enumerate(coeffs[2:], start=2)
            )
            positions.append(_evaluate_binary64_horner(coeffs, t))
            velocities.append(_evaluate_binary64_horner(velocity_coeffs, t))
            accelerations.append(_evaluate_binary64_horner(acceleration_coeffs, t))
        return tuple(positions), tuple(velocities), tuple(accelerations)

    def derivative_ranges(
        self,
        derivative_order: int,
        start_local_ns: int = 0,
        end_local_ns: Optional[int] = None,
    ) -> Tuple[Tuple[float, float], ...]:
        """Return conservative per-joint derivative ranges on a closed interval.

        The polynomial is affinely transformed to x in [0, 1], then enclosed by
        the convex hull of its Bernstein coefficients.  Fraction arithmetic
        makes the enclosure exact with respect to the stored IEEE-754
        coefficients; only the final float conversion is outward-rounded.
        """

        if derivative_order < 0:
            raise ValueError("derivative_order must be non-negative")
        if end_local_ns is None:
            end_local_ns = self.duration_ns
        for name, value in (
            ("start_local_ns", start_local_ns),
            ("end_local_ns", end_local_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if not 0 <= start_local_ns <= end_local_ns <= self.duration_ns:
            raise ValueError("derivative range interval lies outside the segment")

        start_s = Fraction(start_local_ns, 1_000_000_000)
        width_s = Fraction(end_local_ns - start_local_ns, 1_000_000_000)
        ranges = []
        for coeffs in self.coefficients:
            monomial = _differentiate_exact(coeffs, derivative_order)
            transformed = _affine_transform_monomial(monomial, start_s, width_s)
            bernstein = _monomial_to_bernstein(transformed)
            lower = min(bernstein)
            upper = max(bernstein)
            ranges.append((_float_down(lower), _float_up(upper)))
        return tuple(ranges)

    def absolute_derivative_bounds(
        self,
        derivative_order: int,
        start_local_ns: int = 0,
        end_local_ns: Optional[int] = None,
    ) -> JointVector:
        return tuple(
            max(abs(lower), abs(upper))
            for lower, upper in self.derivative_ranges(
                derivative_order, start_local_ns, end_local_ns
            )
        )


@dataclass(frozen=True)
class FrozenJointTrajectory:
    arm: str
    joint_names: Tuple[str, ...]
    points: Tuple[TrajectoryPoint, ...]
    model_hash: str
    units: str = "rad,rad/s,rad/s^2,ns"
    schema_version: str = TRAJECTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.arm not in {"left", "right"}:
            raise InvalidTrajectoryError("arm must be 'left' or 'right'")
        if len(self.joint_names) != DOF_PER_ARM:
            raise InvalidTrajectoryError(
                f"joint_names must contain {DOF_PER_ARM} names"
            )
        if len(set(self.joint_names)) != len(self.joint_names):
            raise InvalidTrajectoryError("joint_names must be unique")
        if any(not name for name in self.joint_names):
            raise InvalidTrajectoryError("joint_names must be non-empty")
        if len(self.points) < 2:
            raise InvalidTrajectoryError("trajectory requires at least two points")
        if self.points[0].time_ns != 0:
            raise InvalidTrajectoryError("the first point must have time_ns=0")
        if not self.model_hash:
            raise InvalidTrajectoryError("model_hash is required")

        previous_time = -1
        acceleration_presence = set()
        for point in self.points:
            if isinstance(point.time_ns, bool) or not isinstance(point.time_ns, int):
                raise InvalidTrajectoryError(
                    "each point time_ns must be an integer number of ns"
                )
            if point.time_ns < 0:
                raise InvalidTrajectoryError("point time_ns must be non-negative")
            if len(point.positions) != DOF_PER_ARM:
                raise InvalidTrajectoryError("point positions do not match 7-DOF arm")
            if len(point.velocities) != DOF_PER_ARM:
                raise InvalidTrajectoryError("point velocities do not match 7-DOF arm")
            if point.accelerations is not None and len(point.accelerations) != DOF_PER_ARM:
                raise InvalidTrajectoryError(
                    "point accelerations do not match 7-DOF arm"
                )
            vectors = (point.positions, point.velocities)
            if point.accelerations is not None:
                vectors = vectors + (point.accelerations,)
            if not all(math.isfinite(value) for vector in vectors for value in vector):
                raise InvalidTrajectoryError("trajectory contains a non-finite value")
            if point.time_ns <= previous_time:
                raise InvalidTrajectoryError("point times must be strictly increasing")
            previous_time = point.time_ns
            acceleration_presence.add(point.accelerations is not None)
        if len(acceleration_presence) != 1:
            raise InvalidTrajectoryError(
                "accelerations must be present at every point or at no point"
            )

    @property
    def interpolation_order(self) -> InterpolationOrder:
        if self.points[0].accelerations is None:
            return InterpolationOrder.CUBIC
        return InterpolationOrder.QUINTIC

    @property
    def duration_ns(self) -> int:
        return self.points[-1].time_ns

    @property
    def content_hash(self) -> str:
        def vector_hex(vector: Optional[JointVector]) -> Optional[Tuple[str, ...]]:
            if vector is None:
                return None
            return tuple(value.hex() for value in vector)

        payload = {
            "schema_version": self.schema_version,
            "arm": self.arm,
            "joint_names": self.joint_names,
            "model_hash": self.model_hash,
            "units": self.units,
            "points": [
                {
                    "time_ns": point.time_ns,
                    "positions": vector_hex(point.positions),
                    "velocities": vector_hex(point.velocities),
                    "accelerations": vector_hex(point.accelerations),
                }
                for point in self.points
            ],
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    def segments(self) -> Tuple[PolynomialSegment, ...]:
        return tuple(
            _segment_from_points(index, self.points[index], self.points[index + 1])
            for index in range(len(self.points) - 1)
        )

    def absolute_derivative_bounds(
        self,
        derivative_order: int,
        start_ns: int = 0,
        end_ns: Optional[int] = None,
    ) -> JointVector:
        return _absolute_trajectory_derivative_bounds(
            self.segments(),
            derivative_order,
            start_ns,
            self.duration_ns if end_ns is None else end_ns,
        )


@dataclass(frozen=True)
class FrozenGripperTrajectory:
    arm: str
    joint_name: str
    points: Tuple[TrajectoryPoint, ...]
    model_hash: str
    units: str = "m,m/s,m/s^2,ns"
    schema_version: str = GRIPPER_TRAJECTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.arm not in {"left", "right"}:
            raise InvalidTrajectoryError("gripper arm must be 'left' or 'right'")
        if not self.joint_name:
            raise InvalidTrajectoryError("gripper joint_name is required")
        if not self.model_hash:
            raise InvalidTrajectoryError("gripper model_hash is required")
        if len(self.points) < 2 or self.points[0].time_ns != 0:
            raise InvalidTrajectoryError(
                "gripper trajectory requires at least two points starting at zero"
            )
        previous_time = -1
        acceleration_presence = set()
        for point in self.points:
            if len(point.positions) != 1 or len(point.velocities) != 1:
                raise InvalidTrajectoryError(
                    "gripper trajectory points must have one position and velocity"
                )
            if point.accelerations is not None and len(point.accelerations) != 1:
                raise InvalidTrajectoryError(
                    "gripper trajectory acceleration must have one value"
                )
            vectors = (point.positions, point.velocities)
            if point.accelerations is not None:
                vectors = vectors + (point.accelerations,)
            if not all(math.isfinite(value) for vector in vectors for value in vector):
                raise InvalidTrajectoryError(
                    "gripper trajectory contains a non-finite value"
                )
            if point.time_ns <= previous_time:
                raise InvalidTrajectoryError(
                    "gripper point times must be strictly increasing"
                )
            previous_time = point.time_ns
            acceleration_presence.add(point.accelerations is not None)
        if len(acceleration_presence) != 1:
            raise InvalidTrajectoryError(
                "gripper accelerations must be present at all points or none"
            )

    @property
    def interpolation_order(self) -> InterpolationOrder:
        if self.points[0].accelerations is None:
            return InterpolationOrder.CUBIC
        return InterpolationOrder.QUINTIC

    @property
    def duration_ns(self) -> int:
        return self.points[-1].time_ns

    @property
    def content_hash(self) -> str:
        def vector_hex(vector: Optional[JointVector]) -> Optional[Tuple[str, ...]]:
            if vector is None:
                return None
            return tuple(value.hex() for value in vector)

        payload = {
            "schema_version": self.schema_version,
            "arm": self.arm,
            "joint_name": self.joint_name,
            "model_hash": self.model_hash,
            "units": self.units,
            "points": [
                {
                    "time_ns": point.time_ns,
                    "positions": vector_hex(point.positions),
                    "velocities": vector_hex(point.velocities),
                    "accelerations": vector_hex(point.accelerations),
                }
                for point in self.points
            ],
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    def segments(self) -> Tuple[PolynomialSegment, ...]:
        return tuple(
            _segment_from_points(index, self.points[index], self.points[index + 1])
            for index in range(len(self.points) - 1)
        )

    def absolute_derivative_bounds(
        self,
        derivative_order: int,
        start_ns: int = 0,
        end_ns: Optional[int] = None,
    ) -> JointVector:
        return _absolute_trajectory_derivative_bounds(
            self.segments(),
            derivative_order,
            start_ns,
            self.duration_ns if end_ns is None else end_ns,
        )


def _segment_from_points(
    index: int, start: TrajectoryPoint, end: TrajectoryPoint
) -> PolynomialSegment:
    duration_s = (end.time_ns - start.time_ns) * 1e-9
    if duration_s <= 0.0:
        raise InvalidTrajectoryError("segment duration must be positive")

    coefficients = []
    if start.accelerations is None and end.accelerations is None:
        order = InterpolationOrder.CUBIC
        for q0, q1, v0, v1 in zip(
            start.positions, end.positions, start.velocities, end.velocities
        ):
            delta = q1 - q0
            coefficients.append(
                (
                    q0,
                    v0,
                    3.0 * delta / duration_s**2 - (2.0 * v0 + v1) / duration_s,
                    -2.0 * delta / duration_s**3 + (v0 + v1) / duration_s**2,
                )
            )
    elif start.accelerations is not None and end.accelerations is not None:
        order = InterpolationOrder.QUINTIC
        for q0, q1, v0, v1, acc0, acc1 in zip(
            start.positions,
            end.positions,
            start.velocities,
            end.velocities,
            start.accelerations,
            end.accelerations,
        ):
            delta = q1 - q0
            coefficients.append(
                (
                    q0,
                    v0,
                    acc0 / 2.0,
                    (
                        20.0 * delta
                        - (8.0 * v1 + 12.0 * v0) * duration_s
                        - (3.0 * acc0 - acc1) * duration_s**2
                    )
                    / (2.0 * duration_s**3),
                    (
                        -30.0 * delta
                        + (14.0 * v1 + 16.0 * v0) * duration_s
                        + (3.0 * acc0 - 2.0 * acc1) * duration_s**2
                    )
                    / (2.0 * duration_s**4),
                    (
                        12.0 * delta
                        - (6.0 * v1 + 6.0 * v0) * duration_s
                        - (acc0 - acc1) * duration_s**2
                    )
                    / (2.0 * duration_s**5),
                )
            )
    else:
        raise InvalidTrajectoryError(
            "segment endpoints disagree about acceleration availability"
        )

    return PolynomialSegment(
        start_ns=start.time_ns,
        end_ns=end.time_ns,
        order=order,
        coefficients=tuple(coefficients),
        source_point_indices=(index, index + 1),
    )


def _absolute_trajectory_derivative_bounds(
    segments: Tuple[PolynomialSegment, ...],
    derivative_order: int,
    start_ns: int,
    end_ns: int,
) -> JointVector:
    """Conservatively combine per-segment derivative bounds over an interval."""

    if not segments:
        raise InvalidTrajectoryError("trajectory has no polynomial segments")
    for name, value in (("start_ns", start_ns), ("end_ns", end_ns)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    trajectory_start = segments[0].start_ns
    trajectory_end = segments[-1].end_ns
    if not trajectory_start <= start_ns < end_ns <= trajectory_end:
        raise ValueError("derivative-bound interval lies outside trajectory")

    combined = None
    for segment in segments:
        overlap_start = max(start_ns, segment.start_ns)
        overlap_end = min(end_ns, segment.end_ns)
        if overlap_start >= overlap_end:
            continue
        bounds = segment.absolute_derivative_bounds(
            derivative_order,
            overlap_start - segment.start_ns,
            overlap_end - segment.start_ns,
        )
        if combined is None:
            combined = list(bounds)
        else:
            combined = [
                max(previous, current)
                for previous, current in zip(combined, bounds)
            ]
    if combined is None:
        raise ValueError("derivative-bound interval has no segment overlap")
    return tuple(combined)


def _differentiate_exact(
    coefficients: Tuple[float, ...], derivative_order: int
) -> Tuple[Fraction, ...]:
    exact = tuple(Fraction.from_float(value) for value in coefficients)
    if derivative_order >= len(exact):
        return (Fraction(0),)
    derived = []
    for degree in range(derivative_order, len(exact)):
        factor = math.prod(range(degree - derivative_order + 1, degree + 1))
        derived.append(exact[degree] * factor)
    return tuple(derived)


def _affine_transform_monomial(
    coefficients: Tuple[Fraction, ...], start: Fraction, width: Fraction
) -> Tuple[Fraction, ...]:
    """Coefficients of p(start + width*x), in increasing powers of x."""

    degree = len(coefficients) - 1
    transformed = []
    for x_degree in range(degree + 1):
        value = Fraction(0)
        for original_degree in range(x_degree, degree + 1):
            value += (
                coefficients[original_degree]
                * math.comb(original_degree, x_degree)
                * start ** (original_degree - x_degree)
                * width**x_degree
            )
        transformed.append(value)
    return tuple(transformed)


def _monomial_to_bernstein(
    coefficients: Tuple[Fraction, ...]
) -> Tuple[Fraction, ...]:
    """Convert monomial coefficients on [0,1] to same-degree Bernstein form."""

    degree = len(coefficients) - 1
    bernstein = []
    for bernstein_index in range(degree + 1):
        value = Fraction(0)
        for monomial_index in range(bernstein_index + 1):
            value += (
                Fraction(
                    math.comb(bernstein_index, monomial_index),
                    math.comb(degree, monomial_index),
                )
                * coefficients[monomial_index]
            )
        bernstein.append(value)
    return tuple(bernstein)


def _float_down(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) > value:
        return math.nextafter(converted, -math.inf)
    return converted


def _float_up(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) < value:
        return math.nextafter(converted, math.inf)
    return converted
