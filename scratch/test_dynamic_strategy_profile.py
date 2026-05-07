from __future__ import annotations

from main import select_dynamic_strategy_profile


def test_manual_strategy_profile_mode_keeps_base_profile():
    profile, reason = select_dynamic_strategy_profile(
        base_profile="aggressive",
        mode="manual",
        regime="VOLATILE",
        regime_diag={"trend_bias": "DOWN"},
        ai_diag={"effective": -4.0},
        total_score=-5.0,
        has_position=True,
    )

    assert profile == "aggressive"
    assert reason == "manual_profile"


def test_dynamic_strategy_profile_selects_aggressive_for_confirmed_uptrend():
    profile, reason = select_dynamic_strategy_profile(
        base_profile="balanced",
        mode="dynamic",
        regime="TREND",
        regime_diag={"trend_bias": "UP"},
        ai_diag={"effective": 3.0, "weak_catalyst": False},
        total_score=6.0,
        has_position=False,
    )

    assert profile == "aggressive"
    assert reason == "trend_up_ai_confirmed"


def test_dynamic_strategy_profile_controls_risk_in_chop_and_volatile():
    chop_profile, _ = select_dynamic_strategy_profile(
        base_profile="aggressive",
        mode="dynamic",
        regime="CHOP",
        regime_diag={"trend_bias": "N/A"},
        ai_diag={"effective": 0.5},
        total_score=3.0,
        has_position=False,
    )
    volatile_profile, _ = select_dynamic_strategy_profile(
        base_profile="aggressive",
        mode="dynamic",
        regime="VOLATILE",
        regime_diag={"trend_bias": "DOWN"},
        ai_diag={"effective": 1.0},
        total_score=4.0,
        has_position=False,
    )

    assert chop_profile == "conservative"
    assert volatile_profile == "conservative"
