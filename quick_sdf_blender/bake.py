# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent UV normal rasterization and light-sweep baking.

The Blender runtime is responsible for extracting evaluated triangle UVs and
corner normals on the main thread.  This module deliberately accepts only
plain array-like values, so its reference implementation can run in a worker
thread, without ``bpy``, and on systems where the optional native DLL is not
available.

Array rows are top-down.  UV ``(0, 1)`` therefore maps to the top-left of the
returned image.  Pixels outside the supplied triangles are always Light.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


_EPSILON = 1.0e-12
_ANGLE_EPSILON = 1.0e-7
_AXIS_PARALLEL = 1.0 - 1.0e-6

# Blender's front view looks along world -Y.  Character up is world +Z.
# These are world axes; object rotation is applied by the caller.
DEFAULT_FRONT_AXIS = "NEG_Y"
WORLD_UP = (0.0, 0.0, 1.0)
WORLD_FRONT_AXES = {
    "NEG_Y": (0.0, -1.0, 0.0),
    "POS_Y": (0.0, 1.0, 0.0),
    "NEG_X": (-1.0, 0.0, 0.0),
    "POS_X": (1.0, 0.0, 0.0),
    "NEG_Z": (0.0, 0.0, -1.0),
    "POS_Z": (0.0, 0.0, 1.0),
}
_WORLD_UP_FALLBACKS = (
    (0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _positive_dimension(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _validated_triangles(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
) -> tuple[np.ndarray, np.ndarray]:
    uvs = np.asarray(triangle_uvs, dtype=np.float64)
    normals = np.asarray(corner_normals, dtype=np.float64)
    if uvs.ndim != 3 or uvs.shape[1:] != (3, 2):
        raise ValueError(f"triangle_uvs must have shape (N, 3, 2), got {uvs.shape}")
    if normals.ndim != 3 or normals.shape[1:] != (3, 3):
        raise ValueError(
            f"corner_normals must have shape (N, 3, 3), got {normals.shape}"
        )
    if uvs.shape[0] != normals.shape[0]:
        raise ValueError("triangle_uvs and corner_normals must contain the same triangles")
    if not np.all(np.isfinite(uvs)) or not np.all(np.isfinite(normals)):
        raise ValueError("triangle UVs and corner normals must be finite")
    lengths = np.linalg.norm(normals, axis=2)
    if np.any(lengths <= _EPSILON):
        raise ValueError("corner normals must have non-zero length")
    normalized = normals / lengths[..., None]
    return np.ascontiguousarray(uvs), np.ascontiguousarray(normalized)


def _validated_guide_angles(angles: Sequence[float] | np.ndarray) -> np.ndarray:
    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("guide angles must be a one-dimensional sequence with at least two keys")
    if not np.all(np.isfinite(values)) or np.any(values < -_ANGLE_EPSILON) or np.any(
        values > 90.0 + _ANGLE_EPSILON
    ):
        raise ValueError("guide angles must be finite values in the inclusive range 0..90")
    if np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("guide angles must be strictly increasing")
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0) or not np.isclose(
        values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0
    ):
        raise ValueError("guide angles must include 0 and 90 degree endpoints")
    return np.ascontiguousarray(values)


def _unit_vector(value: Sequence[float] | np.ndarray, *, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError(f"{name} must contain exactly three values")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be finite")
    length = float(np.linalg.norm(vector))
    if length <= _EPSILON:
        raise ValueError(f"{name} must have non-zero length")
    return vector / length


def _unit_direction(value: Sequence[float] | np.ndarray, *, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError(f"{name} must contain exactly three values")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be finite")
    length = float(np.linalg.norm(vector))
    if length <= _EPSILON:
        raise ValueError(f"{name} must have non-zero length")
    return vector / length


def _rotation_matrix(object_rotation: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return the rotational part of an object-to-world 3x3 matrix."""

    matrix = np.asarray(object_rotation, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("object rotation must be a finite 3x3 matrix")
    # A scaled object matrix must not skew the light basis.  SVD removes scale
    # while preserving a proper rotation, including mirrored objects.
    left, _, right = np.linalg.svd(matrix)
    rotation = left @ right
    if float(np.linalg.det(rotation)) < 0.0:
        left = left.copy()
        left[:, -1] *= -1.0
        rotation = left @ right
    return rotation


def local_character_axes(
    object_rotation: np.ndarray | Sequence[object],
    world_forward: Sequence[float] | np.ndarray,
    world_up: Sequence[float] | np.ndarray = WORLD_UP,
) -> tuple[np.ndarray, np.ndarray]:
    """Map a world front/up pair into the object's local bake frame.

    Corner normals from an evaluated mesh are object-local.  The default front
    is world -Y and the default up is world +Z, so a mesh rotated to stand in
    Blender still sweeps across the face shown in the viewport.
    """

    rotation = _rotation_matrix(object_rotation)
    forward = _unit_direction(world_forward, name="world forward")
    up = _unit_direction(world_up, name="world up")
    if abs(float(np.dot(forward, up))) > _AXIS_PARALLEL:
        for candidate in _WORLD_UP_FALLBACKS:
            trial = np.asarray(candidate, dtype=np.float64)
            if abs(float(np.dot(forward, trial))) <= _AXIS_PARALLEL:
                up = trial
                break
        else:
            raise ValueError("world forward has no perpendicular up axis")
    local_forward = _unit_direction(rotation.T @ forward, name="forward")
    local_up = rotation.T @ up
    local_up = local_up - local_forward * float(np.dot(local_up, local_forward))
    local_up = _unit_direction(local_up, name="up")
    return local_forward, local_up


def guide_light_directions(
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
    side: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return lilToon guide directions for the 0..90 authoring sweep.

    Authoring angles describe progress rather than a literal light yaw.  At
    0 degrees the light is 120 degrees from face-forward (rear-oblique), the
    middle of the rail is a 90 degree side light, and 90 degrees is a
    60 degree front-oblique light.  This matches lilToon's ``frontDot`` domain
    while keeping the artist-facing masks ordered from least to most Light.
    """

    angle_values = _validated_guide_angles(angles)
    side_name = str(side).upper()
    if side_name not in {"RIGHT", "LEFT"}:
        raise ValueError("side must be RIGHT or LEFT")
    up_vector = _unit_vector(up, name="up")
    front = _unit_vector(forward, name="forward")
    front = front - up_vector * float(np.dot(front, up_vector))
    front_length = float(np.linalg.norm(front))
    if front_length <= _EPSILON:
        raise ValueError("forward and up must not be parallel")
    front /= front_length
    side_vector = np.cross(up_vector, front)
    side_vector /= float(np.linalg.norm(side_vector))
    if side_name == "LEFT":
        side_vector = -side_vector

    progress = angle_values / 90.0
    front_dot = progress - 0.5
    side_dot = np.sqrt(np.maximum(0.0, 1.0 - front_dot * front_dot))
    directions = (
        side_vector[None, :] * side_dot[:, None]
        + front[None, :] * front_dot[:, None]
    )
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    return angle_values, np.ascontiguousarray(directions, dtype=np.float32)


def shadow_amount_cutoff(shadow_amount: float) -> float:
    """Map the artist-facing 0..100 Shadow Amount to the N·L cutoff."""

    if isinstance(shadow_amount, bool):
        raise TypeError("shadow_amount must be a number")
    amount = float(shadow_amount)
    if not np.isfinite(amount) or not 0.0 <= amount <= 100.0:
        raise ValueError("shadow_amount must be in the range 0..100")
    return -0.15 + amount * 0.005


def rasterize_uv_normals(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
    width: int,
    height: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Rasterize interpolated corner normals into a top-down UV image.

    Pixel centres are sampled.  Degenerate and fully out-of-tile triangles are
    ignored.  If triangles overlap, the later triangle deterministically wins;
    callers authoring shared UVs should filter evaluated geometry to the chosen
    character side before calling this function.

    Returns ``(normal_image, occupancy)`` where the first array is contiguous
    float32 ``(H, W, 3)`` and the second is contiguous bool ``(H, W)``.
    """

    uvs, normals = _validated_triangles(triangle_uvs, corner_normals)
    image_width = _positive_dimension(width, name="width")
    image_height = _positive_dimension(
        image_width if height is None else height, name="height"
    )
    normal_image = np.zeros((image_height, image_width, 3), dtype=np.float32)
    occupancy = np.zeros((image_height, image_width), dtype=np.bool_)

    for triangle_uv, triangle_normal in zip(uvs, normals, strict=True):
        # Integer image coordinates identify pixel centres after the -0.5
        # offset.  V is inverted because NumPy row zero is the image top.
        x = triangle_uv[:, 0] * image_width - 0.5
        y = (1.0 - triangle_uv[:, 1]) * image_height - 0.5
        x0 = max(0, int(np.ceil(float(np.min(x)))))
        x1 = min(image_width - 1, int(np.floor(float(np.max(x)))))
        y0 = max(0, int(np.ceil(float(np.min(y)))))
        y1 = min(image_height - 1, int(np.floor(float(np.max(y)))))
        if x1 < x0 or y1 < y0:
            continue

        denominator = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (
            y[0] - y[2]
        )
        if abs(float(denominator)) <= _EPSILON:
            continue
        grid_y, grid_x = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
        weight0 = (
            (y[1] - y[2]) * (grid_x - x[2])
            + (x[2] - x[1]) * (grid_y - y[2])
        ) / denominator
        weight1 = (
            (y[2] - y[0]) * (grid_x - x[2])
            + (x[0] - x[2]) * (grid_y - y[2])
        ) / denominator
        weight2 = 1.0 - weight0 - weight1
        inside = (
            (weight0 >= -1.0e-9)
            & (weight1 >= -1.0e-9)
            & (weight2 >= -1.0e-9)
        )
        if not np.any(inside):
            continue

        interpolated = (
            weight0[..., None] * triangle_normal[0]
            + weight1[..., None] * triangle_normal[1]
            + weight2[..., None] * triangle_normal[2]
        )
        lengths = np.linalg.norm(interpolated, axis=2)
        safe = inside & (lengths > _EPSILON)
        if np.any(safe):
            interpolated[safe] /= lengths[safe, None]
        cancelled = inside & ~safe
        if np.any(cancelled):
            # Opposing custom normals can cancel exactly.  The dominant corner
            # is a deterministic, unit-length fallback for those rare samples.
            weights = np.stack((weight0, weight1, weight2), axis=2)
            dominant = np.argmax(weights[cancelled], axis=1)
            interpolated[cancelled] = triangle_normal[dominant]

        region = normal_image[y0 : y1 + 1, x0 : x1 + 1]
        region[inside] = interpolated[inside].astype(np.float32, copy=False)
        occupancy[y0 : y1 + 1, x0 : x1 + 1][inside] = True

    return np.ascontiguousarray(normal_image), np.ascontiguousarray(occupancy)


def bake_face_shadow_guide(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
    side: str,
    shadow_amount: float,
    width: int,
    height: int | None = None,
    *,
    enforce_monotonic: bool = True,
    padding_px: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Bake the rear-oblique to front-oblique authoring guide.

    Every key, including 90 degrees, is a real N·L sample.  The directly-behind
    pure-black and directly-front pure-white endpoints are not part of this
    guide; export adds those 180 and 0 degree sentinels when writing the
    threshold texture. ``padding_px`` copies UV edge values outward without
    changing occupied texels; the returned occupancy remains the original UVs.
    """

    angle_values, directions = guide_light_directions(angles, forward, up, side)
    cutoff = shadow_amount_cutoff(shadow_amount)
    normals, occupancy = rasterize_uv_normals(
        triangle_uvs, corner_normals, width, height
    )
    dots = np.einsum("hwc,ac->ahw", normals, directions, optimize=True)
    masks = (~occupancy)[None, :] | (dots >= cutoff)
    if enforce_monotonic:
        masks = np.ascontiguousarray(masks, dtype=np.bool_)
        for closer in range(1, masks.shape[0]):
            masks[closer] |= masks[closer - 1]
    from .uv_padding import pad_guide_masks

    pad_guide_masks(masks, occupancy, padding_px)
    return np.ascontiguousarray(masks), occupancy


__all__ = [
    "DEFAULT_FRONT_AXIS",
    "WORLD_FRONT_AXES",
    "WORLD_UP",
    "bake_face_shadow_guide",
    "guide_light_directions",
    "local_character_axes",
    "rasterize_uv_normals",
    "shadow_amount_cutoff",
]
