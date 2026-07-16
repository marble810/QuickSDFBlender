"""Focused Blender smoke test for artist-facing timeline semantics."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quick_sdf_blender import timeline  # noqa: E402


def main() -> None:
    default_angles = tuple(index * 90.0 / 8.0 for index in range(9))
    expected_labels = (
        "0%",
        "12.5%",
        "25%",
        "37.5%",
        "50%",
        "62.5%",
        "75%",
        "87.5%",
        "100%",
    )
    assert tuple(timeline._format_progress(value) for value in default_angles) == (
        expected_labels
    )
    assert math.isclose(timeline._physical_light_yaw(0.0), 120.0)
    assert math.isclose(timeline._physical_light_yaw(45.0), 90.0)
    assert math.isclose(timeline._physical_light_yaw(90.0), 60.0)

    keys = tuple(
        (index, SimpleNamespace(angle=angle))
        for index, angle in enumerate(default_angles)
    )
    geometry = timeline.build_geometry(960.0, 104.0, keys)
    center_rect = dict(geometry.key_rects)[4]
    center_x = (center_rect.x0 + center_rect.x1) * 0.5
    playhead_x, _label_rect, label = timeline._playhead_layout(
        geometry, 960.0, 104.0, 45.0
    )
    rail_mid = (geometry.rail.x0 + geometry.rail.x1) * 0.5
    assert math.isclose(center_x, rail_mid, abs_tol=1.0e-6)
    assert math.isclose(
        timeline._x_for_angle(geometry, 45.0), rail_mid, abs_tol=1.0e-6
    )
    assert math.isclose(playhead_x, rail_mid, abs_tol=1.0e-6)
    assert math.isclose(geometry.angle_from_x(rail_mid), 45.0, abs_tol=1.0e-6)
    assert label == "50%"

    captured: list[str | None] = []
    context = SimpleNamespace(
        workspace=SimpleNamespace(status_text_set=captured.append)
    )
    timeline._set_scrub_status(context, 45.0)
    timeline._clear_scrub_status(context)
    assert "50%" in str(captured[0])
    assert "90°" in str(captured[0])
    assert captured[-1] is None

    print("Quick SDF timeline semantics: PASS")


if __name__ == "__main__":
    main()
