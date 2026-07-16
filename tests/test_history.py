from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.history import (
    DEFAULT_BYTE_BUDGET,
    DEFAULT_SOFT_BYTE_BUDGET,
    History,
)


def record(
    history: History,
    label: str,
    before: dict[str, np.ndarray],
    after: dict[str, np.ndarray],
    *,
    metadata: dict[str, object] | None = None,
) -> bool:
    transaction = history.begin_transaction(label, metadata=metadata)
    for key in before:
        transaction.add_delta(key, before[key], after[key])
    return transaction.commit()


class HistoryTests(unittest.TestCase):
    def test_sparse_uint8_round_trip(self) -> None:
        before = np.zeros((32, 24), dtype=np.uint8)
        after = before.copy()
        after[2, 3] = 96
        after[29, 20] = 255
        history = History(compression_level=1)

        self.assertTrue(record(history, "Paint", {"display": before}, {"display": after}))
        undo = history.undo_action({"display": after})
        self.assertIsNotNone(undo)
        np.testing.assert_array_equal(undo.images["display"], before)
        redo = history.redo_action({"display": before})
        self.assertIsNotNone(redo)
        np.testing.assert_array_equal(redo.images["display"], after)

    def test_dense_bool_plane_round_trip(self) -> None:
        before = np.zeros((130, 131), dtype=np.bool_)
        after = before.copy()
        after[3:126, 4:129] = True
        history = History(compression_level=1)

        self.assertTrue(record(history, "Coverage", {"coverage": before}, {"coverage": after}))
        restored = history.undo_action({"coverage": after})
        np.testing.assert_array_equal(restored.images["coverage"], before)
        reapplied = history.redo_action({"coverage": before})
        np.testing.assert_array_equal(reapplied.images["coverage"], after)

    def test_multi_plane_action_and_structure_metadata(self) -> None:
        display_before = np.zeros((8, 8), dtype=np.uint8)
        display_after = display_before.copy()
        display_after[1:4, 2:6] = 255
        coverage_before = np.zeros((8, 8), dtype=np.bool_)
        coverage_after = display_after.astype(np.bool_)
        metadata = {
            "created_key": {
                "uuid": "angle-uuid",
                "angle": 34.0,
                "base_blob": b"base",
                "coverage_blob": b"coverage",
            }
        }
        history = History(compression_level=1)

        self.assertTrue(
            record(
                history,
                "Paint + Auto Key",
                {"display": display_before, "coverage": coverage_before},
                {"display": display_after, "coverage": coverage_after},
                metadata=metadata,
            )
        )
        action = history.undo_action(
            {"display": display_after, "coverage": coverage_after}
        )
        self.assertEqual(action.label, "Paint + Auto Key")
        self.assertEqual(action.metadata, metadata)
        np.testing.assert_array_equal(action.images["display"], display_before)
        np.testing.assert_array_equal(action.images["coverage"], coverage_before)

    def test_metadata_only_action(self) -> None:
        history = History()
        transaction = history.begin_transaction("Create Key", metadata={"uuid": "new"})
        self.assertTrue(transaction.commit())
        action = history.undo_action({})
        self.assertEqual(action.metadata, {"uuid": "new"})

    def test_noop_transaction_is_not_recorded(self) -> None:
        plane = np.zeros((4, 4), dtype=np.uint8)
        history = History()
        transaction = history.begin_transaction("No-op")
        self.assertFalse(transaction.add_delta("display", plane, plane.copy()))
        self.assertFalse(transaction.commit())
        self.assertFalse(history.can_undo)

    def test_rollback_restores_all_before_values(self) -> None:
        before_a = np.zeros((8, 8), dtype=np.uint8)
        after_a = np.full((8, 8), 255, dtype=np.uint8)
        before_b = np.zeros((8, 8), dtype=np.bool_)
        after_b = np.ones((8, 8), dtype=np.bool_)
        history = History()
        transaction = history.begin_transaction("Paint")
        transaction.add_delta("a", before_a, after_a)
        transaction.add_delta("b", before_b, after_b)

        restored = transaction.rollback({"a": after_a, "b": after_b})
        np.testing.assert_array_equal(restored["a"], before_a)
        np.testing.assert_array_equal(restored["b"], before_b)
        self.assertIsNone(history.active_transaction)

    def test_hard_cap_keeps_oversize_transaction_rollbackable(self) -> None:
        before = np.zeros((128, 128), dtype=np.uint8)
        after = np.arange(before.size, dtype=np.uint8).reshape(before.shape)
        history = History(byte_budget=32, soft_byte_budget=32, compression_level=0)
        transaction = history.begin_transaction("Too Large")
        transaction.add_delta("display", before, after)

        self.assertTrue(transaction.needs_rollback)
        self.assertFalse(transaction.commit())
        self.assertIs(history.active_transaction, transaction)
        restored = transaction.rollback({"display": after})
        np.testing.assert_array_equal(restored["display"], before)

    def test_soft_budget_evicts_oldest_action(self) -> None:
        zero = np.zeros((64, 64), dtype=np.uint8)
        one = zero.copy()
        one[::2, ::2] = 1
        two = one.copy()
        two[1::2, 1::2] = 2
        probe = History(byte_budget=64 * 1024, soft_byte_budget=64 * 1024)
        record(probe, "one", {"a": zero}, {"a": one})
        one_cost = probe.bytes_used
        history = History(
            byte_budget=64 * 1024,
            soft_byte_budget=max(1, one_cost + one_cost // 2),
            compression_level=1,
        )
        record(history, "one", {"a": zero}, {"a": one})
        record(history, "two", {"a": one}, {"a": two})
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.undo_label, "two")

    def test_new_transaction_discards_redo_branch(self) -> None:
        zero = np.zeros((4, 4), dtype=np.uint8)
        one = np.ones((4, 4), dtype=np.uint8)
        two = np.full((4, 4), 2, dtype=np.uint8)
        history = History()
        record(history, "one", {"a": zero}, {"a": one})
        history.undo_action({"a": one})
        self.assertTrue(history.can_redo)
        record(history, "branch", {"a": zero}, {"a": two})
        self.assertFalse(history.can_redo)

    def test_invalid_arrays_and_metadata_are_rejected(self) -> None:
        rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        history = History()
        transaction = history.begin_transaction("bad")
        with self.assertRaises(ValueError):
            transaction.add_delta("image", rgba, np.zeros((2, 2, 3), np.uint8))
        transaction.rollback()

        with self.assertRaises((TypeError, ValueError)):
            History().begin_transaction("bad", metadata={"angle": float("nan")})
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaises((TypeError, ValueError)):
            History().begin_transaction("bad", metadata=cyclic)

    def test_undo_validates_current_planes_before_moving_entry(self) -> None:
        before = np.zeros((4, 4), dtype=np.uint8)
        after = np.ones((4, 4), dtype=np.uint8)
        history = History()
        record(history, "change", {"image": before}, {"image": after})
        with self.assertRaises(KeyError):
            history.undo_action({})
        self.assertTrue(history.can_undo)
        with self.assertRaises((TypeError, ValueError)):
            history.undo_action({"image": after.astype(np.float64)})
        self.assertTrue(history.can_undo)

    def test_transaction_state_guards(self) -> None:
        plane = np.zeros((4, 4), dtype=np.uint8)
        history = History()
        transaction = history.begin_transaction("change")
        with self.assertRaises(RuntimeError):
            history.begin_transaction("nested")
        transaction.add_delta("display", plane, np.ones_like(plane))
        self.assertTrue(transaction.commit())
        with self.assertRaises(RuntimeError):
            transaction.commit()

    def test_default_budgets(self) -> None:
        history = History()
        self.assertEqual(history.byte_budget, DEFAULT_BYTE_BUDGET)
        self.assertEqual(history.soft_byte_budget, DEFAULT_SOFT_BYTE_BUDGET)


if __name__ == "__main__":
    unittest.main()
