from __future__ import annotations

import binascii
import ctypes
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np

from quick_sdf_blender.core import (
    ALWAYS_LIGHT,
    ALWAYS_SHADOW,
    exact_edt,
    exact_signed_edt,
    generate_threshold_transitions,
    interpolate_binary_masks,
    pack_lane_bits,
    repair_packed_lane,
    validate_side_monotonic,
)
from quick_sdf_blender.packing import PackingChannelSpec, PackingSource, pack_rgba16
from quick_sdf_blender.png16 import (
    PNG_SIGNATURE,
    commit_png_temporary,
    encode_png_rgba16,
    write_png_rgba16,
    write_png_rgba16_temporary,
)


ANGLES = np.arange(-90.0, 91.0, 15.0)
SIDE_ANGLES = np.arange(0.0, 91.0, 15.0)


def stack_from_transitions(transitions: np.ndarray, count: int) -> np.ndarray:
    return np.arange(count, dtype=np.uint8)[:, None, None] >= transitions[None, ...]


def threshold_pair_from_transitions(
    right: np.ndarray, left: np.ndarray, angles: np.ndarray
) -> np.ndarray:
    output = np.empty((*right.shape, 2), dtype=np.uint16)
    generate_threshold_transitions(right, angles, out=output, channel=0)
    generate_threshold_transitions(left, angles, out=output, channel=1)
    return output


class StackRepairView:
    def __init__(self, display: np.ndarray, compact) -> None:
        self.masks = stack_from_transitions(
            compact.transition_indices, display.shape[0]
        )
        self.changed_mask = np.ascontiguousarray(self.masks != display)
        self.transition_indices = compact.transition_indices
        self.changed_sample_count = compact.changed_sample_count
        self.changed_pixel_count = compact.changed_pixel_count
        self.protected_changed_sample_count = compact.protected_changed_sample_count
        self.protected_changed_pixel_count = compact.protected_changed_pixel_count


def repair_stack(
    display: np.ndarray, base: np.ndarray, coverage: np.ndarray
) -> StackRepairView:
    angles = np.linspace(0.0, 90.0, display.shape[0])
    lane = pack_lane_bits(display, angles, base, coverage)
    return StackRepairView(display, repair_packed_lane(lane))


def pack_golden_liltoon_channels(channels: np.ndarray) -> np.ndarray:
    return pack_rgba16(
        {
            PackingSource.RIGHT_THRESHOLD: channels[..., 0],
            PackingSource.LEFT_THRESHOLD: channels[..., 1],
        },
        (
            PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
            PackingChannelSpec(PackingSource.LEFT_THRESHOLD),
            PackingChannelSpec(PackingSource.CONSTANT, constant_value=0.0),
            PackingChannelSpec(PackingSource.CONSTANT, constant_value=1.0),
        ),
    )


def brute_distance(features: np.ndarray) -> np.ndarray:
    points = np.argwhere(features)
    if not points.size:
        return np.full(features.shape, np.inf)
    result = np.empty(features.shape, dtype=np.float64)
    for y, x in np.ndindex(features.shape):
        result[y, x] = np.sqrt(np.min(np.sum((points - (y, x)) ** 2, axis=1)))
    return result


def decode_png(data: bytes) -> tuple[dict[str, int], np.ndarray]:
    assert data.startswith(PNG_SIGNATURE)
    position = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(data):
        size = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + size]
        checksum = struct.unpack_from(">I", data, position + 8 + size)[0]
        assert checksum == (binascii.crc32(kind + payload) & 0xFFFFFFFF)
        chunks.append((kind, payload))
        position += size + 12
    header = next(payload for kind, payload in chunks if kind == b"IHDR")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    raw = zlib.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"))
    stride = width * 8 + 1
    rows = []
    previous = np.zeros(width * 8, dtype=np.uint8)
    for y in range(height):
        filter_type = raw[y * stride]
        row = np.frombuffer(
            raw[y * stride + 1 : (y + 1) * stride], dtype=np.uint8
        ).copy()
        if filter_type == 2:
            row += previous
        else:
            assert filter_type == 0
        rows.append(row.tobytes())
        previous = row
    pixels = np.frombuffer(b"".join(rows), dtype=">u2").reshape(height, width, 4)
    return {
        "width": width,
        "height": height,
        "depth": depth,
        "color": color,
        "compression": compression,
        "filter": filtering,
        "interlace": interlace,
    }, pixels.astype(np.uint16)


