# SPDX-License-Identifier: GPL-3.0-or-later
"""User-level presentation settings, independent of paint projects."""

import bpy  # type: ignore[import-not-found]  # Provided by Blender.
from bpy.props import FloatProperty  # type: ignore[import-not-found]

ADDON_ID = __package__


def get_preferences(context):
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon is not None else None


def timeline_ui_scale(context):
    preferences = get_preferences(context)
    return float(getattr(preferences, "timeline_ui_scale", 1.0))


def _redraw_ui(_self, _context):
    from .ui import tag_editors_for_redraw

    tag_editors_for_redraw()


def draw_ui_settings(layout, context):
    preferences = get_preferences(context)
    if preferences is None:
        return
    layout.prop(preferences, "timeline_ui_scale", slider=True)
    if not context.preferences.use_preferences_save:
        layout.operator("wm.save_userpref", text="Save Preferences", icon="FILE_TICK")


class QSDF_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    timeline_ui_scale: FloatProperty(  # type: ignore  # Blender RNA declaration.
        name="Bottom UI Scale",
        description="Scale the bottom lighting controls, including text and click targets",
        default=1.0,
        min=0.5,
        max=2.0,
        step=5,
        precision=2,
        update=_redraw_ui,
    )

    def draw(self, context):
        draw_ui_settings(self.layout, context)


CLASSES = (QSDF_AddonPreferences,)
