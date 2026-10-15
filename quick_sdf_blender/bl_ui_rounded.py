# SPDX-License-Identifier: GPL-3.0-or-later
"""Rounded 2D primitives for the viewport overlay.

The rounded outline/UV-mesh helpers are ported from BlenderKit's ``ui_bgl.py``
(commit ``f4d16f7``, GPL-2.0-or-later, see ``THIRD_PARTY_NOTICES.md``):
``_parse_radius_value``, ``_rounded_rect_outline`` and the rounded UV mapping of
``_rounded_rect_mesh``. They stay geometric and are used for rounded *image*
clipping, where a custom image shader would have to reproduce Blender's colour
space conversion.

Filled shapes and borders are drawn by a signed-distance-field shader instead.
That keeps corners identical on all four sides and anti-aliased, which the
geometric ``LINE_STRIP`` border could not do (line width is quantised by the
driver, so edges came out uneven). Local adaptations: region-pixel coordinates
only, ``(x0, y0, x1, y1)`` arguments and cached GPU batches.
"""
from __future__ import annotations

from collections import OrderedDict
import math
from typing import Any, Mapping, Sequence

SEGMENTS_DEFAULT = 4
_BATCH_CACHE_LIMIT = 256
_RECT_CACHE: OrderedDict[tuple, Any] = OrderedDict()
_SDF_CACHE: OrderedDict[tuple, Any] = OrderedDict()
_COLOR_SHADER: Any = None
_SDF_SHADER: Any = None

_SDF_VERTEX = """
void main() {
  vlocal = local;
  gl_Position = mvp * vec4(pos, 0.0, 1.0);
}
"""


_SDF_FRAGMENT = """
float sd_round(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + vec2(r);
  return min(max(q.x, q.y), 0.0) + length(max(q, vec2(0.0))) - r;
}

float corner_radius(vec2 p) {
  // corner_radii is (top-left, top-right, bottom-right, bottom-left).
  if (p.x > 0.0) {
    return (p.y > 0.0) ? corner_radii.y : corner_radii.z;
  }
  return (p.y > 0.0) ? corner_radii.x : corner_radii.w;
}

void main() {
  vec2 half_size = rect_size * 0.5;
  vec2 p = (vlocal - vec2(0.5)) * rect_size;
  float d = sd_round(p, half_size, corner_radius(p));
  float aa = max(fwidth(d), 0.0001);
  float outer = 1.0 - smoothstep(-aa, aa, d);
  float inner_d = sd_round(p, max(half_size - vec2(border_thickness), vec2(0.0)),
                           max(corner_radius(p) - border_thickness, 0.0));
  float inner = 1.0 - smoothstep(-aa, aa, inner_d);
  float ring = clamp(outer - inner, 0.0, 1.0);
  vec4 body = mix(fill_color, border_color, ring / max(outer, 1.0e-4));
  fragColor = vec4(body.rgb, body.a * outer);
}
"""


def _color_shader() -> Any:
    global _COLOR_SHADER
    if _COLOR_SHADER is None:
        import gpu

        _COLOR_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _COLOR_SHADER


def _sdf_shader() -> Any:
    """Lazily build the rounded-rect SDF shader once per session."""

    global _SDF_SHADER
    if _SDF_SHADER is None:
        import gpu

        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, "VEC2", "pos")
        info.vertex_in(1, "VEC2", "local")
        iface = gpu.types.GPUStageInterfaceInfo("qsdf_rounded_iface")
        iface.smooth("VEC2", "vlocal")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "fragColor")
        # Custom shaders do not receive the built-in matrix uniforms, so the
        # projection has to travel as a push constant.
        info.push_constant("MAT4", "mvp")
        info.push_constant("VEC2", "rect_size")
        info.push_constant("VEC4", "corner_radii")
        info.push_constant("FLOAT", "border_thickness")
        info.push_constant("VEC4", "fill_color")
        info.push_constant("VEC4", "border_color")
        info.vertex_source(_SDF_VERTEX)
        info.fragment_source(_SDF_FRAGMENT)
        _SDF_SHADER = gpu.shader.create_from_info(info)
    return _SDF_SHADER


