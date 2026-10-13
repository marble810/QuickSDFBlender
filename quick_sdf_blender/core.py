# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent reference algorithms for Quick SDF.

The native module may accelerate these operations, but this module deliberately
contains a complete NumPy implementation so generation and validation can be
tested without Blender or a GPU.

Mask convention
---------------
``True``/white is Light and ``False``/black is Shadow.  Signed distance is
positive in Shadow and negative in Light.  On each side of the face, masks are
ordered from 0 degrees towards 90 degrees and Light is required to expand.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

import numpy as np


# lilToon consumes the complete normalized channel range.  A larger value
# means that a pixel becomes Light earlier in the authored 0..90 sweep.
# These names describe the two endpoint outcomes; neither value is a reserved
# sentinel and both may also be produced by a transition exactly at an
# endpoint.
ALWAYS_LIGHT = np.uint16(65535)
ALWAYS_SHADOW = np.uint16(0)
_ANGLE_EPSILON = 1.0e-7


@dataclass(frozen=True)
class MonotonicValidation:
    """Result of validating Light expansion in one 0..90 degree lane."""

    is_valid: bool
    violation_count: int
    violation_pixel_count: int
    violation_map: np.ndarray
    offending_transitions: tuple[tuple[float, float, int], ...]


@dataclass(frozen=True)
class PackedLane:
    """A 0..90 lane stored as one bit per key in each image pixel.

    Bit ``i`` corresponds to ``angles[i]``.  Studio lanes are limited to
    sixteen keys, so the representation stays a compact ``uint16`` plane and
    can be passed to the native core without expanding an ``N x H x W``
    boolean stack.
    """

    angles: np.ndarray
    display_bits: np.ndarray
    base_bits: np.ndarray
    coverage_bits: np.ndarray

    @property
    def count(self) -> int:
        return int(self.angles.size)

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(value) for value in self.display_bits.shape)


@dataclass(frozen=True)
class PackedLaneRepairResult:
    """Compact monotonic-repair result produced from a :class:`PackedLane`."""

    transition_indices: np.ndarray
    changed_count: np.ndarray
    changed_sample_count: int
    changed_pixel_count: int
    protected_changed_sample_count: int
    protected_changed_pixel_count: int


def _as_binary(values: np.ndarray | Sequence[object], *, ndim: int) -> np.ndarray:
    """Convert common normalized/8-bit/16-bit mask arrays to bool."""

    array = np.asarray(values)
    if array.ndim != ndim:
        raise ValueError(f"expected a {ndim}D mask array, got shape {array.shape}")
    if any(size <= 0 for size in array.shape):
        raise ValueError("mask dimensions must be non-zero")
    if array.dtype == np.bool_:
        return np.ascontiguousarray(array)
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError("masks must contain boolean or numeric values")
    if not np.all(np.isfinite(array)):
        raise ValueError("masks must not contain NaN or infinity")

    if np.issubdtype(array.dtype, np.floating):
        threshold = 0.5
    else:
        minimum = int(np.min(array))
        maximum = int(np.max(array))
        if minimum >= 0 and maximum <= 1:
            threshold = 0.5
        elif minimum >= 0 and maximum <= 255:
            threshold = 127.0
        elif minimum >= 0 and maximum <= 65535:
            threshold = 32767.0
        else:
            raise ValueError("integer masks must use 0/1, 8-bit, or 16-bit values")
    return np.ascontiguousarray(array >= threshold)


