# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only viewport light indicator with a mesh-face-selected center region."""
from __future__ import annotations

import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector

from .ui import context as ui_context

_HANDLE = None
_CACHE = {}


def _attribute_name(project):
    return "_qsdf_light_center_" + str(project.uuid).replace("-", "")


def _redraw():
    from .ui import tag_editors_for_redraw
    tag_editors_for_redraw()


class QUICKSDF_OT_light_center_from_faces(bpy.types.Operator):
    bl_idname = "quicksdf.light_center_from_faces"
    bl_label = "Use Selected Faces as Light Center"
    bl_description = "Save selected mesh faces as the light indicator center region, without changing Face Area"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == "MESH" and obj.mode == "EDIT"
                and ui_context.object_project(context) is not None
                and context.area is not None and context.area.type == "VIEW_3D")

    def execute(self, context):
        import bmesh

        project = ui_context.object_project(context)
        obj = project.target_object
        bm = bmesh.from_edit_mesh(obj.data)
        selected = [face for face in bm.faces if not face.hide and face.select]
        if not selected or not any(f.calc_area() > 1.0e-12 for f in selected):
            self.report({"ERROR"}, "Select mesh faces with non-zero surface area first")
            return {"CANCELLED"}
        # BMesh integer layers round-trip as INT/FACE attributes, including undo.
        layer = bm.faces.layers.int.get(_attribute_name(project))
        if layer is None:
            layer = bm.faces.layers.int.new(_attribute_name(project))
        selected_set = set(selected)
        for face in bm.faces:
            face[layer] = int(face in selected_set)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _CACHE.clear()
        _redraw()
        self.report({"INFO"}, f"Light center saved from {len(selected)} mesh faces")
        return {"FINISHED"}


class QUICKSDF_OT_light_center_reset(bpy.types.Operator):
    bl_idname = "quicksdf.light_center_reset"
    bl_label = "Use Face Area as Light Center"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and ui_context.object_project(context) is not None

    def execute(self, context):
        import bmesh

        project = ui_context.object_project(context)
        obj = project.target_object
        name = _attribute_name(project)
        if obj.mode == "EDIT":
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.faces.layers.int.get(name)
            if layer is not None:
                bm.faces.layers.int.remove(layer)
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            attribute = obj.data.attributes.get(name)
            if attribute is not None:
                obj.data.attributes.remove(attribute)
                obj.data.update()
        _CACHE.clear()
        _redraw()
        return {"FINISHED"}


def _geometry(context, project):
    obj = project.target_object
    if obj is None or obj.type != "MESH" or obj.mode == "EDIT":
        return None
    custom = obj.data.attributes.get(_attribute_name(project)) is not None
    name = _attribute_name(project) if custom else str(project.face_region_attribute_name)
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world
    key = (str(project.uuid), obj.as_pointer(), depsgraph.as_pointer(), name,
           tuple(value for row in matrix for value in row))
    if key in _CACHE:
        return _CACHE[key]
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        return None
    try:
        attribute = mesh.attributes.get(name)
        if (attribute is None or attribute.domain != "FACE"
                or attribute.data_type not in {"INT", "BOOLEAN"}
                or len(attribute.data) != len(mesh.polygons)):
            return None
        mesh.calc_loop_triangles()
        weighted = Vector((0, 0, 0))
        total = 0.0
        vertices = set()
        for triangle in mesh.loop_triangles:
            if not attribute.data[triangle.polygon_index].value:
                continue
            a, b, c = (matrix @ mesh.vertices[i].co for i in triangle.vertices)
            area = (b - a).cross(c - a).length * 0.5
            weighted += (a + b + c) * (area / 3.0)
            total += area
            vertices.update(triangle.vertices)
        if total <= 1.0e-12:
            return None
        center = weighted / total
        radius = max(math.hypot(*(matrix @ mesh.vertices[i].co - center)[:2])
                     for i in vertices) * 2.4
        if radius <= 1.0e-8:
            return None
        result = (center, radius)
        if len(_CACHE) >= 16:
            _CACHE.clear()
        _CACHE[key] = result
        return result
    finally:
        evaluated.to_mesh_clear()