class MonotonicRepairTests(unittest.TestCase):
    def test_valid_stack_is_bit_identical_and_idempotent(self) -> None:
        rng = np.random.default_rng(303)
        transitions = rng.integers(0, len(SIDE_ANGLES) + 1, size=(9, 11))
        masks = np.arange(len(SIDE_ANGLES))[:, None, None] >= transitions[None, ...]
        base = masks.copy()
        coverage = rng.random(masks.shape) > 0.95
        result = repair_stack(masks, base, coverage)
        np.testing.assert_array_equal(result.masks, masks)
        self.assertEqual(result.changed_sample_count, 0)
        second = repair_stack(result.masks, base, coverage)
        np.testing.assert_array_equal(second.masks, result.masks)
        self.assertEqual(second.changed_sample_count, 0)

    def test_valid_display_wins_even_when_base_and_coverage_are_unrelated(self) -> None:
        rng = np.random.default_rng(3303)
        transitions = rng.integers(0, len(SIDE_ANGLES) + 1, size=(8, 10))
        masks = np.arange(len(SIDE_ANGLES))[:, None, None] >= transitions[None, ...]
        base = rng.random(masks.shape) > 0.5
        coverage = rng.random(masks.shape) > 0.5
        result = repair_stack(masks, base, coverage)
        np.testing.assert_array_equal(result.masks, masks)
        self.assertEqual(result.changed_sample_count, 0)

    def test_complete_tie_chooses_the_lower_transition_index(self) -> None:
        masks = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        result = repair_stack(masks, masks, np.zeros_like(masks))
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True])
        self.assertEqual(int(result.transition_indices[0, 0]), 0)
        self.assertEqual(result.transition_indices.dtype, np.uint8)

    def test_repair_enabled_export_matches_liltoon_png_golden(self) -> None:
        angles = np.asarray([0.0, 20.0, 55.0, 90.0], dtype=np.float64)
        right_transitions = np.asarray([[0, 1, 2, 4], [4, 3, 1, 0]])
        left_transitions = np.asarray([[4, 2, 3, 0], [1, 4, 0, 2]])
        right = np.arange(4)[:, None, None] >= right_transitions[None, ...]
        left = np.arange(4)[:, None, None] >= left_transitions[None, ...]
        coverage = np.indices(right.shape).sum(axis=0) % 3 == 0
        right_repair = repair_stack(right, ~right, coverage)
        left_repair = repair_stack(left, np.roll(left, 1, axis=0), ~coverage)
        repaired_right = right_repair.masks
        repaired_left = left_repair.masks
        channels = threshold_pair_from_transitions(
            right_repair.transition_indices,
            left_repair.transition_indices,
            angles,
        )
        rgba = pack_golden_liltoon_channels(channels)
        np.testing.assert_array_equal(repaired_right, right)
        np.testing.assert_array_equal(repaired_left, left)
        _, decoded = decode_png(encode_png_rgba16(rgba))
        np.testing.assert_array_equal(decoded, rgba)

    def test_coverage_protection_wins_before_total_and_base_cost(self) -> None:
        masks = np.asarray([True, False, True], dtype=np.bool_)[:, None, None]
        base = np.asarray([False, False, True], dtype=np.bool_)[:, None, None]
        coverage = np.zeros_like(masks)
        coverage[0, 0, 0] = True
        result = repair_stack(masks, base, coverage)
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True, True])
        self.assertEqual(result.protected_changed_sample_count, 0)
        self.assertEqual(result.changed_sample_count, 1)

    def test_display_difference_is_protected_without_coverage(self) -> None:
        masks = np.asarray([True, False, True], dtype=np.bool_)[:, None, None]
        base = np.asarray([False, False, True], dtype=np.bool_)[:, None, None]
        result = repair_stack(masks, base, np.zeros_like(masks))
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True, True])
        self.assertEqual(result.protected_changed_sample_count, 0)

    def test_base_breaks_equal_display_cost(self) -> None:
        masks = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        shadow_coverage = np.asarray([False, True], dtype=np.bool_)[:, None, None]
        light_coverage = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        result_light = repair_stack(
            masks, np.ones_like(masks), light_coverage
        )
        result_shadow = repair_stack(
            masks, np.zeros_like(masks), shadow_coverage
        )
        np.testing.assert_array_equal(result_light.masks[:, 0, 0], [True, True])
        np.testing.assert_array_equal(result_shadow.masks[:, 0, 0], [False, False])

    def test_random_invalid_stacks_are_repaired_without_mutating_inputs(self) -> None:
        rng = np.random.default_rng(9917)
        masks = rng.random((len(SIDE_ANGLES), 17, 13)) > 0.5
        base = rng.random(masks.shape) > 0.5
        coverage = rng.random(masks.shape) > 0.85
        originals = (masks.copy(), base.copy(), coverage.copy())
        result = repair_stack(masks, base, coverage)
        self.assertTrue(validate_side_monotonic(result.masks, SIDE_ANGLES).is_valid)
        np.testing.assert_array_equal(masks, originals[0])
        np.testing.assert_array_equal(base, originals[1])
        np.testing.assert_array_equal(coverage, originals[2])
        self.assertEqual(
            result.changed_pixel_count,
            int(np.count_nonzero(np.any(result.changed_mask, axis=0))),
        )

    def test_shape_validation(self) -> None:
        masks = np.zeros((3, 2, 2), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "same shape"):
            repair_stack(masks, masks[:, :, :1], masks)


