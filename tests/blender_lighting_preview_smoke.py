"""Run in an active Quick SDF Paint session; verifies scrub/release/exit together.

runpy.run_path(path)['run']('bl_ext.user_default.quick_sdf_blender')
"""
import importlib
from types import SimpleNamespace

import bpy
import numpy as np


def run(package):
    v = importlib.import_module(package + '.viewport_lighting')
    s = importlib.import_module(package + '.studio')
    live = importlib.import_module(package + '.live_preview')
    p = s.resolve_session_project()
    assert p is not None, 'Open Quick SDF Paint first'
    window = s.find_window(s.current_session().window_pointer)
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    initial_index = int(p.active_angle_index)
    initial_yaw = v.timeline._stored_preview_yaw(p)
    initial_seek = float(p.seek_angle)
    count = len(p.angles)
    checks = []
    with bpy.context.temp_override(window=window, area=area, region=region):
        ctx = bpy.context
        rail = v._geometry(ctx).geometry.rail
        for yaw in np.linspace(0, 180, 361):
            x = v.timeline._x_for_yaw(rail, yaw)
            assert abs(v.timeline._yaw_from_rail(rail, x) - yaw) < 1e-8
        for yaw, fraction in ((180, 0), (130, .01), (120, .05), (90, .5),
                              (60, .95), (50, .99), (0, 1)):
            x = v.timeline._x_for_yaw(rail, yaw)
            assert abs((x - rail.x0) / (rail.x1 - rail.x0) - fraction) < 1e-8
        for index, rect in v._geometry(ctx).geometry.key_rects:
            center = (rect.x0 + rect.x1) / 2
            assert abs(center - v.timeline._x_for_yaw(
                rail, v.timeline._physical_light_yaw(p.angles[index].angle))) < 1e-8
            assert v._geometry(ctx).panel.x0 <= rect.x0 < rect.x1 <= v._geometry(ctx).panel.x1
        for yaw in (180, 150, 135, 30, 15, 0):
            capture = SimpleNamespace(_dragging=False)
            capture._restore_initial_key = lambda c, project: s.select_paint_key(c, project, index=initial_index)
            capture.exit = lambda c, cancel, captured=capture: v.QSDF_GT_viewport_lighting.exit(captured, c, cancel)
            x = v.timeline._x_for_yaw(rail, yaw)
            event = SimpleNamespace(mouse_region_x=x, mouse_region_y=(rail.y0 + rail.y1) / 2)
            assert v.QSDF_GT_viewport_lighting.invoke(capture, ctx, event) == {'RUNNING_MODAL'}
            image = live.update_endpoint_preview(p, yaw)
            before = np.asarray(image.pixels[:])
            # Both Blender event orderings: release delivered, or exit directly.
            if yaw in (180, 135, 15):
                event.type, event.value = 'LEFTMOUSE', 'RELEASE'
                assert v.QSDF_GT_viewport_lighting.modal(capture, ctx, event, None) == {'FINISHED'}
            capture.exit(ctx, False)
            assert abs(v.timeline._stored_preview_yaw(p) - yaw) < 1e-8
            assert s.current_session().view_mode == 'PREVIEW'
            assert not s.current_session().provisional_uuid
            assert len(p.angles) == count
            assert int(p.active_angle_index) == initial_index
            assert not v._geometry(ctx).controls[0][3], 'Preview must disable +'
            np.testing.assert_array_equal(np.asarray(image.pixels[:]), before)
            # A late authoring refresh must preserve the red-zone pixels too.
            live.update_seek_preview(p, float(p.seek_angle))
            np.testing.assert_array_equal(np.asarray(image.pixels[:]), before)
            if yaw in (180, 0):
                assert np.all(before.reshape(-1, 4)[:, :3] == (0 if yaw == 180 else 1))
            checks.append(yaw)
        s.select_paint_key(ctx, p, index=initial_index)
        assert v.timeline._stored_preview_yaw(p) is None
        assert v._geometry(ctx).controls[0][3] == (count < v.MAX_KEYS_PER_SIDE)
        if initial_yaw is not None:
            v._scrub_from_x(ctx, p, rail, v.timeline._x_for_yaw(rail, initial_yaw), settle=True)
        elif abs(initial_seek - float(p.seek_angle)) > 1e-6:
            s.seek_preview(ctx, p, initial_seek)
    return {'passed': True, 'release_and_exit_yaws': checks, 'keys_unchanged': count}
