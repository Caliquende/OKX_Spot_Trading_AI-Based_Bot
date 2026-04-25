import pytest
import pandas as pd
import numpy as np
from strategy.regime_engine import _safe_ratio, detect_market_regime, resolve_regime_params

class MockSettings:
    def __init__(self, **kwargs):
        # Default values
        defaults = {
            "regime_trend_adx_min": 25.0,
            "regime_trend_persistence_min": 0.6,
            "regime_chop_ema_dist_max": 0.02,
            "regime_range_adx_max": 20.0,
            "regime_volatile_atr_ratio_min": 0.05,
            "regime_chop_adx_max": 20.0,
            "regime_trend_slope_lookback": 6,
            "buy_threshold": 10.0,
            "strong_buy_threshold": 20.0,
            "sell_threshold": -10.0,
            "strong_sell_threshold": -20.0,
            "buy_pct": 0.5,
            "strong_buy_pct": 1.0,
        }
        for k, v in defaults.items():
            setattr(self, k, v)
        for k, v in kwargs.items():
            setattr(self, k, v)

def test_safe_ratio():
    assert _safe_ratio(10, 0) == 0.0
    assert _safe_ratio(10, 1e-13) == 0.0
    assert _safe_ratio(10, 2) == 5.0

def test_detect_market_regime_short_df():
    settings = MockSettings()
    df = pd.DataFrame([{
        "adx": 10, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10
    }])
    df = pd.concat([df, df]) 
    regime, diag = detect_market_regime(df, settings)
    # adx=10, ema_dist=0, atr_ratio=0.01.
    # _range_ok: 10 <= 20 and 0 <= 0.02 and 0.01 < 0.05 -> True.
    assert regime == "RANGE"

def test_detect_market_regime_trend_up():
    settings = MockSettings(regime_trend_adx_min=20)
    data = [
        {"adx": 30, "atr": 1, "close": 100, "ema20": 90, "ema50": 80, "dmp": 20, "dmn": 10},
        {"adx": 31, "atr": 1, "close": 101, "ema20": 91, "ema50": 81, "dmp": 21, "dmn": 9},
        {"adx": 32, "atr": 1, "close": 102, "ema20": 92, "ema50": 82, "dmp": 22, "dmn": 8},
    ]
    df = pd.DataFrame(data)
    regime, diag = detect_market_regime(df, settings)
    assert regime == "TREND"
    assert diag["trend_bias"] == "UP"
    assert diag["trend_persistence"] == 1.0

def test_detect_market_regime_trend_down():
    settings = MockSettings(regime_trend_adx_min=20)
    data = [
        {"adx": 30, "atr": 1, "close": 100, "ema20": 80, "ema50": 90, "dmp": 10, "dmn": 20},
        {"adx": 31, "atr": 1, "close": 99, "ema20": 79, "ema50": 89, "dmp": 9, "dmn": 21},
        {"adx": 32, "atr": 1, "close": 98, "ema20": 78, "ema50": 88, "dmp": 8, "dmn": 22},
    ]
    df = pd.DataFrame(data)
    regime, diag = detect_market_regime(df, settings)
    assert regime == "TREND"
    assert diag["trend_bias"] == "DOWN"
    assert diag["trend_persistence"] == 1.0

