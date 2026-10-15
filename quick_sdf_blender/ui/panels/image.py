# SPDX-License-Identifier: GPL-3.0-or-later
"""Image Editor sidebar section for the open Quick SDF Paint project."""

from __future__ import annotations

from ...i18n import tr
from .. import context as ui_context
from .. import widgets
from ..base import ImageEditorPanel


class QSDF_PT_image_studio(ImageEditorPanel):
    bl_idname = "QSDF_PT_image_studio"
    bl_label = "Quick SDF Paint"

    @classmethod
    def poll(cls, context):
        return ui_context.is_studio_active(context, ui_context.sidebar_project(context))

    def draw(self, context):
        project = ui_context.sidebar_project(context)
        editing_aux = widgets.aux_edit_status(self.layout, context, project)
        if not editing_aux:
            widgets.paint_value_row(self.layout, project)
            self.layout.prop(project, "onion_enabled", text=tr("Onion"), toggle=True)
            widgets.uv_padding_row(self.layout, project)
        widgets.normals_toggle(self.layout, project)
        widgets.export_section(self.layout, project, compact=True)


__all__ = ["QSDF_PT_image_studio"]