class PackedLaneTests(unittest.TestCase):
    def test_compact_repair_and_threshold_match_stack_reference(self) -> None:
        rng = np.random.default_rng(7001)
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        transitions = rng.integers(0, 9, size=(19, 23), dtype=np.uint8)
        display = np.arange(8)[:, None, None] >= transitions[None, ...]
        # Deliberately make base/coverage unrelated to exercise repair costs.
        base = rng.random(display.shape) > 0.48
        coverage = rng.random(display.shape) > 0.8
        lane = pack_lane_bits(display, angles, base, coverage)
        unpacked = (
            (lane.display_bits[None, ...]
             >> np.arange(8, dtype=np.uint16)[:, None, None])
            & np.uint16(1)
        ) != 0
        np.testing.assert_array_equal(unpacked, display)
        compact = repair_packed_lane(lane)
        reference = repair_stack(display, base, coverage)
        np.testing.assert_array_equal(
            compact.transition_indices, reference.transition_indices
        )
        np.testing.assert_array_equal(
            compact.changed_count,
            np.count_nonzero(reference.changed_mask, axis=0).astype(np.uint8),
        )
        self.assertEqual(compact.changed_sample_count, reference.changed_sample_count)
        threshold = generate_threshold_transitions(transitions, angles)
        pair = threshold_pair_from_transitions(transitions, transitions, angles)
        np.testing.assert_array_equal(threshold, pair[..., 0])

    def test_compact_lane_rejects_more_than_sixteen_keys(self) -> None:
        stack = np.zeros((17, 1, 1), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "at most 16"):
            pack_lane_bits(stack, np.linspace(0.0, 90.0, 17), stack, stack)


class ExactEdtTests(unittest.TestCase):
    def test_exact_against_brute_force_non_square(self) -> None:
        for shape in ((1, 5), (5, 1), (4, 7), (7, 4)):
            features = np.zeros(shape, dtype=bool)
            features[0, 0] = True
            features[-1, -1] = True
            if shape[0] > 2 and shape[1] > 2:
                features[2, 1] = True
            np.testing.assert_allclose(exact_edt(features), brute_distance(features), atol=1e-12)

    def test_circle_and_edges_against_brute_force(self) -> None:
        yy, xx = np.ogrid[:9, :11]
        features = (yy - 4) ** 2 + (xx - 5) ** 2 <= 5
        features[0, -1] = True
        np.testing.assert_allclose(exact_edt(features), brute_distance(features), atol=1e-12)

    def test_no_features_is_infinite(self) -> None:
        self.assertTrue(np.all(np.isinf(exact_edt(np.zeros((3, 4), dtype=bool)))))

    def test_signed_convention_and_constant_masks(self) -> None:
        mask = np.asarray([[False, True], [False, True]])
        signed = exact_signed_edt(mask)
        np.testing.assert_allclose(signed, [[1.0, -1.0], [1.0, -1.0]])
        self.assertTrue(np.all(np.isneginf(exact_signed_edt(np.ones((2, 3), dtype=bool)))))
        self.assertTrue(np.all(np.isposinf(exact_signed_edt(np.zeros((2, 3), dtype=bool)))))


