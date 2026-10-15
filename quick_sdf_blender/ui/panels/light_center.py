# SPDX-License-Identifier: GPL-3.0-or-later
"""Light Indicator Center sidebar section.

The panel lives with the other sidebar sections, while the indicator overlay
itself stays in :mod:`..lighting_indicator`.
"""

from __future__ import annotations

from ...i18n import tr
from ..base import ProjectSidebarPanel


class QSDF_PT_light_center(ProjectSidebarPanel):
    bl_idname = "QSDF_PT_light_center"
    bl_label = "Light Indicator Center"
    bl_order = 50

    def draw(self, context):
        self.layout.label(text=tr("Edit Mode: select face polygons in 3D"))
        self.layout.operator("quicksdf.light_center_from_faces")
        self.layout.operator("quicksdf.light_center_reset")


__all__ = ["QSDF_PT_light_center"]
