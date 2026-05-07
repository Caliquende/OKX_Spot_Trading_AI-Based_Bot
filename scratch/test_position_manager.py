from __future__ import annotations

from core.position_manager import PositionManager


class MockSettings:
    pass


def test_scale_in_allowed_only_below_average_entry():
    manager = PositionManager(MockSettings())
    position = {"qty": 1.0, "avg_entry_price": 100.0, "status": "OPEN"}

    assert manager.can_scale_in(position, 99.0) == (True, "ok")
    assert manager.can_scale_in(position, 100.0) == (False, "scale_in_only_on_pullback")
    assert manager.can_scale_in(position, 101.0) == (False, "scale_in_only_on_pullback")


def test_scale_in_rejects_invalid_position_inputs():
    manager = PositionManager(MockSettings())

    assert manager.can_scale_in(None, 99.0) == (False, "no_position")
    assert manager.can_scale_in({"avg_entry_price": 0.0}, 99.0) == (False, "invalid_avg")
    assert manager.can_scale_in({"avg_entry_price": 100.0}, 0.0) == (False, "invalid_last_price")
