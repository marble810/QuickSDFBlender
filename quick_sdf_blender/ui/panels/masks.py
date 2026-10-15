# SPDX-License-Identifier: GPL-3.0-or-later
"""Additional Masks sidebar section: shared masks for every light angle."""

from __future__ import annotations

import bpy  # type: ignore[import-not-found]  # Provided by Blender.

from ...i18n import tr
from .. import context as ui_context
from .. import widgets
from ..base import ProjectSidebarPanel


class QSDF_UL_aux_masks(bpy.types.UIList):
    """Compact project-local list used by mask editing and packing assignment."""

    bl_idname = "QSDF_UL_aux_masks"

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _filter_flag=0,
    ):
        if self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="IMAGE_DATA")
            return
        role = str(item.role)
        if role == "CUSTOM":
            layout.prop(item, "name", text="", emboss=False, icon="IMAGE_DATA")
        else:
            layout.label(
                text=str(item.name) or tr(role.replace("_", " ").title()),
                icon="IMAGE_DATA",
            )


class QSDF_PT_additional_masks(ProjectSidebarPanel):
    bl_idname = "QSDF_PT_additional_masks"
    bl_label = "Additional Masks"
    bl_order = 30

    def draw(self, context):
        layout = self.layout
        project = ui_context.sidebar_project(context)
        if project is None:
            return

        editing_uuid = ui_context.editing_aux_mask_uuid(context, project)
        if editing_uuid:
            widgets.aux_edit_status(layout, context, project)
        else:
            layout.label(text=tr("These masks are shared by every angle."), icon="INFO")

        masks = list(project.aux_masks)
        if not masks:
            layout.label(text=tr("No additional masks."), icon="INFO")
            layout.operator("quicksdf.aux_mask_add", text=tr("Add Custom Mask"), icon="ADD")
            return

        list_row = layout.row()
        list_row.template_list(
            "QSDF_UL_aux_masks",
            "project_masks",
            project,
            "aux_masks",
            project,
            "active_aux_mask_index",
            rows=min(5, max(3, len(masks))),
        )
        self._draw_list_buttons(list_row.column(align=True), project)
        mask = ui_context.active_aux_mask(project)
        if mask is not None:
            self._draw_mask_actions(layout, mask, editing_uuid)

    @staticmethod
    def _draw_list_buttons(column, project) -> None:
        column.operator("quicksdf.aux_mask_add", text="", icon="ADD")
        active = ui_context.active_aux_mask(project)
        delete_row = column.row(align=True)
        delete_row.enabled = bool(
            active is not None and str(getattr(active, "role", "")) == "CUSTOM"
        )
        remove = delete_row.operator("quicksdf.aux_mask_delete", text="", icon="REMOVE")
        if active is not None:
            remove.mask_uuid = str(active.uuid)

    @staticmethod
    def _draw_mask_actions(layout, mask, editing_uuid: str) -> None:
        mask_uuid = str(getattr(mask, "uuid", ""))
        role = str(getattr(mask, "role", "CUSTOM")).upper()
        box = layout.box()
        name = str(getattr(mask, "name", "")) or tr("Additional Mask")
        box.label(
            text=name,
            icon="BRUSH_DATA" if editing_uuid == mask_uuid else "IMAGE_DATA",
        )
        image = getattr(mask, "image", None)
        image_name = str(getattr(image, "name", "")) or str(
            getattr(mask, "image_name", "")
        )
        if image_name:
            image_row = box.row()
            image_row.enabled = False
            image_row.label(text=tr("Image: %s") % image_name, icon="IMAGE")

        edit = box.operator("quicksdf.aux_mask_edit", text=tr("Edit Mask"), icon="BRUSH_DATA")
        edit.mask_uuid = mask_uuid
        actions = box.row(align=True)
        import_op = actions.operator(
            "quicksdf.aux_mask_import", text=tr("Import from Image"), icon="IMPORT"
        )
        import_op.mask_uuid = mask_uuid
        white = actions.operator("quicksdf.aux_mask_fill", text=tr("Fill White"))
        white.mask_uuid = mask_uuid
        white.value = 1.0
        black = actions.operator("quicksdf.aux_mask_fill", text=tr("Fill Black"))
        black.mask_uuid = mask_uuid
        black.value = 0.0

        if role == "SDF_AREA":
            reset = box.operator(
                "quicksdf.aux_mask_reset_sdf_area",
                text=tr("Reset SDF Area from UV"),
                icon="UV",
            )
            reset.mask_uuid = mask_uuid


__all__ = ["QSDF_PT_additional_masks", "QSDF_UL_aux_masks"]