class BinaryMaskInterpolationTests(unittest.TestCase):
    def test_exact_sdf_blend_moves_a_straight_boundary(self) -> None:
        first = np.asarray([[True, True, False, False, False, False, False]])
        second = np.asarray([[True, True, True, True, True, True, False]])
        np.testing.assert_array_equal(
            interpolate_binary_masks(first, second, 0.25),
            [[True, True, True, False, False, False, False]],
        )
        np.testing.assert_array_equal(
            interpolate_binary_masks(first, second, 0.5),
            [[True, True, True, True, False, False, False]],
        )

    def test_constant_masks_follow_live_preview_finite_distance_rule(self) -> None:
        shadow = np.zeros((3, 5), dtype=np.bool_)
        light = np.ones_like(shadow)
        self.assertFalse(np.any(interpolate_binary_masks(shadow, light, 0.49)))
        # The exact zero crossing is Light, matching Studio's ``<= 0`` rule.
        self.assertTrue(np.all(interpolate_binary_masks(shadow, light, 0.5)))
        self.assertTrue(np.all(interpolate_binary_masks(shadow, light, 0.51)))
        self.assertTrue(np.all(interpolate_binary_masks(light, light, 0.37)))
        self.assertFalse(np.any(interpolate_binary_masks(shadow, shadow, 0.63)))

    def test_endpoints_are_exact_contiguous_bool_copies(self) -> None:
        first = np.asarray([[0, 255, 0], [255, 0, 255]], dtype=np.uint8)[:, ::-1]
        second = np.asarray([[1, 0, 1], [0, 1, 0]], dtype=np.uint16)
        at_first = interpolate_binary_masks(first, second, 0.0)
        at_second = interpolate_binary_masks(first, second, 1.0)
        self.assertEqual(at_first.dtype, np.bool_)
        self.assertTrue(at_first.flags.c_contiguous)
        np.testing.assert_array_equal(at_first, first >= 0.5)
        np.testing.assert_array_equal(at_second, second >= 0.5)

    def test_validation_and_pre_cancel(self) -> None:
        mask = np.zeros((2, 3), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "same shape"):
            interpolate_binary_masks(mask, mask[:, :2], 0.5)
        for factor in (-0.01, 1.01, np.nan, np.inf):
            with self.assertRaisesRegex(ValueError, "factor"):
                interpolate_binary_masks(mask, mask, factor)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            interpolate_binary_masks(mask, mask, 0.5, cancel_flag=True)


class MonotonicTests(unittest.TestCase):
    def test_valid_side_expansion(self) -> None:
        stack = np.zeros((len(SIDE_ANGLES), 2, 3), dtype=bool)
        for index, angle in enumerate(SIDE_ANGLES):
            stack[index, :, 0] = angle >= 30
            stack[index, :, 1] = angle >= 60
        report = validate_side_monotonic(stack, SIDE_ANGLES)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.violation_count, 0)

    def test_reports_pixels_and_transitions(self) -> None:
        stack = np.ones((len(SIDE_ANGLES), 2, 2), dtype=bool)
        stack[1, 0, 0] = False
        stack[3, 1, 1] = False
        report = validate_side_monotonic(stack, SIDE_ANGLES)
        self.assertFalse(report.is_valid)
        self.assertEqual(report.violation_count, 2)
        self.assertEqual(report.violation_pixel_count, 2)
        self.assertTrue(report.violation_map[0, 0])
        self.assertTrue(report.violation_map[1, 1])
        self.assertEqual(len(report.offending_transitions), 2)


