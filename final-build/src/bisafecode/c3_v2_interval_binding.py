"""Numerical bindings for the C3-v2 model-relative interval checker.

The frozen verifier evaluates stored binary64 polynomial coefficients with a
fixed Horner operation order.  This module bounds the difference between that
implementation and the real polynomial induced by the same stored
coefficients at exact integer-nanosecond time.  It deliberately does not use
the candidate-selection evaluator: that comparison belongs to oracle audit,
not to the verifier certificate.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Tuple

from .geometry_contract import GeometryEntitySpec
from .trajectory import PolynomialSegment


_UNIT_ROUNDOFF = Fraction(1, 2**53)
_MINIMUM_HALF_SUBNORMAL = Fraction(1, 2**1075)
_EXACT_NS_TO_S = Fraction(1, 1_000_000_000)
_BINARY64_NS_TO_S = Fraction.from_float(1e-9)


def _float_up(value: Fraction) -> float:
    converted = float(value)
    if math.isinf(converted):
        return converted
    if Fraction.from_float(converted) < value:
        return math.nextafter(converted, math.inf)
    return converted


def verifier_horner_position_error_bounds(
    segment: PolynomialSegment,
) -> Tuple[float, ...]:
    """Return outward joint-position error bounds for one frozen segment.

    The bound contains two terms:

    1. conversion of exact integer-nanosecond time to the binary64 seconds
       value consumed by the verifier; and
    2. ten explicitly ordered binary64 operations for a quintic Horner
       evaluation (or the same conservative ten-operation budget for a lower
       order polynomial).

    Stored coefficients are the verifier's polynomial definition, so their
    earlier construction is not counted again.  A small absolute underflow
    term keeps the bound valid without assuming every intermediate is normal.
    """

    operation_count = 2 * max(len(coefficients) - 1 for coefficients in segment.coefficients)
    if operation_count > 10:
        raise ValueError("C3-v2 error binding supports at most quintic segments")
    gamma = (10 * _UNIT_ROUNDOFF) / (1 - 10 * _UNIT_ROUNDOFF)
    duration_ns = segment.duration_ns
    t_max = Fraction(duration_ns, 1_000_000_000)
    scale_representation_error = duration_ns * abs(
        _BINARY64_NS_TO_S - _EXACT_NS_TO_S
    )
    multiplication_roundoff = (
        _UNIT_ROUNDOFF
        / (1 - _UNIT_ROUNDOFF)
        * duration_ns
        * abs(_BINARY64_NS_TO_S)
    )
    time_error_s = scale_representation_error + multiplication_roundoff

    derivative_ranges = segment.derivative_ranges(1, 0, duration_ns)
    results = []
    for coefficients, (lower, upper) in zip(
        segment.coefficients, derivative_ranges
    ):
        polynomial_magnitude = sum(
            abs(Fraction.from_float(coefficient)) * t_max**degree
            for degree, coefficient in enumerate(coefficients)
        )
        arithmetic_error = gamma * polynomial_magnitude
        underflow_error = 10 * _MINIMUM_HALF_SUBNORMAL
        derivative_magnitude = Fraction.from_float(max(abs(lower), abs(upper)))
        time_conversion_error = derivative_magnitude * time_error_s
        results.append(
            _float_up(arithmetic_error + underflow_error + time_conversion_error)
        )
    return tuple(results)


def entity_interpolation_error_bound(
    entity: GeometryEntitySpec,
    *,
    left_joint_error_rad: Tuple[float, ...],
    right_joint_error_rad: Tuple[float, ...],
) -> float:
    """Propagate verifier joint error through one frozen entity envelope."""

    if len(left_joint_error_rad) != 7 or len(right_joint_error_rad) != 7:
        raise ValueError("OpenArm interpolation errors must contain seven joints per arm")
    exact = Fraction(0)
    for coefficient, error in zip(
        entity.left_joint_m_per_rad, left_joint_error_rad
    ):
        exact += Fraction.from_float(coefficient) * Fraction.from_float(error)
    for coefficient, error in zip(
        entity.right_joint_m_per_rad, right_joint_error_rad
    ):
        exact += Fraction.from_float(coefficient) * Fraction.from_float(error)
    return _float_up(exact)


def pair_interpolation_error_bound(
    first: GeometryEntitySpec,
    second: GeometryEntitySpec,
    *,
    left_joint_error_rad: Tuple[float, ...],
    right_joint_error_rad: Tuple[float, ...],
) -> float:
    """Return an outward Cartesian distance error for one entity pair."""

    first_error = entity_interpolation_error_bound(
        first,
        left_joint_error_rad=left_joint_error_rad,
        right_joint_error_rad=right_joint_error_rad,
    )
    second_error = entity_interpolation_error_bound(
        second,
        left_joint_error_rad=left_joint_error_rad,
        right_joint_error_rad=right_joint_error_rad,
    )
    return _float_up(
        Fraction.from_float(first_error) + Fraction.from_float(second_error)
    )
