# SPDX-License-Identifier: GPL-3.0-or-later
"""ctypes bridge to the optional compiled exact-EDT core."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any

import numpy as np

_DLL: Any = None
# Sentinel for "the native core is absent or failed to load on this platform".
_UNAVAILABLE = object()
NATIVE_ABI = 9

# The bundled exact-EDT core is optional: the pure Python implementations in
# core.py / bake.py are used whenever the platform library is missing.
_LIBRARY_NAMES = {
    "win32": "quicksdf_core.dll",       # Windows (MSVC)
    "darwin": "libquicksdf_core.dylib",  # macOS (clang)
}
_DEFAULT_LIBRARY_NAME = "libquicksdf_core.so"  # Linux (gcc/clang)


class NativeCoreError(RuntimeError):
    pass


def library_path():
    """Return the bundled native core for this platform, or None."""

    name = _LIBRARY_NAMES.get(sys.platform, _DEFAULT_LIBRARY_NAME)
    candidate = Path(__file__).with_name("bin") / name
    return candidate if candidate.exists() else None


def _load() -> Any:
    """Return the cached ctypes core, or None when it is unavailable."""

    global _DLL
    if _DLL is _UNAVAILABLE:
        return None
    if _DLL is not None:
        return _DLL
    path = library_path()
    if path is None:
        _DLL = _UNAVAILABLE
        return None
    try:
        dll = ctypes.CDLL(str(path))
        u8p = ctypes.POINTER(ctypes.c_uint8)
        f32p = ctypes.POINTER(ctypes.c_float)
        f64p = ctypes.POINTER(ctypes.c_double)
        u16p = ctypes.POINTER(ctypes.c_uint16)
        i32p = ctypes.POINTER(ctypes.c_int)
        dll.qsdf_version.argtypes = []
        dll.qsdf_version.restype = ctypes.c_int
        if int(dll.qsdf_version()) != NATIVE_ABI:
            _DLL = _UNAVAILABLE
            return None
        dll.qsdf_bake_face_shadow_guide.argtypes = [
            f32p, f32p, ctypes.c_int,
            f64p, ctypes.c_int, f64p, f64p,
            ctypes.c_int, ctypes.c_double,
            ctypes.c_int, ctypes.c_int, u8p, u8p, i32p,
        ]
        dll.qsdf_bake_face_shadow_guide.restype = ctypes.c_int
        dll.qsdf_repair_lane_bits.argtypes = [
            u16p, u16p, u16p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            u8p, u8p,
            i32p, i32p, i32p, i32p, i32p,
        ]
        dll.qsdf_repair_lane_bits.restype = ctypes.c_int
        dll.qsdf_generate_threshold_transitions.argtypes = [
            u8p, f64p, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
            u16p, ctypes.c_int, ctypes.c_int, i32p, i32p,
        ]
        dll.qsdf_generate_threshold_transitions.restype = ctypes.c_int
        dll.qsdf_interpolate_binary_masks.argtypes = [
            u8p, u8p, ctypes.c_int, ctypes.c_int, ctypes.c_double, u8p, i32p,
        ]
        dll.qsdf_interpolate_binary_masks.restype = ctypes.c_int
        _DLL = dll
    except OSError:
        _DLL = _UNAVAILABLE
    return None if _DLL is _UNAVAILABLE else _DLL


def available() -> bool:
    return _load() is not None


def version() -> int:
    dll = _load()
    return int(dll.qsdf_version()) if dll is not None else 0


def native_guide_bake_available() -> bool:
    """Return whether the current lilToon guide rasterizer is available."""

    return version() == NATIVE_ABI


def native_packed_lane_available() -> bool:
    """Return whether the current compact repair/generation core is available."""

    return version() == NATIVE_ABI


def native_interpolation_available() -> bool:
    """Return whether the current exact binary-mask interpolator is available."""

    return version() == NATIVE_ABI


def interpolate_binary_masks(first, second, factor, cancel_flag=None):
    """Return an exact-SDF blend, using the current native core when available."""

    from .core import _as_binary
    from .core import interpolate_binary_masks as fallback

    first_mask = _as_binary(first, ndim=2)
    second_mask = _as_binary(second, ndim=2)
    if first_mask.shape != second_mask.shape:
        raise ValueError("first and second masks must have the same shape")
    if not first_mask.size:
        raise ValueError("mask dimensions must be positive")
    value = float(factor)
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("factor must be a finite value in [0, 1]")
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF interpolation was cancelled")
    if not native_interpolation_available():
        try:
            return fallback(first_mask, second_mask, value, cancel_flag=cancel_value)
        except RuntimeError as error:
            if "cancelled" in str(error):
                raise NativeCoreError("Quick SDF interpolation was cancelled") from error
            raise

    first_binary = np.ascontiguousarray(first_mask, dtype=np.uint8)
    second_binary = np.ascontiguousarray(second_mask, dtype=np.uint8)
    height, width = first_binary.shape
    output = np.empty((height, width), dtype=np.uint8)
    dll = _load()
    code = dll.qsdf_interpolate_binary_masks(
        first_binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        second_binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        width,
        height,
        ctypes.c_double(value),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF interpolation was cancelled")
    if code:
        raise NativeCoreError(
            f"Native Quick SDF interpolation failed with status {code}"
        )
    return np.ascontiguousarray(output.astype(np.bool_))


def bake_face_shadow_guide(
    triangle_uvs,
    corner_normals,
    angles,
    forward,
    up,
    side,
    shadow_amount,
    width,
    height=None,
    *,
    enforce_monotonic=True,
    padding_px=0,
    cancel_flag=None,
):
    """Bake the side-explicit lilToon guide in the current core or exact Python."""

    from .bake import (
        bake_face_shadow_guide as fallback,
    )
    from .bake import (
        guide_light_directions,
        shadow_amount_cutoff,
    )

    if not native_guide_bake_available() or not enforce_monotonic:
        return fallback(
            triangle_uvs,
            corner_normals,
            angles,
            forward,
            up,
            side,
            shadow_amount,
            width,
            height,
            enforce_monotonic=enforce_monotonic,
            padding_px=padding_px,
        )
    uvs = np.ascontiguousarray(np.asarray(triangle_uvs), dtype=np.float32)
    normals = np.ascontiguousarray(np.asarray(corner_normals), dtype=np.float32)
    angle_values, _directions = guide_light_directions(angles, forward, up, side)
    angle_values = np.ascontiguousarray(angle_values, dtype=np.float64)
    forward_values = np.ascontiguousarray(np.asarray(forward), dtype=np.float64)
    up_values = np.ascontiguousarray(np.asarray(up), dtype=np.float64)
    cutoff = shadow_amount_cutoff(shadow_amount)
    if uvs.ndim != 3 or uvs.shape[1:] != (3, 2):
        raise ValueError("triangle_uvs must have shape (N, 3, 2)")
    if normals.shape != (uvs.shape[0], 3, 3):
        raise ValueError("corner_normals must have shape (N, 3, 3)")
    if not np.all(np.isfinite(uvs)) or not np.all(np.isfinite(normals)):
        raise ValueError("triangle data must be finite")
    lengths = np.linalg.norm(normals, axis=2)
    if np.any(lengths <= 1.0e-12):
        raise ValueError("corner normals must have non-zero length")
    normals = np.ascontiguousarray(normals / lengths[..., None], dtype=np.float32)
    image_width = int(width)
    image_height = image_width if height is None else int(height)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    masks = np.empty((angle_values.size, image_height, image_width), dtype=np.uint8)
    occupancy = np.empty((image_height, image_width), dtype=np.uint8)
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    side_sign = 1 if str(side).upper() == "RIGHT" else -1
    dll = _load()
    code = dll.qsdf_bake_face_shadow_guide(
        uvs.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        normals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        int(uvs.shape[0]),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(angle_values.size),
        forward_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        up_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        side_sign,
        ctypes.c_double(cutoff),
        image_width,
        image_height,
        masks.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        occupancy.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF guide bake was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF guide bake failed with status {code}")
    from .uv_padding import pad_guide_masks

    masks = np.ascontiguousarray(masks, dtype=np.bool_)
    occupancy = np.ascontiguousarray(occupancy, dtype=np.bool_)
    pad_guide_masks(masks, occupancy, padding_px)
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF guide bake was cancelled")
    return masks, occupancy


def _cancel_value(cancel_flag):
    if cancel_flag is None:
        return ctypes.c_int(0)
    if isinstance(cancel_flag, ctypes.c_int):
        return cancel_flag
    return ctypes.c_int(int(bool(cancel_flag)))


def repair_packed_lane(lane, *, cancel_flag=None):
    """Repair a compact :class:`core.PackedLane` through the current core."""

    from .core import PackedLane, PackedLaneRepairResult
    from .core import repair_packed_lane as fallback

    if not isinstance(lane, PackedLane):
        raise TypeError("lane must be a PackedLane")
    # The reference implementation performs the complete public validation.
    # Keep that behavior on machines without the bundled Windows core.
    if not native_packed_lane_available():
        if _cancel_value(cancel_flag).value:
            raise NativeCoreError("Quick SDF packed repair was cancelled")
        return fallback(lane)
    count = int(np.asarray(lane.angles).size)
    if count < 1 or count > 16:
        raise ValueError("packed lanes support between 1 and 16 angle keys")
    planes = []
    for name, value in (
        ("display_bits", lane.display_bits),
        ("base_bits", lane.base_bits),
        ("coverage_bits", lane.coverage_bits),
    ):
        array = np.asarray(value)
        if array.ndim != 2 or any(size <= 0 for size in array.shape):
            raise ValueError(f"{name} must be a non-empty 2D plane")
        if array.dtype != np.uint16:
            raise TypeError(f"{name} must use uint16")
        planes.append(np.ascontiguousarray(array))
    display_bits, base_bits, coverage_bits = planes
    if base_bits.shape != display_bits.shape or coverage_bits.shape != display_bits.shape:
        raise ValueError("packed display, base, and coverage planes must share a shape")
    height, width = display_bits.shape
    transitions = np.empty((height, width), dtype=np.uint8)
    changed_count = np.empty((height, width), dtype=np.uint8)
    changed_samples = ctypes.c_int(0)
    changed_pixels = ctypes.c_int(0)
    protected_samples = ctypes.c_int(0)
    protected_pixels = ctypes.c_int(0)
    cancel_value = _cancel_value(cancel_flag)
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF packed repair was cancelled")
    dll = _load()
    code = dll.qsdf_repair_lane_bits(
        display_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        base_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        coverage_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        count,
        width,
        height,
        transitions.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        changed_count.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(changed_samples),
        ctypes.byref(changed_pixels),
        ctypes.byref(protected_samples),
        ctypes.byref(protected_pixels),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF packed repair was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF packed repair failed with status {code}")
    return PackedLaneRepairResult(
        transition_indices=transitions,
        changed_count=changed_count,
        changed_sample_count=int(changed_samples.value),
        changed_pixel_count=int(changed_pixels.value),
        protected_changed_sample_count=int(protected_samples.value),
        protected_changed_pixel_count=int(protected_pixels.value),
    )


def generate_threshold_transitions(
    transition_indices,
    angles,
    *,
    out=None,
    channel=0,
    cancel_flag=None,
    progress=None,
):
    """Generate one exact threshold channel from a compact transition map."""

    from .core import generate_threshold_transitions as fallback

    transitions = np.asarray(transition_indices)
    angle_values = np.asarray(angles, dtype=np.float64)
    if transitions.ndim != 2 or any(size <= 0 for size in transitions.shape):
        raise ValueError("transition indices must be a non-empty 2D plane")
    if not np.issubdtype(transitions.dtype, np.integer):
        raise TypeError("transition indices must contain integers")
    if angle_values.ndim != 1 or angle_values.size < 2 or angle_values.size > 16:
        raise ValueError("angles must contain between 2 and 16 keys")
    if not np.all(np.isfinite(angle_values)) or np.any(np.diff(angle_values) <= 1.0e-7):
        raise ValueError("angles must be finite and strictly increasing")
    if not np.isclose(angle_values[0], 0.0, atol=1.0e-7, rtol=0.0) or not np.isclose(
        angle_values[-1], 90.0, atol=1.0e-7, rtol=0.0
    ):
        raise ValueError("angles must include 0 and 90 degree endpoints")
    if np.any(transitions < 0) or np.any(transitions > angle_values.size):
        raise ValueError("transition indices are outside the angle sequence")
    transitions = np.ascontiguousarray(transitions, dtype=np.uint8)
    angle_values = np.ascontiguousarray(angle_values, dtype=np.float64)
    height, width = transitions.shape
    if out is None:
        destination = np.empty((height, width), dtype=np.uint16)
        pixel_stride = 1
        channel_offset = 0
    else:
        destination = np.asarray(out)
        if destination.dtype != np.uint16:
            raise TypeError("threshold output must use uint16")
        if not destination.flags.writeable or not destination.flags.c_contiguous:
            raise ValueError("threshold output must be writeable and C-contiguous")
        if destination.ndim == 2:
            if destination.shape != transitions.shape:
                raise ValueError("threshold output shape does not match transition plane")
            pixel_stride = 1
            channel_offset = 0
        elif destination.ndim == 3:
            channel_offset = int(channel)
            if destination.shape[:2] != transitions.shape or not 0 <= channel_offset < destination.shape[2]:
                raise ValueError("threshold output channel or shape is invalid")
            pixel_stride = int(destination.shape[2])
        else:
            raise ValueError("threshold output must be a 2D plane or HxWxC image")
    cancel_value = _cancel_value(cancel_flag)
    if progress is None:
        progress_value = ctypes.c_int(0)
    elif isinstance(progress, ctypes.c_int):
        progress_value = progress
    else:
        raise TypeError("progress must be a ctypes.c_int counter")
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF threshold generation was cancelled")
    if not native_packed_lane_available():
        result = fallback(
            transitions, angle_values, out=destination, channel=channel_offset
        )
        progress_value.value = int(angle_values.size)
        return result
    dll = _load()
    code = dll.qsdf_generate_threshold_transitions(
        transitions.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(angle_values.size),
        width,
        height,
        destination.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        pixel_stride,
        channel_offset,
        ctypes.byref(cancel_value),
        ctypes.byref(progress_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF threshold generation was cancelled")
    if code:
        raise NativeCoreError(
            f"Native Quick SDF transition threshold failed with status {code}"
        )
    return destination


__all__ = [
    "NATIVE_ABI",
    "NativeCoreError",
    "available",
    "bake_face_shadow_guide",
    "generate_threshold_transitions",
    "interpolate_binary_masks",
    "native_guide_bake_available",
    "native_interpolation_available",
    "native_packed_lane_available",
    "repair_packed_lane",
    "version",
]
