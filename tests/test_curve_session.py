import unittest

import numpy as np

from quick_sdf_blender.curve_fill import (
    compose_generated_fill,
    cubic_bezier_3d,
    sample_closed_bezier_3d,
)


class CurveSessionTests(unittest.TestCase):
    def test_closed_bezier_samples_without_duplicating_the_first_knot(self):
        knots = (
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
            ((1.0, 1.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
            ((0.0, 1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)),
        )
        sampled = sample_closed_bezier_3d(knots, samples_per_segment=4)
        self.assertGreaterEqual(len(sampled), 12)
        self.assertEqual(sampled[0], (0.0, 0.0, 0.0))
        self.assertNotEqual(sampled[-1], sampled[0])

    def test_cubic_bezier_3d_midpoint_of_a_straight_segment(self):
        point = cubic_bezier_3d(
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            0.5,
        )
        self.assertAlmostEqual(point[0], 1.0)
        self.assertAlmostEqual(point[1], 0.0)
        self.assertAlmostEqual(point[2], 0.0)

    def test_generated_fill_preserves_coverage_pixels(self):
        current = np.full((4, 4), 128, dtype=np.uint8)
        coverage = np.zeros((4, 4), dtype=np.bool_)
        coverage[1, 1] = True
        region = np.zeros((4, 4), dtype=np.bool_)
        region[1:3, 1:3] = True
        filled = compose_generated_fill(current, coverage, region, value=0)
        self.assertEqual(int(filled[1, 1]), 128)
        self.assertEqual(int(filled[2, 2]), 0)
        self.assertEqual(int(filled[0, 0]), 128)
        light = compose_generated_fill(current, coverage, region, value=1)
        self.assertEqual(int(light[1, 1]), 128)
        self.assertEqual(int(light[2, 2]), 255)


if __name__ == "__main__":
    unittest.main()
