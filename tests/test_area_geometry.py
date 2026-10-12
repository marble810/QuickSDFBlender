from __future__ import annotations

import unittest
from types import SimpleNamespace

from quick_sdf_blender.area_geometry import join_candidates, shared_edge


def rect(type_name, x, y, width, height, pointer):
    """Stand-in for a Blender ``ScreenArea``; only the geometry is used."""

    return SimpleNamespace(
        type=type_name,
        x=x,
        y=y,
        width=width,
        height=height,
        as_pointer=lambda: pointer,
    )


def areas(gap):
    """The reported Layout arrangement with a divider of ``gap`` px.

    Geometry is copied from a macOS session that exposed the regression: the
    four areas of the copied Layout screen, whose touching edges are separated
    by 7px there. Closing those two offsets reproduces the 4px divider a 1x
    Windows display leaves for the same layout.
    """

    shrink = 7 - gap
    return [
        rect("PROPERTIES", 2447, 47, 524, 1982, 1),
        rect("NODE_EDITOR", 4, 47, 2436, 638, 2),
        rect("VIEW_3D", 4, 692 - shrink, 2436, 1337, 3),
        rect("OUTLINER", 2978 - shrink, 47, 438, 1982, 4),
    ]


def offered(rects):
    return [(source.type, target.type) for source, target in join_candidates(rects)]


class SharedEdgeTests(unittest.TestCase):
    def test_accepts_the_wider_high_dpi_divider(self) -> None:
        properties, node_editor, view_3d, outliner = areas(7)

        self.assertEqual(shared_edge(node_editor, view_3d), (0, 7))
        self.assertEqual(shared_edge(properties, outliner), (0, 7))

    def test_accepts_the_narrow_windows_divider(self) -> None:
        properties, node_editor, view_3d, outliner = areas(4)

        self.assertEqual(shared_edge(node_editor, view_3d), (0, 4))
        self.assertEqual(shared_edge(properties, outliner), (0, 4))

    def test_rejects_areas_that_only_sit_near_each_other(self) -> None:
        properties, node_editor, _view_3d, _outliner = areas(7)

        # Side by side but of different heights, so they share no edge.
        self.assertIsNone(shared_edge(node_editor, properties))

    def test_rejects_a_gap_as_wide_as_the_areas_themselves(self) -> None:
        first = rect("VIEW_3D", 4, 47, 2436, 1982, 1)
        second = rect("OUTLINER", 4000, 47, 438, 1982, 2)

        # In line and the same height, but far too far apart to touch: that
        # distance is another area, not a divider.
        self.assertIsNone(shared_edge(first, second))


class JoinCandidateTests(unittest.TestCase):
    def test_offers_both_touching_pairs_of_the_reported_layout(self) -> None:
        self.assertEqual(
            sorted(offered(areas(7))),
            [("NODE_EDITOR", "VIEW_3D"), ("PROPERTIES", "OUTLINER")],
        )

    def test_offers_the_same_pairs_for_the_windows_divider(self) -> None:
        self.assertEqual(offered(areas(7)), offered(areas(4)))

    def test_ranks_the_cleanest_edge_first(self) -> None:
        clean = rect("VIEW_3D", 0, 0, 100, 100, 1)
        tidy = rect("NODE_EDITOR", 0, 100, 100, 100, 2)
        crooked = rect("OUTLINER", 0, 200, 104, 100, 3)

        self.assertEqual(
            offered([crooked, tidy, clean]),
            [("VIEW_3D", "NODE_EDITOR"), ("NODE_EDITOR", "OUTLINER")],
        )

    def test_keeps_the_sequence_stable_for_a_screen(self) -> None:
        rects = areas(7)

        self.assertEqual(offered(rects), offered(list(reversed(rects))))

    def test_offers_nothing_for_a_disjoint_screen(self) -> None:
        rects = [
            rect("VIEW_3D", 0, 0, 100, 100, 1),
            rect("IMAGE_EDITOR", 4000, 3000, 100, 100, 2),
        ]

        self.assertEqual(join_candidates(rects), [])

    def test_single_area_offers_nothing(self) -> None:
        self.assertEqual(join_candidates([rect("VIEW_3D", 0, 0, 100, 100, 1)]), [])


if __name__ == "__main__":
    unittest.main()