def _validated_angles(angles: Sequence[float] | np.ndarray, count: int) -> np.ndarray:
    array = np.asarray(angles, dtype=np.float64)
    if array.ndim != 1 or array.size != count:
        raise ValueError(f"expected {count} angles, got shape {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError("angles must be finite")
    if np.any(array < -1e-7) or np.any(array > 90.0 + 1e-7):
        raise ValueError("angles must be in the inclusive range 0..90")
    order = np.argsort(array)
    if array.size > 1 and np.any(np.diff(array[order]) <= 1e-7):
        raise ValueError("angles must be unique")
    if np.count_nonzero(np.isclose(array, 0.0, atol=1e-7, rtol=0.0)) != 1:
        raise ValueError("angles must contain exactly one 0 degree mask")
    return array


def _edt_rows_squared(cost: np.ndarray, *, block_rows: int = 1024) -> np.ndarray:
    """Felzenszwalb-Huttenlocher squared transform along the last axis.

    This vectorized adaptation is based on Pedro Felzenszwalb's 2006
    GPL-2.0-or-later implementation accompanying *Distance Transforms of
    Sampled Functions*: https://cs.brown.edu/people/pfelzens/dt/ . Quick SDF
    modifications are GPL-3.0-or-later; see ``THIRD_PARTY_NOTICES.md``.

    Rows are processed together with NumPy while the lower-envelope dimension
    remains sequential.  Blocking prevents the envelope workspaces from
    becoming excessive for 4K textures.
    """

    row_count, length = cost.shape
    output = np.empty_like(cost, dtype=np.float64)
    for block_start in range(0, row_count, block_rows):
        f = cost[block_start : block_start + block_rows]
        batch = f.shape[0]
        rows = np.arange(batch, dtype=np.intp)
        vertices = np.empty((batch, length), dtype=np.int32)
        bounds = np.empty((batch, length + 1), dtype=np.float64)
        envelope = np.zeros(batch, dtype=np.int32)
        vertices[:, 0] = 0
        bounds[:, 0] = -np.inf
        bounds[:, 1] = np.inf

        for q in range(1, length):
            vertex = vertices[rows, envelope]
            separation = (
                (f[:, q] + float(q * q))
                - (f[rows, vertex] + vertex.astype(np.float64) ** 2)
            ) / (2.0 * (q - vertex))
            pop = separation <= bounds[rows, envelope]
            while np.any(pop):
                envelope[pop] -= 1
                selected = rows[pop]
                vertex = vertices[selected, envelope[pop]]
                separation[pop] = (
                    (f[pop, q] + float(q * q))
                    - (f[selected, vertex] + vertex.astype(np.float64) ** 2)
                ) / (2.0 * (q - vertex))
                pop = separation <= bounds[rows, envelope]
            envelope += 1
            vertices[rows, envelope] = q
            bounds[rows, envelope] = separation
            bounds[rows, envelope + 1] = np.inf

        envelope.fill(0)
        block_output = output[block_start : block_start + batch]
        for q in range(length):
            advance = bounds[rows, envelope + 1] < float(q)
            while np.any(advance):
                envelope[advance] += 1
                advance = bounds[rows, envelope + 1] < float(q)
            vertex = vertices[rows, envelope]
            block_output[:, q] = (q - vertex) ** 2 + f[rows, vertex]
    return output


def exact_edt(features: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return exact Euclidean distance to the nearest ``True`` pixel.

    The result is float64.  If no feature exists, every distance is infinity.
    Pixel centres use unit spacing.
    """

    feature_mask = _as_binary(features, ndim=2)
    if not np.any(feature_mask):
        return np.full(feature_mask.shape, np.inf, dtype=np.float64)

    # The parabolic lower envelope is the expensive pass.  Put the shorter
    # dimension on its axis to reduce Python loop overhead for non-square data.
    transposed = feature_mask.shape[1] > feature_mask.shape[0]
    work = feature_mask.T if transposed else feature_mask
    height, width = work.shape
    unreachable = float((height - 1) ** 2 + (width - 1) ** 2 + 1)

    vertical = np.full((height, width), unreachable, dtype=np.float64)
    last = np.full(width, -height, dtype=np.int64)
    for y in range(height):
        last = np.where(work[y], y, last)
        valid = last >= 0
        vertical[y, valid] = (y - last[valid]) ** 2
    last.fill(height * 2)
    for y in range(height - 1, -1, -1):
        last = np.where(work[y], y, last)
        valid = last < height
        vertical[y, valid] = np.minimum(vertical[y, valid], (last[valid] - y) ** 2)

    squared = _edt_rows_squared(vertical)
    distance = np.sqrt(squared, out=squared)
    return np.ascontiguousarray(distance.T if transposed else distance)


def exact_signed_edt(light_mask: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return exact signed distance, positive in Shadow and negative in Light."""

    light = _as_binary(light_mask, ndim=2)
    if np.all(light):
        return np.full(light.shape, -np.inf, dtype=np.float64)
    if not np.any(light):
        return np.full(light.shape, np.inf, dtype=np.float64)
    to_light = exact_edt(light)
    to_shadow = exact_edt(~light)
    return to_light - to_shadow


def _cancel_requested(cancel_flag: object | None) -> bool:
    if cancel_flag is None:
        return False
    return bool(getattr(cancel_flag, "value", cancel_flag))


def interpolate_binary_masks(
    first: np.ndarray | Sequence[object],
    second: np.ndarray | Sequence[object],
    factor: float,
    cancel_flag: object | None = None,
) -> np.ndarray:
    """Blend two binary masks with the exact signed-distance preview rule.

    ``factor`` is zero at ``first`` and one at ``second``. Constant masks have
    infinite signed distance, which is replaced by the image diagonal plus one
    before blending. This is the same finite convention used by Studio seek
    preview and makes all-Light/all-Shadow interpolation deterministic.
    """

    first_mask = _as_binary(first, ndim=2)
    second_mask = _as_binary(second, ndim=2)
    if first_mask.shape != second_mask.shape:
        raise ValueError("first and second masks must have the same shape")
    if not first_mask.size:
        raise ValueError("mask dimensions must be positive")
    value = float(factor)
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("factor must be a finite value in [0, 1]")
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    if value == 0.0:
        return np.ascontiguousarray(first_mask)
    if value == 1.0:
        return np.ascontiguousarray(second_mask)

    diagonal = float(
        (first_mask.shape[0] ** 2 + first_mask.shape[1] ** 2) ** 0.5 + 1.0
    )
    first_sdf = np.nan_to_num(
        exact_signed_edt(first_mask),
        nan=0.0,
        posinf=diagonal,
        neginf=-diagonal,
    )
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    second_sdf = np.nan_to_num(
        exact_signed_edt(second_mask),
        nan=0.0,
        posinf=diagonal,
        neginf=-diagonal,
    )
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    return np.ascontiguousarray(
        ((1.0 - value) * first_sdf + value * second_sdf) <= 0.0
    )


def _quantize_transition(normalized_angle: np.ndarray) -> np.ndarray:
    """Encode authored transition progress as lilToon's inverted SDF value."""

    transition = np.clip(normalized_angle, 0.0, 1.0)
    values = np.floor((1.0 - transition) * 65535.0 + 0.5)
    return values.astype(np.uint16)


def _threshold_for_side(
    masks: np.ndarray,
    angles: np.ndarray,
    indices: np.ndarray,
    sdf_cache: dict[int, np.ndarray],
) -> np.ndarray:
    height, width = masks.shape[1:]
    # Export-only endpoints, not baked samples.  0 is the directly-front
    # pure-white mask: a texel still in shadow after the last real key becomes
    # light only at that endpoint.  65535 is the directly-behind pure-black
    # mask's complement: a texel already light at the first real key was light
    # before the baked 120-degree sample, so it takes the 180-degree value.
    threshold = np.full((height, width), ALWAYS_SHADOW, dtype=np.uint16)
    threshold[masks[indices[0]]] = ALWAYS_LIGHT

    for first, second in zip(indices[:-1], indices[1:], strict=True):
        transition = ~masks[first] & masks[second]
        if not np.any(transition):
            continue
        if int(first) not in sdf_cache:
            sdf_cache[int(first)] = exact_signed_edt(masks[first])
        if int(second) not in sdf_cache:
            sdf_cache[int(second)] = exact_signed_edt(masks[second])
        distance0 = np.abs(sdf_cache[int(first)][transition])
        distance1 = np.abs(sdf_cache[int(second)][transition])
        denominator = distance0 + distance1
        ratio = np.divide(
            distance0,
            denominator,
            out=np.full_like(distance0, 0.5),
            where=np.isfinite(denominator) & (denominator > 0.0),
        )
        angle0 = abs(float(angles[first])) / 90.0
        angle1 = abs(float(angles[second])) / 90.0
        interpolated = angle0 + (angle1 - angle0) * ratio
        threshold[transition] = _quantize_transition(interpolated)
    return threshold


def _validated_side_stack(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    *,
    name: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Validate one artist-facing 0..90 degree mask lane.

    Quick SDF Paint authors the right and left lanes independently. Keeping the
    lane validation explicit makes it impossible to accidentally swap the R
    and G output channels.
    """

    masks = _as_binary(mask_stack, ndim=3)
    values = _validated_angles(angles, masks.shape[0])
    if np.any(values < -_ANGLE_EPSILON) or np.any(values > 90.0 + _ANGLE_EPSILON):
        raise ValueError(f"{name} angles must be in the range 0..90 degrees")
    order = np.argsort(values, kind="stable")
    masks = masks[order]
    values = values[order]
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0):
        raise ValueError(f"{name} requires a 0 degree mask")
    if not np.isclose(values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0):
        raise ValueError(f"{name} requires a 90 degree mask")
    return masks, values


def validate_side_monotonic(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> MonotonicValidation:
    """Validate that light pixels only expand from front to profile."""

    masks, values = _validated_side_stack(mask_stack, angles, name="side")
    offending: list[tuple[float, float, int]] = []
    violation_map = np.zeros(masks.shape[1:], dtype=np.bool_)
    violation_count = 0
    for first, second, angle0, angle1 in zip(
        masks[:-1], masks[1:], values[:-1], values[1:], strict=True
    ):
        invalid = first & ~second
        count = int(np.count_nonzero(invalid))
        if count:
            violation_count += 1
            violation_map |= invalid
            offending.append((float(angle0), float(angle1), count))
    return MonotonicValidation(
        is_valid=not offending,
        violation_count=violation_count,
        violation_pixel_count=int(np.count_nonzero(violation_map)),
        violation_map=violation_map,
        offending_transitions=tuple(offending),
    )


def pack_lane_bits(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    base_stack: np.ndarray | Sequence[object],
    coverage_stack: np.ndarray | Sequence[object],
) -> PackedLane:
    """Pack three lane stacks into per-pixel ``uint16`` bit fields."""

    masks, values = _validated_side_stack(mask_stack, angles, name="lane")
    base = _as_binary(base_stack, ndim=3)
    coverage = _as_binary(coverage_stack, ndim=3)
    if base.shape != masks.shape or coverage.shape != masks.shape:
        raise ValueError("mask, base, and coverage stacks must have the same shape")
    if masks.shape[0] > 16:
        raise ValueError("packed lanes support at most 16 angle keys")

    # _validated_side_stack sorts the display stack. Apply the same stable
    # order to the other two planes before assigning their bits.
    original_angles = _validated_angles(angles, masks.shape[0])
    order = np.argsort(original_angles, kind="stable")
    base = np.ascontiguousarray(base[order])
    coverage = np.ascontiguousarray(coverage[order])
    shape = masks.shape[1:]
    display_bits = np.zeros(shape, dtype=np.uint16)
    base_bits = np.zeros(shape, dtype=np.uint16)
    coverage_bits = np.zeros(shape, dtype=np.uint16)
    for index in range(masks.shape[0]):
        bit = np.uint16(1 << index)
        display_bits[masks[index]] |= bit
        base_bits[base[index]] |= bit
        coverage_bits[coverage[index]] |= bit
    return PackedLane(
        angles=np.ascontiguousarray(values, dtype=np.float64),
        display_bits=display_bits,
        base_bits=base_bits,
        coverage_bits=coverage_bits,
    )


def repair_packed_lane(lane: PackedLane) -> PackedLaneRepairResult:
    """Pure NumPy reference for compact monotonic repair."""

    if not isinstance(lane, PackedLane):
        raise TypeError("lane must be a PackedLane")
    values = _validated_angles(lane.angles, lane.count)
    if lane.count < 1 or lane.count > 16:
        raise ValueError("packed lanes support between 1 and 16 angle keys")
    if np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("packed lane angles must be strictly increasing")
    planes = []
    for name, plane in (
        ("display", lane.display_bits),
        ("base", lane.base_bits),
        ("coverage", lane.coverage_bits),
    ):
        array = np.asarray(plane)
        if array.ndim != 2 or any(size <= 0 for size in array.shape):
            raise ValueError(f"{name}_bits must be a non-empty 2D plane")
        if array.dtype != np.uint16:
            raise TypeError(f"{name}_bits must use uint16")
        planes.append(np.ascontiguousarray(array))
    display_bits, base_bits, coverage_bits = planes
    if base_bits.shape != display_bits.shape or coverage_bits.shape != display_bits.shape:
        raise ValueError("packed display, base, and coverage planes must share a shape")

    protected_cost = np.zeros(display_bits.shape, dtype=np.int16)
    display_cost = np.zeros(display_bits.shape, dtype=np.int16)
    base_cost = np.zeros(display_bits.shape, dtype=np.int16)
    for index in range(lane.count):
        bit = np.uint16(1 << index)
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        protected = ((coverage_bits & bit) != 0) | (display != base)
        display_cost += (~display).astype(np.int16)
        base_cost += (~base).astype(np.int16)
        protected_cost += (protected & ~display).astype(np.int16)

    best_protected = protected_cost.copy()
    best_display = display_cost.copy()
    best_base = base_cost.copy()
    transitions = np.zeros(display_bits.shape, dtype=np.uint8)
    for transition in range(1, lane.count + 1):
        bit = np.uint16(1 << (transition - 1))
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        protected = ((coverage_bits & bit) != 0) | (display != base)
        display_delta = np.where(display, 1, -1).astype(np.int16)
        protected_cost += display_delta * protected.astype(np.int16)
        display_cost += display_delta
        base_cost += np.where(base, 1, -1).astype(np.int16)
        better = (protected_cost < best_protected) | (
            (protected_cost == best_protected)
            & (
                (display_cost < best_display)
                | ((display_cost == best_display) & (base_cost < best_base))
            )
        )
        best_protected[better] = protected_cost[better]
        best_display[better] = display_cost[better]
        best_base[better] = base_cost[better]
        transitions[better] = np.uint8(transition)

    changed_count = np.zeros(display_bits.shape, dtype=np.uint8)
    protected_changed_count = np.zeros(display_bits.shape, dtype=np.uint8)
    for index in range(lane.count):
        bit = np.uint16(1 << index)
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        repaired = index >= transitions
        changed = repaired != display
        protected = ((coverage_bits & bit) != 0) | (display != base)
        changed_count += changed.astype(np.uint8)
        protected_changed_count += (changed & protected).astype(np.uint8)
    return PackedLaneRepairResult(
        transition_indices=np.ascontiguousarray(transitions),
        changed_count=np.ascontiguousarray(changed_count),
        changed_sample_count=int(np.sum(changed_count, dtype=np.uint64)),
        changed_pixel_count=int(np.count_nonzero(changed_count)),
        protected_changed_sample_count=int(
            np.sum(protected_changed_count, dtype=np.uint64)
        ),
        protected_changed_pixel_count=int(np.count_nonzero(protected_changed_count)),
    )


def _threshold_for_lane(masks: np.ndarray, angles: np.ndarray) -> np.ndarray:
    indices = np.arange(masks.shape[0], dtype=np.intp)
    return _threshold_for_side(masks, angles, indices, {})


def generate_threshold_transitions(
    transition_indices: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    *,
    out: np.ndarray | None = None,
    channel: int = 0,
) -> np.ndarray:
    """Generate one exact threshold channel from compact transition indices.

    ``transition_indices`` stores the number of leading Shadow keys per pixel:
    zero is always Light and ``len(angles)`` is always Shadow.  ``out`` may be
    either a two-dimensional uint16 plane or an HxWxC uint16 image; the latter
    lets the native/export path write directly into an RGBA destination.
    """

    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size < 2 or values.size > 16:
        raise ValueError("angles must contain between 2 and 16 keys")
    if not np.all(np.isfinite(values)) or np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("angles must be finite and strictly increasing")
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0) or not np.isclose(
        values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0
    ):
        raise ValueError("angles must include 0 and 90 degree endpoints")
    transitions = np.asarray(transition_indices)
    if transitions.ndim != 2 or any(size <= 0 for size in transitions.shape):
        raise ValueError("transition indices must be a non-empty 2D plane")
    if not np.issubdtype(transitions.dtype, np.integer):
        raise TypeError("transition indices must contain integers")
    if np.any(transitions < 0) or np.any(transitions > values.size):
        raise ValueError("transition indices are outside the angle sequence")
    masks = np.arange(values.size, dtype=np.int16)[:, None, None] >= transitions[None, ...]
    threshold = _threshold_for_lane(np.ascontiguousarray(masks), values)
    if out is None:
        return threshold
    destination = np.asarray(out)
    if destination.dtype != np.uint16:
        raise TypeError("threshold output must use uint16")
    if not destination.flags.writeable:
        raise ValueError("threshold output must be writeable")
    if destination.ndim == 2:
        if destination.shape != threshold.shape:
            raise ValueError("threshold output shape does not match transition plane")
        destination[...] = threshold
    elif destination.ndim == 3:
        selected = int(channel)
        if destination.shape[:2] != threshold.shape or not 0 <= selected < destination.shape[2]:
            raise ValueError("threshold output channel or shape is invalid")
        destination[..., selected] = threshold
    else:
        raise ValueError("threshold output must be a 2D plane or HxWxC image")
    return destination


__all__ = [
    "ALWAYS_LIGHT",
    "ALWAYS_SHADOW",
    "MonotonicValidation",
    "PackedLane",
    "PackedLaneRepairResult",
    "exact_edt",
    "exact_signed_edt",
    "generate_threshold_transitions",
    "interpolate_binary_masks",
    "pack_lane_bits",
    "repair_packed_lane",
    "validate_side_monotonic",
]