def parse_radius(value: Any, *, max_radius: float, min_dimension: float) -> float:
    """Return a clamped pixel radius.

    Accepts raw pixels, numeric strings, percentage strings (``"50%"``) and
    mappings with ``percent`` / ``pct`` / ``ratio`` / ``px`` keys.
    """

    def clamp(radius: float) -> float:
        return max(0.0, min(float(radius), max_radius))

    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                return clamp(float(text[:-1].strip()) * 0.01 * min_dimension)
            except ValueError:
                return 0.0
        try:
            value = float(text)
        except ValueError:
            return 0.0
    elif isinstance(value, Mapping):
        for key in ("percent", "pct", "ratio"):
            if key in value:
                try:
                    factor = float(value[key])
                except (TypeError, ValueError):
                    factor = 0.0
                if key != "ratio":
                    factor *= 0.01
                return clamp(factor * min_dimension)
        if "px" in value:
            try:
                return clamp(value["px"])
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    try:
        return clamp(value)
    except (TypeError, ValueError):
        return 0.0


def outline(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: Any = 0.0,
    segments: int = SEGMENTS_DEFAULT,
) -> list[tuple[float, float]]:
    """Return the closed outline of a rounded rectangle, corner order TL/TR/BR/BL."""

    width = float(x1) - float(x0)
    height = float(y1) - float(y0)
    if width <= 0.0 or height <= 0.0:
        return []
    min_dimension = min(width, height)
    max_radius = max(0.0, min_dimension * 0.5)

    if isinstance(radius, (tuple, list)):
        raw_radii = list(radius)
    else:
        raw_radii = [radius]
    if not raw_radii:
        raw_radii = [0.0]
    radii = [parse_radius(value, max_radius=max_radius, min_dimension=min_dimension)
             for value in raw_radii]
    while len(radii) < 4:
        radii.append(radii[-1])
    r_tl, r_tr, r_br, r_bl = radii[:4]

    if not any(radii):
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]

    steps = max(1, int(segments))
    points: list[tuple[float, float]] = []

    def emit(cx, cy, start_angle, end_angle, radius_value, fallback):
        if radius_value <= 0.0:
            points.append(fallback)
            return
        for step in range(steps + 1):
            angle = start_angle + (end_angle - start_angle) * (step / steps)
            points.append((cx + math.cos(angle) * radius_value,
                           cy + math.sin(angle) * radius_value))

    emit(x0 + r_tl, y1 - r_tl, math.pi, math.pi * 0.5, r_tl, (x0, y1))
    emit(x1 - r_tr, y1 - r_tr, math.pi * 0.5, 0.0, r_tr, (x1, y1))
    emit(x1 - r_br, y0 + r_br, 0.0, -math.pi * 0.5, r_br, (x1, y0))
    emit(x0 + r_bl, y0 + r_bl, -math.pi * 0.5, -math.pi, r_bl, (x0, y0))
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def _corner_values(x0, y0, x1, y1, radius) -> list[float]:
    """Resolve a radius spec to pixel radii for (top-left, top-right, bottom-right, bottom-left)."""

    min_dimension = min(float(x1) - float(x0), float(y1) - float(y0))
    max_radius = max(0.0, min_dimension * 0.5)
    if isinstance(radius, (tuple, list)):
        values = [parse_radius(value, max_radius=max_radius, min_dimension=min_dimension)
                  for value in radius]
        if not values:
            values = [0.0]
        while len(values) < 4:
            values.append(values[-1])
        return values[:4]
    value = parse_radius(radius, max_radius=max_radius, min_dimension=min_dimension)
    return [value, value, value, value]


def draw(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    fill: Sequence[float] | None = None,
    radius: Any = 0.0,
    border: Sequence[float] | None = None,
    border_thickness: float = 1.0,
    segments: int = SEGMENTS_DEFAULT,
) -> None:
    """Fill and/or outline a rectangle in region pixel coordinates."""

    from gpu_extras.batch import batch_for_shader

    width = float(x1) - float(x0)
    height = float(y1) - float(y0)
    if width <= 0.0 or height <= 0.0:
        return
    if fill is None and (border is None or border_thickness <= 0.0):
        return
    import gpu

    # The SDF shader writes alpha=0 outside the shape, so every draw here needs
    # blending.  Other drawing (notably ``blf``) can leave it disabled.
    previous_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        _draw_shape(x0, y0, x1, y1, fill, radius, border, border_thickness, segments)
    finally:
        gpu.state.blend_set(previous_blend)


