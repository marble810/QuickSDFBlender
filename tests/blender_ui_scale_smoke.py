"""Check global UI preferences and scaled overlay geometry in isolated Blender.

Run once with -- --save-preferences, then without factory startup using
-- --verify-preferences and the same isolated BLENDER_USER_RESOURCES directory.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
addon_utils.enable("quick_sdf_blender", default_set=True)
assert addon_utils.check("quick_sdf_blender")[1]

from quick_sdf_blender import preferences, ui, viewport_lighting  # noqa: E402

context = bpy.context
prefs = preferences.get_preferences(context)
assert prefs is not None
if "--verify-preferences" in sys.argv:
    assert abs(prefs.timeline_ui_scale - 1.25) < 1.0e-6
else:
    assert prefs.timeline_ui_scale == 1.0
assert preferences.QSDF_AddonPreferences.bl_idname == "quick_sdf_blender"
assert ui.QSDF_PT_ui_settings.bl_category == "Quick SDF"
assert ui.QSDF_PT_ui_settings.bl_order > getattr(ui.QSDF_PT_launcher, "bl_order", 0)
assert "DEFAULT_CLOSED" in ui.QSDF_PT_ui_settings.bl_options
assert not getattr(ui.QSDF_PT_ui_settings, "bl_parent_id", "")

project = SimpleNamespace(
    angles=[SimpleNamespace(angle=value, side="RIGHT") for value in (0, 11.25, 45, 90)],
    symmetry_mode="MIRROR", active_side="RIGHT", authoring_side="RIGHT",
    active_angle_index=1,
)
region = SimpleNamespace(width=8000, height=4000, x=0)
fake_context = SimpleNamespace(
    # Background Blender has no display DPI; supply an explicit UI multiplier.
    preferences=SimpleNamespace(addons=context.preferences.addons,
                                system=SimpleNamespace(ui_scale=1.5)), region=region,
    area=SimpleNamespace(regions=[SimpleNamespace(type="UI", width=300, x=7700)]),
)
original_project = viewport_lighting._project
original_session = viewport_lighting.active_session
try:
    viewport_lighting._project = lambda _context: project
    viewport_lighting.active_session = lambda _context: None
    sizes = []
    for factor in (0.5, 1.0, 1.25, 2.0):
        prefs.timeline_ui_scale = factor
        layout = viewport_lighting._geometry(fake_context)
        assert layout is not None
        assert abs(layout.scale - 1.5 * fake_context.preferences.system.ui_scale * factor) < 1.0e-6
        assert layout.panel.x1 < 7700
        assert layout.panel.y1 < region.height
        for index, rect in layout.geometry.key_rects:
            center = (rect.x0 + rect.x1) / 2
            yaw = viewport_lighting.timeline._yaw_from_rail(layout.geometry.rail, center)
            angle = viewport_lighting.timeline._authoring_angle_for_yaw(yaw)
            assert angle is not None
            assert abs(angle - project.angles[index].angle) < 1.0e-6
            assert rect.contains(center, (rect.y0 + rect.y1) / 2)
        control = layout.controls[0][2]
        thumb = layout.geometry.key_rects[0][1]
        sizes.append((factor, layout.panel.y1 - layout.panel.y0,
                      thumb.x1 - thumb.x0, control.x1 - control.x0))
    for first, second in zip(sizes, sizes[1:], strict=False):
        ratio = second[0] / first[0]
        for before, after in zip(first[1:], second[1:], strict=True):
            assert abs(after - before * ratio) < 1.0e-6
finally:
    viewport_lighting._project = original_project
    viewport_lighting.active_session = original_session
    prefs.timeline_ui_scale = 1.0

# Missing preference entries preserve the old size (e.g. direct register tests).
assert preferences.timeline_ui_scale(SimpleNamespace(
    preferences=SimpleNamespace(addons={})
)) == 1.0

# Both the sidebar and addon settings use the same preference drawing helper.
for auto_save in (True, False):
    calls = []
    layout = SimpleNamespace(
        prop=lambda owner, name, _calls=calls, **_kwargs: _calls.append((owner, name)),
        operator=lambda name, _calls=calls, **_kwargs: _calls.append(name),
    )
    settings_context = SimpleNamespace(preferences=SimpleNamespace(
        addons=context.preferences.addons, use_preferences_save=auto_save,
    ))
    preferences.draw_ui_settings(layout, settings_context)
    assert calls[0] == (prefs, "timeline_ui_scale")
    assert ("wm.save_userpref" in calls) == (not auto_save)

# Re-registering the extension must retain the user value and cleanly re-add UI.
prefs.timeline_ui_scale = 1.25
addon_utils.disable("quick_sdf_blender", default_set=False)
addon_utils.enable("quick_sdf_blender", default_set=False)
prefs = preferences.get_preferences(context)
assert prefs is not None
assert abs(prefs.timeline_ui_scale - 1.25) < 1.0e-6
assert ui.QSDF_PT_ui_settings.is_registered

if "--save-preferences" in sys.argv:
    prefs.timeline_ui_scale = 1.25
    assert bpy.ops.wm.save_userpref() == {"FINISHED"}

print("[Quick SDF UI scale smoke] PASS: global preferences, geometry and hit targets")
