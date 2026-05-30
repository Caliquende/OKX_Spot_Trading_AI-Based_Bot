import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import is_open_trade_position, resolve_profile_scale_in_settings


class MockSettings:
    dust_candidate_max_value_usdt = 0.05
    scale_in_trigger_streak = 9
    strong_scale_in_trigger_streak = 8
    scale_in_buy_pct = 0.09
    strong_scale_in_buy_pct = 0.18
    max_scale_in_count = 7


def test_resolve_profile_scale_in_settings_prefers_profile_values():
    values = {
        "scale_in_trigger_streak": 2,
        "strong_scale_in_trigger_streak": 3,
        "scale_in_buy_pct": 0.015,
        "strong_scale_in_buy_pct": 0.03,
        "max_scale_in_count": 4,
    }

    resolved = resolve_profile_scale_in_settings(values, MockSettings())

    assert resolved == {
        "scale_in_trigger_streak": 2,
        "strong_scale_in_trigger_streak": 3,
        "scale_in_buy_pct": 0.015,
        "strong_scale_in_buy_pct": 0.03,
        "max_scale_in_count": 4,
    }


def test_resolve_profile_scale_in_settings_falls_back_to_settings():
    resolved = resolve_profile_scale_in_settings({}, MockSettings())

    assert resolved["scale_in_trigger_streak"] == 9
    assert resolved["strong_scale_in_trigger_streak"] == 8
    assert resolved["scale_in_buy_pct"] == 0.09
    assert resolved["strong_scale_in_buy_pct"] == 0.18
    assert resolved["max_scale_in_count"] == 7


def test_is_open_trade_position_rejects_closed_zero_and_dust_threshold():
    settings = MockSettings()

    assert not is_open_trade_position({"status": "CLOSED", "qty": 10}, 10, settings)
    assert not is_open_trade_position({"status": "OPEN", "qty": 0}, 10, settings)
    assert not is_open_trade_position({"status": "OPEN", "qty": 0.005}, 10, settings)
    assert is_open_trade_position({"status": "OPEN", "qty": 0.006}, 10, settings)


def test_is_open_trade_position_keeps_qty_fallback_when_price_unavailable():
    assert is_open_trade_position({"status": "OPEN", "qty": 0.1}, 0, MockSettings())
