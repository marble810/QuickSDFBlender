# SPDX-License-Identifier: GPL-3.0-or-later
"""Nearest-texel UV edge extension, independent of Blender and native ABI."""

from __future__ import annotations

import operator

import numpy as np

from .core import _edt_rows_squared


def padding_map(occupancy: np.ndarray, pixels: int) -> tuple[np.ndarray, np.ndarray]:
    """Return flat destination/source indices outside UVs within a Euclidean radius.

    Original occupied texels are never destinations. Equal-distance ties are
    deterministic; all guide keys reuse the same map to retain monotonicity.
    Image edges clip the extension rather than wrapping it to the opposite edge.
    """

    radius = operator.index(pixels)
    if radius < 0:
        raise ValueError("UV padding must be non-negative")
    mask = np.asarray(occupancy, dtype=np.bool_)
    if mask.ndim != 2 or not mask.size:
        raise ValueError("UV occupancy must be a non-empty 2D plane")
    empty = np.empty(0, dtype=np.intp)
    if not radius or not np.any(mask) or np.all(mask):
        return empty, empty
    height, width = mask.shape
    unreachable = float((height - 1) ** 2 + (width - 1) ** 2 + 1)
    vertical = np.full(mask.shape, unreachable, dtype=np.float64)
    source_y = np.zeros(mask.shape, dtype=np.int32)
    last = np.full(width, -height, dtype=np.int32)
    for y in range(height):
        last = np.where(mask[y], y, last)
        valid = last >= 0
        vertical[y, valid] = (y - last[valid]).astype(np.float64) ** 2
        source_y[y, valid] = last[valid]
    last.fill(height * 2)
    for y in range(height - 1, -1, -1):
        last = np.where(mask[y], y, last)
        distance = (last - y).astype(np.float64) ** 2
        closer = (last < height) & (distance < vertical[y])
        vertical[y, closer] = distance[closer]
        source_y[y, closer] = last[closer]
    source_x = np.empty(mask.shape, dtype=np.int32)
    squared = _edt_rows_squared(vertical, indices_out=source_x)
    rows, columns = np.nonzero(~mask & (squared <= radius * radius))
    nearest_x = source_x[rows, columns]
    nearest_y = source_y[rows, nearest_x]
    destination = rows * width + columns
    source = nearest_y.astype(np.intp) * width + nearest_x
    destination.setflags(write=False)
    source.setflags(write=False)
    return destination, source


def extend_uv_edges(
    values: np.ndarray,
    mapping: tuple[np.ndarray, np.ndarray],
    *,
    touched: np.ndarray | None = None,
) -> bool:
    """Extend one 2D plane in place, optionally only from changed UV texels."""

    destination, source = mapping
    if touched is not None:
        selected = np.asarray(touched, dtype=np.bool_).flat[source]
        destination, source = destination[selected], source[selected]
    if not destination.size:
        return False
    copied = values.flat[source]
    changed = bool(np.any(values.flat[destination] != copied))
    values.flat[destination] = copied
    return changed


def pad_guide_masks(masks: np.ndarray, occupancy: np.ndarray, pixels: int) -> np.ndarray:
    mapping = padding_map(occupancy, pixels)
    for mask in masks:
        extend_uv_edges(mask, mapping)
    return masks