def _draw():
    from .viewport_lighting import _project as viewport_project

    context = bpy.context
    project = viewport_project(context)
    if project is None:
        return
    try:
        geometry = _geometry(context, project)
        if geometry is None:
            return
        center, radius = geometry
        rotation = project.target_object.matrix_world.to_quaternion()
        front = Vector(project.forward_vector)
        up = Vector(project.up_vector).normalized()
        front -= up * front.dot(up)
        if front.length <= 1.0e-8:
            return
        front.normalize()
        side = up.cross(front).normalized()
        if project.active_side == "LEFT":
            side.negate()
        front = rotation @ front
        side = rotation @ side
        # Flatten the reference frame, not the light vector: a tilted mesh's
        # projected light vector can lose all sweep motion.
        front.z = side.z = 0.0
        # Imported meshes may rotate local forward onto world Z. The reference
        # arc must remain visible even when that axis has no XY projection.
        if front.length <= 1.0e-8:
            front = rotation @ up
            front.z = 0.0
            if front.length <= 1.0e-8:
                return
        front.normalize()
        if side.length <= 1.0e-8:
            side = Vector((-front.y, front.x, 0.0))
            if project.active_side == "LEFT":
                side.negate()
        side.normalize()
        orientation = 1.0 if front.cross(side).z >= 0.0 else -1.0
        tangent = Vector((-front.y, front.x, 0.0)) * orientation
        points = [center + radius * (front * math.cos(i * math.pi / 64)
                                    + tangent * math.sin(i * math.pi / 64))
                  for i in range(65)]
        ticks = []
        for index in range(9):
            angle = index * math.pi / 8.0
            direction = front * math.cos(angle) + tangent * math.sin(angle)
            length = 0.055 if index in {0, 4, 8} else 0.030
            ticks.extend((center + direction * radius,
                          center + direction * (radius * (1.0 + length))))
        # The ring still shows 180 behind through 0 in front.  Inside the
        # baked keys the arrow follows that key's light.  Past the first or
        # last key it follows the preview-only sentinel yaw.
        from .timeline import _stored_preview_yaw

        stored_yaw = _stored_preview_yaw(project)
        if stored_yaw is None:
            progress = max(0.0, min(1.0, float(project.seek_angle) / 90.0))
            front_dot = progress - 0.5
            yaw = math.acos(max(-1.0, min(1.0, front_dot)))
        else:
            yaw = math.radians(max(0.0, min(180.0, stored_yaw)))
        light = front * math.cos(yaw) + tangent * math.sin(yaw)
        tail = center + light * radius
        tip = center + light * (radius * 0.70)
        perpendicular = Vector((-light.y, light.x, 0.0))
        wings = [tip + light * (radius * 0.10) + perpendicular * (radius * sign * 0.055)
                 for sign in (-1, 1)]
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        blend = gpu.state.blend_get()
        depth = gpu.state.depth_test_get()
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("NONE")
            shader.bind()
            shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
            shader.uniform_float("lineWidth", 2.0 * context.preferences.system.ui_scale)
            shader.uniform_float("color", (0.50, 0.65, 0.75, 0.45))
            batch_for_shader(shader, "LINE_STRIP", {"pos": points}).draw(shader)
            shader.uniform_float("color", (0.50, 0.65, 0.75, 0.75))
            batch_for_shader(shader, "LINES", {"pos": ticks}).draw(shader)
            shader.uniform_float("color", (1.0, 0.68, 0.20, 0.95))
            batch_for_shader(shader, "LINES", {"pos": [tail, tip, tip, wings[0], tip, wings[1]]}).draw(shader)
        finally:
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        return


@persistent
def _invalidate(*_args):
    _CACHE.clear()


def register_overlay():
    global _HANDLE
    if _HANDLE is None:
        _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.load_post,
                     bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _invalidate not in handlers:
            handlers.append(_invalidate)


def unregister_overlay():
    global _HANDLE
    if _HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLE, "WINDOW")
        _HANDLE = None
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.load_post,
                     bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _invalidate in handlers:
            handlers.remove(_invalidate)
    _CACHE.clear()


CLASSES = (QUICKSDF_OT_light_center_from_faces, QUICKSDF_OT_light_center_reset)
