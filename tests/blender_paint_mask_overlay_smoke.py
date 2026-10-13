"""Regression for Blender's alpha-blind Texture Paint face-mask overlay.

The real Studio projection/preview path must hide native overlays without
turning off face isolation, and restore per-viewport settings on save/exit.
Run with Blender --background --factory-startup --python-exit-code 1.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quick_sdf_blender import studio  # noqa: E402


def view(pointer, overlays):
    space = SimpleNamespace(clip_start=0.1, clip_end=1000.0, overlay=SimpleNamespace(show_overlays=overlays),
                            shading=SimpleNamespace(type="MATERIAL"), as_pointer=lambda: pointer)
    return SimpleNamespace(type="VIEW_3D", spaces=SimpleNamespace(active=space))


class Workspace(dict):
    name = "Overlay Smoke Workspace"


areas = [view(1, True), view(2, False)]
window = SimpleNamespace(screen=SimpleNamespace(areas=areas, name="Overlay Smoke Screen"),
                         workspace=Workspace())
scene = SimpleNamespace(name="Overlay Smoke", tool_settings=SimpleNamespace(
    image_paint=SimpleNamespace(use_normal_falloff=True),
))
obj = SimpleNamespace(dimensions=(1, 1, 1), data=SimpleNamespace(use_paint_mask=True),
                      mode="TEXTURE_PAINT")
session = studio.StudioSession(
    project_uuid="overlay-smoke", window_pointer=1, scene_name=scene.name,
    workspace_name=window.workspace.name, previous_workspace_name="", previous_active_object_name="",
    previous_selected_object_names=(), previous_mode="OBJECT",
    previous_image_paint_mode="MATERIAL", previous_canvas_name="",
)

studio._apply_projection_settings(scene, session, window, obj)
assert all(not area.spaces.active.overlay.show_overlays for area in areas), (
    "Native face-mask overlays still expose transparent internal components"
)
assert obj.data.use_paint_mask, "Do not disable face isolation to hide the artifact"
record = json.loads(window.workspace[studio.PROJECTION_RECOVERY_KEY])
assert record["view_overlays"] == [True, False]
expected_start, expected_end = studio._studio_clip_range(obj)
assert expected_end / expected_start <= studio._CLIP_RATIO + 1.0e-6
assert all(space.spaces.active.clip_start == expected_start for space in areas)
assert all(space.spaces.active.clip_end == expected_end for space in areas)
assert record["clip_starts"] == [0.1, 0.1]
assert record["clip_ends"] == [1000.0, 1000.0]

# Recovery before a paint stroke must re-hide overlays without overwriting the
# original values, including additional views created during the session.
areas.append(view(3, True))
areas[0].spaces.active.overlay.show_overlays = True
find_window = studio.find_window
try:
    studio.find_window = lambda _pointer: window
    assert studio.ensure_studio_material_preview(session=session)
finally:
    studio.find_window = find_window
assert all(not area.spaces.active.overlay.show_overlays for area in areas)
studio._apply_projection_settings(scene, session, window, obj)
studio._restore_projection_settings(scene, session, window)
assert [area.spaces.active.overlay.show_overlays for area in areas] == [True, False, True]
assert [(area.spaces.active.clip_start, area.spaces.active.clip_end) for area in areas[:2]] == [
    (0.1, 1000.0),
    (0.1, 1000.0),
]
assert studio.PROJECTION_RECOVERY_KEY not in window.workspace

# Save/resume uses the same restoration path and must not recapture the hidden
# state as the user's preference.
studio._apply_projection_settings(scene, session, window, obj)
originals = (studio._SESSION, studio.find_window, studio.resolve_session_project)
try:
    studio._SESSION = session
    studio.find_window = lambda _pointer: window
    studio.resolve_session_project = lambda _session: SimpleNamespace(target_object=obj)
    obj.mode = "EDIT"
    studio._sync_native_studio_overlays(scene, None)
    assert [area.spaces.active.overlay.show_overlays for area in areas] == [True, False, True]
    obj.mode = "TEXTURE_PAINT"
    studio._sync_native_studio_overlays(scene, None)
    assert all(not area.spaces.active.overlay.show_overlays for area in areas)
finally:
    studio._SESSION, studio.find_window, studio.resolve_session_project = originals
studio._restore_projection_settings(scene, session, window)
assert [area.spaces.active.overlay.show_overlays for area in areas] == [True, False, True]
assert obj.data.use_paint_mask

# Autosave recovery restores native display state; older journals without the
# new field must leave existing overlay preferences alone.
studio._apply_projection_settings(scene, session, window, obj)
record = json.loads(window.workspace[studio.PROJECTION_RECOVERY_KEY])
record["paint_surface"] = None
window.workspace[studio.PROJECTION_RECOVERY_KEY] = json.dumps(record)
original_bpy = studio.bpy
try:
    studio.bpy = SimpleNamespace(data=SimpleNamespace(
        workspaces=[window.workspace], scenes={scene.name: scene},
        screens={window.screen.name: window.screen},
    ))
    studio._restore_serialized_projection_settings()
    assert [area.spaces.active.overlay.show_overlays for area in areas] == [True, False, True]
    assert [(area.spaces.active.clip_start, area.spaces.active.clip_end) for area in areas[:2]] == [
        (0.1, 1000.0),
        (0.1, 1000.0),
    ]
    assert studio.PROJECTION_RECOVERY_KEY not in window.workspace
    del record["view_overlays"]
    window.workspace[studio.PROJECTION_RECOVERY_KEY] = json.dumps(record)
    studio._restore_serialized_projection_settings()
    assert [area.spaces.active.overlay.show_overlays for area in areas] == [True, False, True]
finally:
    studio.bpy = original_bpy

studio.register_studio()
assert studio._sync_native_studio_overlays in studio.bpy.app.handlers.depsgraph_update_post
studio.unregister_studio()
assert studio._sync_native_studio_overlays not in studio.bpy.app.handlers.depsgraph_update_post
print("[Quick SDF paint mask overlay smoke] PASS: isolation, mode switching, restore and autosave recovery")
