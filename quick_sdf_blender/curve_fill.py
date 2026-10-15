# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-free terminator sampling and generated-layer compositing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


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
