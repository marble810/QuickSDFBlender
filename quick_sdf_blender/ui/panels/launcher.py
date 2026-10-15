# SPDX-License-Identifier: GPL-3.0-or-later
"""The Quick SDF sidebar section: create, open, edit and tune one face.

Every control the project needs is listed here without a second container
panel.  Only the Front axis chooser folds, because it is set once and then
left alone.
"""

from __future__ import annotations

from ... import runtime
from ...i18n import tr
from ...model import DEFAULT_KEY_ANGLES
from .. import context as ui_context
from .. import widgets
from ..base import SidebarPanel


class QSDF_PT_launcher(SidebarPanel):
    bl_idname = "QSDF_PT_launcher"
    bl_label = "Quick SDF Paint"
    bl_order = 10

    def draw(self, context):
        layout = self.layout
        project = ui_context.sidebar_project(context)
        if project is None:
            self._draw_create(layout, context)
            return
        studio = ui_context.is_studio_active(context, project)
        if studio:
            self._draw_paint_controls(layout, context, project)
        else:
            layout.label(text=project.name, icon="IMAGE_DATA")
            button = layout.column()
            button.scale_y = 1.7
            button.operator(
                "quicksdf.studio_enter", text=tr("Open Quick SDF Paint"), icon="WORKSPACE"
            )
        self._draw_project_settings(layout, context, project)
        if studio:
            widgets.export_section(layout, project)
            layout.operator("quicksdf.studio_exit", text=tr("Exit Quick SDF"), icon="X")

    # --- creation --------------------------------------------------------

    def _draw_create(self, layout, context) -> None:
        """Offer the face selection that creates a project, then its settings."""

        obj = context.active_object
        if obj is None or obj.type != "MESH":
            box = layout.box()
            box.label(text=tr("Select the face mesh"), icon="OUTLINER_OB_MESH")
            box.label(text=tr("Then create a paint-ready face shadow."))
        else:
            self._draw_create_action(layout, context, obj)
        self._draw_create_settings(layout, context)

    def _draw_create_action(self, layout, context, obj) -> None:
        info = layout.box()
        info.label(text=obj.name, icon="OUTLINER_OB_MESH")
        selected = runtime.selected_edit_face_mask(obj)
        if selected is not None:
            selected_count = int(selected.sum())
            info.label(
                text=tr("Face Area: %s selected faces") % f"{selected_count:,}",
                icon="FACESEL",
            )
            if selected_count == 0:
                warning = layout.box()
                warning.alert = True
                warning.label(
                    text=tr("Select the face polygons in the 3D View"), icon="INFO"
                )
            button = layout.column()
            button.enabled = selected_count > 0
            button.scale_y = 1.7
            button.operator(
                "quicksdf.create_and_edit",
                text=tr("Create & Edit Selected Faces"),
                icon="BRUSH_DATA",
            )
            layout.operator(
                "quicksdf.face_area_cancel",
                text=tr("Cancel Face Area Selection"),
                icon="X",
            )
            return
        candidates = runtime.material_slot_candidates(obj)
        active_index = int(getattr(obj, "active_material_index", 0))
        slot_index = (
            active_index
            if active_index in candidates
            else (int(candidates[0]) if candidates else 0)
        )
        material = (
            obj.material_slots[slot_index].material
            if 0 <= slot_index < len(obj.material_slots)
            else None
        )
        material_name = material.name if material else tr("A material will be created")
        if len(candidates) > 1:
            info.label(text=tr("Choose a surface when creating"), icon="MATERIAL")
        else:
            info.label(
                text=tr("Face Area: Entire %s surface") % material_name, icon="MATERIAL"
            )
        button = layout.column()
        button.scale_y = 1.7
        button.operator(
            "quicksdf.create_and_edit", text=tr("Create & Edit"), icon="BRUSH_DATA"
        )
        layout.operator(
            "quicksdf.face_area_select",
            text=tr("Select Face Area…"),
            icon="FACESEL",
        )
        layout.label(
            text=tr("Use Face Area when the face shares a mesh with the body."),
            icon="INFO",
        )

    def _draw_create_settings(self, layout, context) -> None:
        """Settings that only exist before the project is created."""

        settings = context.scene.quick_sdf_settings
        layout.prop(settings, "resolution")
        layout.prop(settings, "initialization")
        if settings.initialization == "NORMAL_GUIDE":
            layout.prop(settings, "guide_padding_px")
        if settings.initialization == "EXISTING":
            layout.prop(settings, "source_image")
        if int(settings.resolution) >= 4096:
            layout.label(text=tr("4096 uses substantial memory."), icon="ERROR")
        self._draw_front(layout, context, None)

    # --- project ---------------------------------------------------------

    def _draw_paint_controls(self, layout, context, project) -> None:
        """Daily paint controls while the Studio hosts the project."""

        status = layout.box()
        status.label(text=tr("Editing %s") % project.target_object.name, icon="BRUSH_DATA")
        editing_aux = widgets.aux_edit_status(status, context, project)
        if ui_context.curve_edit_active(context):
            status.label(text=tr("Terminator Curve"), icon="CURVE_BEZCURVE")
            row = status.row(align=True)
            row.operator(
                "quicksdf.curve_edit_confirm",
                text=tr("Fill & Back to Paint"),
                icon="CHECKMARK",
            )
            row.operator(
                "quicksdf.curve_edit_cancel", text=tr("Back to Paint"), icon="LOOP_BACK"
            )
        elif not editing_aux:
            widgets.paint_value_row(status, project)
            status.operator(
                "quicksdf.curve_edit_enter",
                text=tr("Terminator Curve"),
                icon="CURVE_BEZCURVE",
            )
            widgets.uv_padding_row(status, project)
            widgets.display_row(status, project)

    def _draw_project_settings(self, layout, context, project) -> None:
        """Project facts and tuning, listed flat for every project state."""

        if bool(project.base_needs_update):
            warning = layout.box()
            warning.label(text=tr("Base needs update"), icon="ERROR")
            warning.operator(
                "quicksdf.bake_base", text=tr("Rebake Base"), icon="FILE_REFRESH"
            )

        if bool(getattr(project, "guide_direction_warning", False)):
            direction = layout.box()
            direction.alert = True
            direction.label(
                text=tr(str(project.guide_direction_message)), icon="ORIENTATION_VIEW"
            )
            direction.operator(
                "quicksdf.set_forward_from_view",
                text=tr("Use This View as Front"),
                icon="VIEW_CAMERA",
            )

        facts = layout.column(align=True)
        facts.prop(project, "target_object", text=tr("Object"))
        try:
            slot_count = len(runtime.face_region_material_slots(project))
        except ValueError:
            slot_count = 0
        materials = tr("material") if slot_count == 1 else tr("materials")
        facts.label(
            text=tr("Face Area: %s faces · %s %s")
            % (f"{int(project.face_region_face_count):,}", slot_count, materials),
            icon="FACESEL",
        )
        facts.prop(project, "uv_map_name", text=tr("UV Map"))
        facts.prop(project, "resolution")

        self._draw_front(layout, context, project)
        widgets.normals_toggle(layout, project, enabled=project.preview_enabled)

        guide = widgets.section(layout, tr("Adjust Shadow Guide"))
        guide.prop(project, "guide_shadow_amount", text=tr("Shadow Amount"), slider=True)
        guide.prop(project, "guide_padding_px")
        guide.operator(
            "quicksdf.bake_base", text=tr("Update Shadow Guide"), icon="FILE_REFRESH"
        )
        if bool(getattr(project, "guide_direction_warning", False)):
            guide.alert = True
            guide.label(text=tr(str(project.guide_direction_message)), icon="ERROR")

        mirror = widgets.section(layout, tr("Mirror"))
        mirror.prop(project, "symmetry_mode", text=tr("Layout"))
        mirror.prop(project, "authoring_side", text=tr("Paint Side"))
        if bool(project.mirror_enabled):
            mirror.operator(
                "quicksdf.break_mirror", text=tr("Break Mirror"), icon="UNLINKED"
            )
        widgets.mirror_confirmation(layout, project)

        keys = widgets.section(layout, tr("Angle Keys"))
        row = keys.row(align=True)
        add = row.operator("quicksdf.key_add", text=tr("Add Angle…"), icon="ADD")
        add.duplicate = False
        duplicate = row.operator(
            "quicksdf.key_add", text=tr("Duplicate Angle…"), icon="DUPLICATE"
        )
        duplicate.duplicate = True
        active = runtime.active_angle(project)
        if active is not None:
            row = keys.row(align=True)
            row.enabled = not any(
                abs(float(active.angle) - angle) < 1.0e-5
                for angle in DEFAULT_KEY_ANGLES
            )
            move = row.operator(
                "quicksdf.key_move", text=tr("Move / Retime"), icon="ARROW_LEFTRIGHT"
            )
            move.uuid = str(active.uuid)
            remove = row.operator("quicksdf.key_delete", text=tr("Delete"), icon="REMOVE")
            remove.uuid = str(active.uuid)

        self._draw_review(layout, project)

    def _draw_review(self, layout, project) -> None:
        """Recovery actions, export adjustments and project removal."""

        layout.operator("quicksdf.bake_base", text=tr("Rebake Base"), icon="FILE_REFRESH")
        # Boundary authoring is hidden until its editing workflow is complete.
        # Keep stored tracks and generation behavior intact for existing projects.
        review = widgets.section(layout, tr("Review / Recovery"))
        review.operator("quicksdf.export_mask_sequence", text=tr("Export Review Masks"))
        review.operator(
            "quicksdf.restore_materials", text=tr("Restore Materials"), icon="LOOP_BACK"
        )

        adjusted = int(getattr(project, "export_adjustment_pixel_count", 0))
        adjustment_image = getattr(project, "export_adjustment_image", None)
        if adjusted and adjustment_image is not None:
            adjustments = layout.box()
            adjustments.label(
                text=tr("%s authored pixels needed angle adjustment.") % f"{adjusted:,}"
            )
            adjustments.label(
                text=tr("%s angle samples changed")
                % f"{int(getattr(project, 'export_adjustment_sample_count', 0)):,}"
            )
            protected = int(
                getattr(project, "export_adjustment_protected_pixel_count", 0)
            )
            if protected:
                adjustments.label(
                    text=tr("%s edited pixels required adjustment.") % f"{protected:,}",
                    icon="INFO",
                )
            adjustments.operator(
                "quicksdf.review_export_adjustments",
                text=tr("Review Export Adjustments"),
                icon="IMAGE_DATA",
            )

        if project.diagnostic_message:
            warning = layout.box()
            warning.alert = True
            for line in str(project.diagnostic_message).splitlines():
                warning.label(text=line, icon="ERROR")

        layout.separator()
        layout.operator(
            "quicksdf.project_remove", text=tr("Remove Quick SDF Project"), icon="TRASH"
        )

    def _draw_front(self, layout, context, project) -> None:
        """The one Front chooser.  ``project`` is None before creation."""

        front = layout.box()
        expanded = widgets.front_axis_section(
            front, context, ui_context.front_axis_id(context, project)
        )
        if expanded and project is not None:
            front.operator(
                "quicksdf.set_forward_from_view", text=tr("Use This View as Front")
            )


__all__ = ["QSDF_PT_launcher"]
