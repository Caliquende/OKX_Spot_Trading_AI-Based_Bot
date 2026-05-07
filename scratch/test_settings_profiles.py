from __future__ import annotations

import importlib


def _load_settings_with_profile(monkeypatch, profile: str):
    monkeypatch.setenv("OKX_API_KEY", "test")
    monkeypatch.setenv("OKX_SECRET", "test")
    monkeypatch.setenv("OKX_PASSPHRASE", "test")
    monkeypatch.setenv("STRATEGY_PROFILE", profile)
    monkeypatch.setenv("STRATEGY_AGGRESSIVE_MAX_SYMBOL_EXPOSURE_PCT", "0.12")
    monkeypatch.setenv("STRATEGY_AGGRESSIVE_MAX_TOTAL_EXPOSURE_PCT", "0.95")
    monkeypatch.setenv("STRATEGY_AGGRESSIVE_MAX_SINGLE_TRADE_PCT", "0.04")
    monkeypatch.setenv("STRATEGY_BALANCED_MAX_SYMBOL_EXPOSURE_PCT", "0.10")
    monkeypatch.setenv("STRATEGY_BALANCED_MAX_TOTAL_EXPOSURE_PCT", "0.60")
    monkeypatch.setenv("STRATEGY_BALANCED_MAX_SINGLE_TRADE_PCT", "0.025")

    import config.settings as settings_module

    return importlib.reload(settings_module).settings


def test_aggressive_profile_uses_env_profile_risk_limits(monkeypatch):
    settings = _load_settings_with_profile(monkeypatch, "aggressive")

    assert settings.max_symbol_exposure_pct == 0.12
    assert settings.max_total_exposure_pct == 0.95
    assert settings.max_single_trade_pct == 0.04


def test_balanced_profile_is_supported(monkeypatch):
    settings = _load_settings_with_profile(monkeypatch, "balanced")

    assert settings.max_symbol_exposure_pct == 0.10
    assert settings.max_total_exposure_pct == 0.60
    assert settings.max_single_trade_pct == 0.025