def test_detect_market_regime_range():
    settings = MockSettings(regime_range_adx_max=20, regime_chop_ema_dist_max=0.01)
    data = [
        {"adx": 15, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 15, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 15, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings)
    assert regime == "RANGE"

def test_detect_market_regime_volatile():
    settings = MockSettings(regime_volatile_atr_ratio_min=0.1, regime_chop_adx_max=5)
    data = [
        {"adx": 30, "atr": 20, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 20, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 20, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings)
    assert regime == "VOLATILE"

def test_detect_market_regime_fallback_range():
    settings = MockSettings(regime_volatile_atr_ratio_min=0.5, regime_chop_adx_max=5)
    data = [
        {"adx": 30, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings)
    assert regime == "RANGE" 

def test_hysteresis_trend():
    settings = MockSettings(regime_trend_adx_min=20)
    data = [
        {"adx": 18, "atr": 1, "close": 100, "ema20": 110, "ema50": 80, "dmp": 20, "dmn": 10},
        {"adx": 18, "atr": 1, "close": 100, "ema20": 110, "ema50": 80, "dmp": 20, "dmn": 10},
        {"adx": 18, "atr": 1, "close": 100, "ema20": 110, "ema50": 80, "dmp": 20, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings, current_regime="TREND")
    assert regime == "TREND"
    
    df.loc[:, "adx"] = 16
    regime, _ = detect_market_regime(df, settings, current_regime="TREND")
    assert regime != "TREND"

def test_hysteresis_range():
    settings = MockSettings(regime_range_adx_max=20, regime_trend_adx_min=40)
    # Exit threshold = 23
    data = [
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings, current_regime="RANGE")
    assert regime == "RANGE"
    
    # Case 2: Exit RANGE. 
    # To avoid fallback to RANGE, let's make it TREND.
    df.loc[:, "adx"] = 50 
    df.loc[:, "ema20"] = 150 # ema_dist_ratio = 0.5 > 0.02
    regime, _ = detect_market_regime(df, settings, current_regime="RANGE")
    assert regime == "TREND"

def test_hysteresis_chop():
    settings = MockSettings(regime_chop_adx_max=20)
    data = [
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 22, "atr": 1, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings, current_regime="CHOP")
    assert regime == "CHOP"
    
    df.loc[:, "adx"] = 24
    regime, _ = detect_market_regime(df, settings, current_regime="CHOP")
    assert regime != "CHOP"

def test_hysteresis_volatile():
    settings = MockSettings(regime_volatile_atr_ratio_min=0.1)
    data = [
        {"adx": 30, "atr": 9, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 9, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 30, "atr": 9, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings, current_regime="VOLATILE")
    assert regime == "VOLATILE"
    
    df.loc[:, "atr"] = 7
    regime, _ = detect_market_regime(df, settings, current_regime="VOLATILE")
    assert regime != "VOLATILE"

def test_volatile_exit_via_chop():
    settings = MockSettings(regime_volatile_atr_ratio_min=0.1, regime_chop_adx_max=20)
    data = [
        {"adx": 15, "atr": 15, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 15, "atr": 15, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
        {"adx": 15, "atr": 15, "close": 100, "ema20": 100, "ema50": 100, "dmp": 10, "dmn": 10},
    ]
    df = pd.DataFrame(data)
    regime, _ = detect_market_regime(df, settings, current_regime="VOLATILE")
    assert regime == "CHOP"

def test_resolve_regime_params():
    settings = MockSettings(
        regime_buy_threshold_trend=5.0,
        buy_threshold=10.0
    )
    params = resolve_regime_params(settings, "TREND")
    assert params["buy_threshold"] == 5.0
    assert params["regime"] == "TREND"
    
    params = resolve_regime_params(settings, "UNKNOWN")
    assert params["buy_threshold"] == 10.0
    assert params["regime"] == "BASE"

    params = resolve_regime_params(settings, None)
    assert params["buy_threshold"] == 10.0
    assert params["regime"] == "BASE"

def test_trend_persistence_logic():
    settings = MockSettings(regime_trend_slope_lookback=2)
    data = [
        {"adx": 30, "atr": 1, "close": 100, "ema20": 110, "ema50": 100, "dmp": 20, "dmn": 10}, 
        {"adx": 30, "atr": 1, "close": 100, "ema20": 90, "ema50": 100, "dmp": 20, "dmn": 10},  
        {"adx": 30, "atr": 1, "close": 100, "ema20": 110, "ema50": 100, "dmp": 20, "dmn": 10}, 
        {"adx": 30, "atr": 1, "close": 100, "ema20": 110, "ema50": 100, "dmp": 20, "dmn": 10}, 
    ]
    df = pd.DataFrame(data)
    # len(df) = 4. 
    # last = index 2.
    # start_idx = max(4 - 2 - 2, 0) = 0.
    # window = df.iloc[0:3] (indices 0, 1, 2)
    # last (index 2) has ema20=110, ema50=100 -> sign=1.
    # aligned: [True, False, True]
    # mean = 2/3 = 0.666...
    regime, diag = detect_market_regime(df, settings)
    assert diag["trend_persistence"] == pytest.approx(0.6666, 0.001)

if __name__ == "__main__":
    pytest.main([__file__, "-vv", "--cov=strategy.regime_engine", "--cov-report=term-missing", "--cov-branch"])
