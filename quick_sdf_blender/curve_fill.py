# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-free terminator sampling and generated-layer compositing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

Vec3 = tuple[float, float, float]
_HANDLE_FIT_DET_EPS = 1.0e-12
_HANDLE_DEGENERATE_EPS2 = 1.0e-12


def cubic_bezier_3d(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
    t: float,
) -> tuple[float, float, float]:
    omt = 1.0 - t
    omt2 = omt * omt
    t2 = t * t
    s0, s1, s2, s3 = omt2 * omt, 3.0 * omt2 * t, 3.0 * omt * t2, t2 * t
    return (
        s0 * a[0] + s1 * b[0] + s2 * c[0] + s3 * d[0],
        s0 * a[1] + s1 * b[1] + s2 * c[1] + s3 * d[1],
        s0 * a[2] + s1 * b[2] + s2 * c[2] + s3 * d[2],
    )


def sample_closed_bezier_3d(
    knots: Sequence[tuple[Sequence[float], Sequence[float], Sequence[float]]],
    samples_per_segment: int = 16,
) -> list[tuple[float, float, float]]:
    """Sample a cyclic Bézier polyline. Each knot is ``(co, handle_left, handle_right)``."""

    if len(knots) < 2:
        if not knots:
            return []
        co = knots[0][0]
        return [(float(co[0]), float(co[1]), float(co[2]))]
    samples_per_segment = max(2, int(samples_per_segment))
    result: list[tuple[float, float, float]] = []
    for index, current in enumerate(knots):
        following = knots[(index + 1) % len(knots)]
        for sample in range(samples_per_segment):
            if index and sample == 0:
                continue
            t = sample / float(samples_per_segment)
            result.append(
                cubic_bezier_3d(
                    current[0], current[2], following[1], following[0], t
                )
            )
    return result


def _as_vec3(point: Sequence[float]) -> Vec3:
    return (float(point[0]), float(point[1]), float(point[2]))


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def _lerp3(a: Sequence[float], b: Sequence[float], t: float) -> Vec3:
    return (
        float(a[0]) + (float(b[0]) - float(a[0])) * t,
        float(a[1]) + (float(b[1]) - float(a[1])) * t,
        float(a[2]) + (float(b[2]) - float(a[2])) * t,
    )


def sample_segment_for_handle_fit(
    p0: Sequence[float],
    c1: Sequence[float],
    c2: Sequence[float],
    p1: Sequence[float],
    samples_per_segment: int = 16,
) -> list[tuple[float, Vec3]]:
    """Interior samples of a cubic, or the chord if handles collapse onto the knots."""

    count = max(2, int(samples_per_segment))
    degenerate = (
        _distance(p0, c1) ** 2 <= _HANDLE_DEGENERATE_EPS2
        and _distance(p1, c2) ** 2 <= _HANDLE_DEGENERATE_EPS2
    )
    samples: list[tuple[float, Vec3]] = []
    for index in range(1, count):
        t = index / float(count)
        point = _lerp3(p0, p1, t) if degenerate else cubic_bezier_3d(p0, c1, c2, p1, t)
        samples.append((t, point))
    return samples


def _clamp_handle(origin: Sequence[float], control: Sequence[float], max_length: float) -> Vec3:
    offset = (
        float(control[0]) - float(origin[0]),
        float(control[1]) - float(origin[1]),
        float(control[2]) - float(origin[2]),
    )
    length = math.sqrt(sum(component * component for component in offset))
    if length <= max_length or length <= 1.0e-12:
        return _as_vec3(control)
    scale = max_length / length
    return (
        float(origin[0]) + offset[0] * scale,
        float(origin[1]) + offset[1] * scale,
        float(origin[2]) + offset[2] * scale,
    )


def fit_cubic_handles(
    p0: Sequence[float],
    p1: Sequence[float],
    samples: Sequence[Sequence[float]],
    times: Sequence[float],
    *,
    max_handle_ratio: float = 0.6,
) -> tuple[Vec3, Vec3] | None:
    """Least-squares interior controls for a cubic with fixed endpoints.

    ``samples[i]`` is the on-surface point at ``times[i]`` in (0, 1). Handle
    length is capped at ``max_handle_ratio`` times the projected polyline
    length so a seam jump cannot fire a handle across the mesh.
    """

    start, end = _as_vec3(p0), _as_vec3(p1)
    s11 = s12 = s22 = 0.0
    rhs1 = [0.0, 0.0, 0.0]
    rhs2 = [0.0, 0.0, 0.0]
    used = 0
    polyline = 0.0
    previous = start
    for sample, time in zip(samples, times):
        t = float(time)
        point = _as_vec3(sample)
        polyline += _distance(previous, point)
        previous = point
        if t <= 1.0e-8 or t >= 1.0 - 1.0e-8:
            continue
        u = 1.0 - t
        b0 = u * u * u
        b1 = 3.0 * u * u * t
        b2 = 3.0 * u * t * t
        b3 = t * t * t
        s11 += b1 * b1
        s12 += b1 * b2
        s22 += b2 * b2
        for axis in range(3):
            remainder = point[axis] - b0 * start[axis] - b3 * end[axis]
            rhs1[axis] += b1 * remainder
            rhs2[axis] += b2 * remainder
        used += 1
    polyline += _distance(previous, end)
    det = s11 * s22 - s12 * s12
    if used < 4 or abs(det) <= _HANDLE_FIT_DET_EPS or polyline <= 1.0e-8:
        return None
    inv = 1.0 / det
    c1 = tuple((s22 * rhs1[axis] - s12 * rhs2[axis]) * inv for axis in range(3))
    c2 = tuple((-s12 * rhs1[axis] + s11 * rhs2[axis]) * inv for axis in range(3))
    if any(not math.isfinite(value) for value in (*c1, *c2)):
        return None
    max_length = float(max_handle_ratio) * polyline
    return _clamp_handle(start, c1, max_length), _clamp_handle(end, c2, max_length)


def compose_generated_fill(
    current: Any,
    coverage: Any,
    region: Any,
    value: int,
) -> Any:
    """Fill ``region`` with Light/Shadow and copy Coverage pixels through."""

    import numpy as np

    generated = np.array(current, copy=True, dtype=np.uint8)
    mask = np.asarray(region, dtype=np.bool_)
    if mask.shape != generated.shape:
        mask = mask.reshape(generated.shape)
    protected = np.asarray(coverage, dtype=np.bool_)
    if protected.shape != generated.shape:
        protected = protected.reshape(generated.shape)
    generated[mask] = np.uint8(255 if int(value) else 0)
    generated[protected] = np.asarray(current, dtype=np.uint8).reshape(generated.shape)[
        protected
    ]
    return generated
