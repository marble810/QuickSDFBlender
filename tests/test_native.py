import ctypes
import threading
import time
import unittest

import numpy as np

from quick_sdf_blender import native
from quick_sdf_blender.bake import bake_face_shadow_guide as reference_guide_bake
from quick_sdf_blender.core import (
    PackedLane,
    generate_threshold_transitions as reference_threshold,
    interpolate_binary_masks as reference_interpolation,
    pack_lane_bits,
    repair_packed_lane as reference_repair,
)


@unittest.skipUnless(native.available(), "Windows native core was not built")
class NativeCoreTests(unittest.TestCase):
    def test_version_eight_exports_only_current_api(self) -> None:
        self.assertEqual(native.version(), native.NATIVE_ABI)
        self.assertEqual(native.NATIVE_ABI, 8)
        self.assertTrue(native.native_guide_bake_available())
        self.assertTrue(native.native_interpolation_available())
        self.assertTrue(native.native_packed_lane_available())
        dll = native._load()
        for symbol in (
            "qsdf_version",
            "qsdf_interpolate_binary_masks",
            "qsdf_bake_face_shadow_guide",
            "qsdf_repair_lane_bits",
            "qsdf_generate_threshold_transitions",
        ):
            self.assertTrue(hasattr(dll, symbol), symbol)

    def test_packed_repair_and_transition_threshold_are_byte_exact(self) -> None:
        rng = np.random.default_rng(7019)
        for count in (2, 3, 8, 16):
            angles = np.linspace(0.0, 90.0, count)
            transitions = rng.integers(
                0, count + 1, size=(31, 27), dtype=np.uint8
            )
            masks = np.arange(count)[:, None, None] >= transitions[None, ...]
            base = rng.random(masks.shape) > 0.5
            coverage = rng.random(masks.shape) > 0.83
            lane = pack_lane_bits(masks, angles, base, coverage)
            expected_repair = reference_repair(lane)
            actual_repair = native.repair_packed_lane(lane)
            np.testing.assert_array_equal(
                actual_repair.transition_indices,
                expected_repair.transition_indices,
            )
            np.testing.assert_array_equal(
                actual_repair.changed_count, expected_repair.changed_count
            )
            self.assertEqual(
                actual_repair.changed_sample_count,
                expected_repair.changed_sample_count,
            )
            self.assertEqual(
                actual_repair.protected_changed_pixel_count,
                expected_repair.protected_changed_pixel_count,
            )

            expected = reference_threshold(transitions, angles)
            output = np.full((31, 27, 4), 123, dtype=np.uint16)
            progress = ctypes.c_int(0)
            result = native.generate_threshold_transitions(
                transitions,
                angles,
                out=output,
                channel=2,
                progress=progress,
            )
            self.assertIs(result, output)
            self.assertEqual(progress.value, count)
            np.testing.assert_array_equal(output[..., 2], expected)
            self.assertTrue(np.all(output[..., (0, 1, 3)] == 123))

    def test_packed_api_validates_types_shapes_and_cancel(self) -> None:
        angles = np.asarray([0.0, 45.0, 90.0])
        plane = np.zeros((3, 4), dtype=np.uint16)
        lane = PackedLane(angles, plane, plane.copy(), plane.copy())
        cancelled = ctypes.c_int(1)
        with self.assertRaisesRegex(native.NativeCoreError, "cancelled"):
            native.repair_packed_lane(lane, cancel_flag=cancelled)
        with self.assertRaisesRegex(native.NativeCoreError, "cancelled"):
            native.generate_threshold_transitions(
                np.zeros((3, 4), dtype=np.uint8),
                angles,
                cancel_flag=cancelled,
            )
        with self.assertRaisesRegex(TypeError, "uint16"):
            native.repair_packed_lane(
                PackedLane(angles, plane.astype(np.uint8), plane, plane)
            )
        with self.assertRaisesRegex(ValueError, "share a shape"):
            native.repair_packed_lane(
                PackedLane(angles, plane, plane[:, :2], plane)
            )

    def test_running_threshold_generation_cancels_cooperatively(self) -> None:
        size = 1024
        count = 16
        angles = np.linspace(0.0, 90.0, count)
        y, x = np.indices((size, size))
        transitions = ((3 * x + 5 * y) % (count + 1)).astype(np.uint8)
        cancel_flag = ctypes.c_int(0)
        outcome: list[BaseException] = []

        def generate() -> None:
            try:
                native.generate_threshold_transitions(
                    transitions, angles, cancel_flag=cancel_flag
                )
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=generate)
        started = time.perf_counter()
        worker.start()
        time.sleep(0.02)
        cancel_flag.value = 1
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(len(outcome), 1)
        self.assertRegex(str(outcome[0]), "cancelled")

    def test_native_binary_interpolation_matches_python_byte_exact(self) -> None:
        rng = np.random.default_rng(6102)
        first = rng.random((37, 29)) > 0.52
        second = rng.random((37, 29)) > 0.48
        for factor in (0.0, 0.01, 0.2, 0.5, 0.731, 0.99, 1.0):
            expected = reference_interpolation(first, second, factor)
            actual = native.interpolate_binary_masks(first, second, factor)
            self.assertEqual(actual.dtype, np.bool_)
            self.assertTrue(actual.flags.c_contiguous)
            np.testing.assert_array_equal(actual, expected)

    def test_native_binary_interpolation_normalizes_and_cancels(self) -> None:
        shadow = np.zeros((5, 7), dtype=np.uint16)[:, ::-1]
        light = np.full((5, 7), 255, dtype=np.uint8, order="F")
        for factor in (0.49, 0.5, 0.51):
            np.testing.assert_array_equal(
                native.interpolate_binary_masks(shadow, light, factor),
                reference_interpolation(shadow, light, factor),
            )
        with self.assertRaisesRegex(native.NativeCoreError, "cancelled"):
            native.interpolate_binary_masks(
                shadow, light, 0.5, cancel_flag=ctypes.c_int(1)
            )

    def test_running_binary_interpolation_cancels_cooperatively(self) -> None:
        size = 1024
        y, x = np.indices((size, size))
        first = (x + y) % 17 < 8
        second = (3 * x + y) % 19 < 9
        cancel_flag = ctypes.c_int(0)
        outcome: list[BaseException] = []

        def interpolate() -> None:
            try:
                native.interpolate_binary_masks(
                    first, second, 0.413, cancel_flag=cancel_flag
                )
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=interpolate)
        started = time.perf_counter()
        worker.start()
        time.sleep(0.01)
        cancel_flag.value = 1
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(len(outcome), 1)
        self.assertRegex(str(outcome[0]), "cancelled")

    def test_native_face_shadow_guide_matches_reference(self) -> None:
        uvs = np.asarray(
            [
                [[0.05, 0.05], [0.95, 0.10], [0.15, 0.95]],
                [[0.95, 0.10], [0.90, 0.95], [0.15, 0.95]],
            ],
            dtype=np.float32,
        )
        normals = np.asarray(
            [
                [[0.8, -1.0, 0.0], [0.2, -1.0, 0.4], [-0.8, -1.0, 0.0]],
                [[0.2, -1.0, 0.4], [-0.8, -1.0, 0.0], [0.0, -1.0, -0.5]],
            ],
            dtype=np.float32,
        )
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        for side in ("RIGHT", "LEFT"):
            expected_masks, expected_occupancy = reference_guide_bake(
                uvs,
                normals,
                angles,
                (0.3, -1.0, 0.2),
                (0.0, 0.0, 1.0),
                side,
                50.0,
                29,
                23,
            )
            masks, occupancy = native.bake_face_shadow_guide(
                uvs,
                normals,
                angles,
                (0.3, -1.0, 0.2),
                (0.0, 0.0, 1.0),
                side,
                50.0,
                29,
                23,
            )
            np.testing.assert_array_equal(occupancy, expected_occupancy)
            np.testing.assert_array_equal(masks, expected_masks)
            self.assertTrue(np.all(masks[-1, occupancy]))


if __name__ == "__main__":
    unittest.main()
