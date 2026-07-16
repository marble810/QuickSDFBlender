from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.symmetry import (
    IslandPair,
    SymmetryMode,
    analyze_symmetry,
    mirror_side_layer,
)


class AutoAnalysisTests(unittest.TestCase):
    def test_detects_overlapped_layout_with_high_confidence(self) -> None:
        positive = np.zeros((4, 8), dtype=bool)
        positive[1:4, 0:3] = True
        analysis = analyze_symmetry(positive, positive.copy())
        self.assertEqual(analysis.suggested_mode, SymmetryMode.OVERLAPPED)
        self.assertEqual(analysis.overlap_score, 1.0)
        self.assertEqual(analysis.mirror_score, 0.0)
        self.assertEqual(analysis.confidence, 1.0)
        self.assertFalse(analysis.requires_confirmation)

    def test_detects_texture_mirror(self) -> None:
        positive = np.zeros((3, 7), dtype=np.uint8)
        positive[:, 0:2] = 255
        analysis = analyze_symmetry(positive, positive[:, ::-1])
        self.assertEqual(analysis.suggested_mode, SymmetryMode.TEXTURE_MIRROR)
        self.assertEqual(analysis.mirror_score, 1.0)
        self.assertEqual(analysis.overlap_score, 0.0)

    def test_ambiguous_and_unmatched_layouts(self) -> None:
        symmetric = np.zeros((3, 7), dtype=bool)
        symmetric[:, 1:6] = True
        ambiguous = analyze_symmetry(symmetric, symmetric)
        self.assertEqual(ambiguous.confidence, 0.0)
        self.assertTrue(ambiguous.requires_confirmation)

        positive = np.zeros((4, 8), bool)
        negative = np.zeros((4, 8), bool)
        positive[0, 0] = True
        negative[3, 3] = True
        unmatched = analyze_symmetry(positive, negative)
        self.assertEqual(unmatched.suggested_mode, SymmetryMode.INDEPENDENT)

    def test_invalid_analysis_shape_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same shape"):
            analyze_symmetry(np.zeros((2, 2)), np.zeros((2, 3)))


class SideMirrorTests(unittest.TestCase):
    def test_texture_mirror_and_overlapped(self) -> None:
        source = np.arange(24, dtype=np.uint8).reshape(3, 4, 2)
        np.testing.assert_array_equal(
            mirror_side_layer(source, "TEXTURE_MIRROR"), source[:, ::-1]
        )
        np.testing.assert_array_equal(
            mirror_side_layer(source, "OVERLAPPED_UV"), source
        )

    def test_independent_returns_copy_or_template(self) -> None:
        source = np.arange(12, dtype=np.uint8).reshape(3, 4)
        copied = mirror_side_layer(source, "INDEPENDENT")
        np.testing.assert_array_equal(copied, source)
        self.assertFalse(np.shares_memory(copied, source))
        template = np.full_like(source, 9)
        np.testing.assert_array_equal(
            mirror_side_layer(source, "INDEPENDENT", target_template=template),
            template,
        )

    def test_island_pair_resamples_and_preserves_outside_target(self) -> None:
        source = np.arange(18, dtype=np.int16).reshape(3, 6)
        source_mask = np.zeros((3, 6), bool)
        target_mask = np.zeros((3, 6), bool)
        source_mask[:, 0:2] = True
        target_mask[:, 4:6] = True
        template = np.full((3, 6), -1, dtype=np.int16)
        result = mirror_side_layer(
            source,
            "ISLAND_PAIR",
            [IslandPair(source_mask, target_mask)],
            template,
        )
        np.testing.assert_array_equal(result[:, 4:6], source[:, 0:2][:, ::-1])
        self.assertTrue(np.all(result[:, :4] == -1))

    def test_multiple_pairs_and_mapping_form(self) -> None:
        source = np.zeros((2, 6), dtype=np.uint8)
        source[0, 0:2] = [1, 2]
        source[1, 2:4] = [3, 4]
        first_source = np.zeros((2, 6), bool)
        first_target = np.zeros((2, 6), bool)
        second_source = np.zeros((2, 6), bool)
        second_target = np.zeros((2, 6), bool)
        first_source[0, 0:2] = True
        first_target[0, 4:6] = True
        second_source[1, 2:4] = True
        second_target[1, 0:2] = True
        result = mirror_side_layer(
            source,
            "ISLAND_PAIR",
            [
                IslandPair(first_source, first_target),
                {"source_mask": second_source, "target_mask": second_target},
            ],
        )
        np.testing.assert_array_equal(result[0, 4:6], [2, 1])
        np.testing.assert_array_equal(result[1, 0:2], [4, 3])

    def test_invalid_modes_and_pairs_are_rejected(self) -> None:
        source = np.ones((2, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "occupancy"):
            mirror_side_layer(source, "AUTO")
        with self.assertRaisesRegex(ValueError, "unknown symmetry mode"):
            mirror_side_layer(source, "radial")
        with self.assertRaisesRegex(ValueError, "requires island_pairs"):
            mirror_side_layer(source, "ISLAND_PAIR")


if __name__ == "__main__":
    unittest.main()
