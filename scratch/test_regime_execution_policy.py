import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy.scoring_engine import apply_regime_execution_policy


def test_trend_up_keeps_buy_opportunity():
    signal = {"action": "BUY", "fraction": 0.05, "stance": "BUY"}

    adjusted, reason = apply_regime_execution_policy(
        signal=signal,
        score=5.5,
        regime_params={
            "regime": "TREND",
            "buy_threshold": 3,
            "strong_buy_threshold": 6,
            "sell_threshold": -4,
            "strong_sell_threshold": -9,
            "buy_pct": 0.05,
            "strong_buy_pct": 0.09,
        },
        regime_diag={"regime": "TREND", "trend_bias": "UP"},
        indicator_details={"ema_trend": 2, "ema_slope": 1, "price_vs_ema50": 1},
        ai_diag={"effective": 1.0, "weak_catalyst": False},
    )

    assert adjusted == signal
    assert reason == "policy_ok_trend"


def test_trend_down_blocks_weak_buy():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "BUY", "fraction": 0.05, "stance": "BUY"},
        score=5.0,
        regime_params={
            "regime": "TREND",
            "buy_threshold": 3,
            "strong_buy_threshold": 6,
            "sell_threshold": -4,
            "strong_sell_threshold": -9,
            "buy_pct": 0.05,
            "strong_buy_pct": 0.09,
        },
        regime_diag={"regime": "TREND", "trend_bias": "DOWN"},
        ai_diag={"effective": -1.0, "weak_catalyst": True},
    )

    assert adjusted["action"] == "HOLD"
    assert reason == "policy_block_trend_down_without_strong_reversal"


def test_range_requires_mean_reversion_evidence():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "BUY", "fraction": 0.02, "stance": "BUY"},
        score=5.1,
        regime_params={
            "regime": "RANGE",
            "buy_threshold": 5,
            "strong_buy_threshold": 8,
            "sell_threshold": -5,
            "strong_sell_threshold": -10,
            "buy_pct": 0.02,
            "strong_buy_pct": 0.04,
        },
        regime_diag={"regime": "RANGE", "trend_bias": "DOWN"},
        indicator_details={"rsi": 0, "stochrsi": 0, "bollinger": 0},
        ai_diag={"effective": 1.0, "weak_catalyst": False},
    )

    assert adjusted["action"] == "HOLD"
    assert reason == "policy_block_range_without_mean_reversion"


def test_chop_allows_only_strong_score_and_reduces_size():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "STRONG_BUY", "fraction": 0.018, "stance": "STRONG_BUY"},
        score=11.5,
        regime_params={
            "regime": "CHOP",
            "buy_threshold": 7,
            "strong_buy_threshold": 11,
            "sell_threshold": -6,
            "strong_sell_threshold": -10,
            "buy_pct": 0.008,
            "strong_buy_pct": 0.018,
        },
        regime_diag={"regime": "CHOP", "trend_bias": "UP"},
        indicator_details={"rsi": 2, "stochrsi": 2},
        ai_diag={"effective": 2.0, "weak_catalyst": False},
    )

    assert adjusted["action"] == "STRONG_BUY"
    assert adjusted["fraction"] == 0.008
    assert reason == "policy_ok_chop_reduced_size"


def test_aggressive_chop_allows_small_ai_confirmed_probe():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "BUY", "fraction": 0.01, "stance": "BUY"},
        score=6.7,
        regime_params={
            "regime": "CHOP",
            "strategy_profile": "aggressive",
            "buy_threshold": 6.5,
            "strong_buy_threshold": 8,
            "sell_threshold": -4,
            "strong_sell_threshold": -8,
            "buy_pct": 0.01,
            "strong_buy_pct": 0.018,
        },
        regime_diag={"regime": "CHOP", "trend_bias": "UP"},
        indicator_details={"ema_slope": 1},
        ai_diag={"effective": 2.1, "weak_catalyst": False},
    )

    assert adjusted["action"] == "BUY"
    assert adjusted["fraction"] == 0.01
    assert reason == "policy_ok_chop_aggressive_probe"


def test_aggressive_chop_blocks_without_ai_conviction():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "BUY", "fraction": 0.01, "stance": "BUY"},
        score=6.7,
        regime_params={
            "regime": "CHOP",
            "strategy_profile": "aggressive",
            "buy_threshold": 6.5,
            "strong_buy_threshold": 8,
            "sell_threshold": -4,
            "strong_sell_threshold": -8,
            "buy_pct": 0.01,
            "strong_buy_pct": 0.018,
        },
        regime_diag={"regime": "CHOP", "trend_bias": "UP"},
        indicator_details={"ema_slope": 1},
        ai_diag={"effective": 1.9, "weak_catalyst": False},
    )

    assert adjusted["action"] == "HOLD"
    assert reason == "policy_block_chop_aggressive_needs_ai_conviction"


def test_volatile_blocks_without_confirmation():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "BUY", "fraction": 0.015, "stance": "BUY"},
        score=6.4,
        regime_params={
            "regime": "VOLATILE",
            "buy_threshold": 6,
            "strong_buy_threshold": 10,
            "sell_threshold": -6,
            "strong_sell_threshold": -11,
            "buy_pct": 0.015,
            "strong_buy_pct": 0.03,
        },
        regime_diag={"regime": "VOLATILE", "trend_bias": "DOWN"},
        indicator_details={"ema_trend": -2, "ema_slope": -1, "price_vs_ema50": -1},
        ai_diag={"effective": 0.5, "weak_catalyst": True},
    )

    assert adjusted["action"] == "HOLD"
    assert reason == "policy_block_volatile_without_breakout_confirmation"


def test_losing_partial_close_promotes_to_full_close():
    adjusted, reason = apply_regime_execution_policy(
        signal={"action": "PARTIAL_CLOSE", "fraction": 0.5, "stance": "SELL"},
        score=-5.0,
        regime_params={
            "regime": "CHOP",
            "buy_threshold": 7,
            "strong_buy_threshold": 11,
            "sell_threshold": -6,
            "strong_sell_threshold": -10,
            "buy_pct": 0.008,
            "strong_buy_pct": 0.018,
        },
        regime_diag={"regime": "CHOP", "trend_bias": "DOWN"},
        has_position=True,
        position_pnl_pct=-0.026,
    )

    assert adjusted["action"] == "FULL_CLOSE"
    assert adjusted["fraction"] == 1.0
    assert adjusted["stance"] == "STRONG_SELL"
    assert reason == "policy_full_close_loser_chop"