def _draw_shape(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    fill: Sequence[float] | None,
    radius: Any,
    border: Sequence[float] | None,
    border_thickness: float,
    segments: int,
) -> None:
    from gpu_extras.batch import batch_for_shader

    width = float(x1) - float(x0)
    height = float(y1) - float(y0)
    if width <= 0.0 or height <= 0.0:
        return
    fill_color = tuple(fill) if fill is not None else (0.0, 0.0, 0.0, 0.0)
    border_color = tuple(border) if border is not None else fill_color
    if border is None:
        border_thickness = 0.0
    corners = _corner_values(x0, y0, x1, y1, radius)
    max_corner = max(corners)

    if max_corner <= 0.0 and border_thickness <= 0.0:
        # Plain rectangles stay on the cheap builtin path.
        shader = _color_shader()
        key = (x0, y0, x1, y1)
        batch = _RECT_CACHE.get(key)
        if batch is None:
            batch = batch_for_shader(
                shader, "TRIS",
                {"pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1))},
                indices=((0, 1, 2), (2, 3, 0)),
            )
            _RECT_CACHE[key] = batch
            while len(_RECT_CACHE) > _BATCH_CACHE_LIMIT:
                _RECT_CACHE.popitem(last=False)
        else:
            _RECT_CACHE.move_to_end(key)
        shader.bind()
        shader.uniform_float("color", fill_color)
        batch.draw(shader)
        return

    import gpu

    shader = _sdf_shader()
    key = (x0, y0, x1, y1)
    batch = _SDF_CACHE.get(key)
    if batch is None:
        batch = batch_for_shader(
            shader, "TRIS",
            {"pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
             "local": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))},
            indices=((0, 1, 2), (2, 3, 0)),
        )
        _SDF_CACHE[key] = batch
        while len(_SDF_CACHE) > _BATCH_CACHE_LIMIT:
            _SDF_CACHE.popitem(last=False)
    else:
        _SDF_CACHE.move_to_end(key)
    matrix = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.bind()
    shader.uniform_float("mvp", matrix)
    shader.uniform_float("rect_size", (width, height))
    shader.uniform_float("corner_radii", tuple(corners))
    shader.uniform_float("border_thickness", max(0.0, float(border_thickness)))
    shader.uniform_float("fill_color", fill_color)
    shader.uniform_float("border_color", border_color)
    batch.draw(shader)


def clear_caches() -> None:
    """Drop cached batches; called from the owning addon's unregister path."""

    global _SDF_SHADER
    _RECT_CACHE.clear()
    _SDF_CACHE.clear()
    _SDF_SHADER = None


def mesh(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: Any = 0.0,
    crop: Sequence[float] = (0.0, 0.0, 1.0, 1.0),
    segments: int = SEGMENTS_DEFAULT,
) -> tuple[list, list, list] | None:
    """Return ``(coords, uvs, indices)`` clipping an image to a rounded rect."""

    width = float(x1) - float(x0)
    height = float(y1) - float(y0)
    if width <= 0.0 or height <= 0.0:
        return None
    loop = outline(x0, y0, x1, y1, radius, segments)
    if not loop:
        return None
    if len(loop) > 1 and loop[0] == loop[-1]:
        loop = loop[:-1]
    if len(loop) < 3:
        return None
    u0, v0, u1, v1 = (float(value) for value in crop)
    u_span = u1 - u0 or 1.0
    v_span = v1 - v0 or 1.0
    coords = list(loop)
    uvs = [((vx - x0) / width * u_span + u0, (vy - y0) / height * v_span + v0)
           for vx, vy in coords]
    indices = [(0, index, index + 1) for index in range(1, len(coords) - 1)]
    return coords, uvs, indices