class ThresholdTests(unittest.TestCase):
    def test_full_range_liltoon_values_and_channels(self) -> None:
        right = np.asarray([[0, len(SIDE_ANGLES), 2]], dtype=np.uint8)
        left = np.asarray([[0, len(SIDE_ANGLES), 4]], dtype=np.uint8)
        output = threshold_pair_from_transitions(right, left, SIDE_ANGLES)
        self.assertEqual(output.dtype, np.uint16)
        self.assertEqual(output.shape, (1, 3, 2))
        np.testing.assert_array_equal(output[0, 0, :], [ALWAYS_LIGHT, ALWAYS_LIGHT])
        np.testing.assert_array_equal(output[0, 1, :], [ALWAYS_SHADOW, ALWAYS_SHADOW])
        self.assertTrue(1 <= int(output[0, 2, 0]) <= 65534)
        self.assertTrue(1 <= int(output[0, 2, 1]) <= 65534)
        # Earlier Light transitions have larger lilToon SDF values.
        self.assertGreater(output[0, 2, 0], output[0, 2, 1])

    def test_sdf_ratio_places_straight_boundary_halfway(self) -> None:
        angles = np.asarray([0.0, 90.0])
        transitions = np.ones((1, 2), dtype=np.uint8)
        output = generate_threshold_transitions(transitions, angles)
        # Adjacent black/white pixel centres have equal |SDF|, so transition is 45 degrees.
        expected = int(np.floor((1.0 - 0.5) * 65535 + 0.5))
        np.testing.assert_array_equal(output[0, :], [expected, expected])

    def test_transition_outside_lane_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            generate_threshold_transitions(
                np.asarray([[len(SIDE_ANGLES) + 1]]), SIDE_ANGLES
            )

    def test_generation_requires_lane_endpoints(self) -> None:
        transitions = np.zeros((1, 1), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "0 and 90"):
            generate_threshold_transitions(transitions, [15, 45, 90])


class PngTests(unittest.TestCase):
    def test_byte_exact_rgba16_roundtrip(self) -> None:
        pixels = np.asarray(
            [[[0x0000, 0x0001, 0x00FF, 0xFFFF], [0x1234, 0xABCD, 0x8000, 0x7FFF]]],
            dtype=np.uint16,
        )
        header, decoded = decode_png(encode_png_rgba16(pixels, compress_level=9))
        self.assertEqual(
            header,
            {"width": 2, "height": 1, "depth": 16, "color": 6, "compression": 0, "filter": 0, "interlace": 0},
        )
        np.testing.assert_array_equal(decoded, pixels)

    def test_atomic_write_and_overwrite_policy(self) -> None:
        pixels = np.zeros((2, 2, 4), dtype=np.uint16)
        pixels[..., 3] = 65535
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "threshold.png"
            self.assertEqual(write_png_rgba16(path, pixels), path)
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)
            with self.assertRaises(FileExistsError):
                write_png_rgba16(path, pixels)
            pixels[..., 0] = 42
            write_png_rgba16(path, pixels, overwrite=True)
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_worker_temporary_can_be_revision_checked_before_publish(self) -> None:
        pixels = np.arange(4 * 7 * 4, dtype=np.uint16).reshape(4, 7, 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "threshold.png"
            temporary = write_png_rgba16_temporary(path, pixels)
            self.assertTrue(temporary.exists())
            self.assertFalse(path.exists())
            commit_png_temporary(temporary, path)
            self.assertFalse(temporary.exists())
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)

    def test_cancelled_worker_png_removes_partial_temporary(self) -> None:
        pixels = np.zeros((32, 37, 4), dtype=np.uint16)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cancelled.png"
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                write_png_rgba16_temporary(
                    path, pixels, cancel_requested=ctypes.c_int(1)
                )
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_rejects_non_uint16_or_wrong_shape(self) -> None:
        with self.assertRaises(TypeError):
            encode_png_rgba16(np.zeros((2, 2, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            encode_png_rgba16(np.zeros((2, 2, 3), dtype=np.uint16))


if __name__ == "__main__":
    unittest.main()
