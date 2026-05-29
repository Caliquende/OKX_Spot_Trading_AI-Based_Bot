from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exchange import OKXExchange
from core.risk_engine import RiskEngine
from main import resolve_min_trade_quote


class MockSettings:
    min_order_quote_usdt = 0.05
    min_trade_pct = 0.04
    min_trade_quote_buffer_pct = 0.02
    min_free_usdt = 0.0
    max_symbol_exposure_pct = 0.20
    max_total_exposure_pct = 0.90
    max_single_trade_pct = 0.15


class MockExchange:
    def min_quote_amount(self, symbol: str, last_price: float = 0.0) -> float:
        return 2.0


def test_resolve_min_trade_quote_uses_portfolio_pct_and_exchange_floor():
    quote = resolve_min_trade_quote(
        MockExchange(),
        "NEAR/USDT",
        2.5,
        {"total_balance": 62.0},
        MockSettings(),
    )

    assert round(quote, 4) == round(62.0 * 0.04 * 1.02, 4)


def test_exchange_min_quote_amount_uses_min_base_times_price():
    exchange = OKXExchange.__new__(OKXExchange)
    exchange.market = lambda symbol: {
        "limits": {"amount": {"min": 1.0}, "cost": {"min": None}},
        "info": {},
    }

    assert exchange.min_quote_amount("NEAR/USDT", 2.5) == 2.5


def test_risk_engine_honors_dynamic_min_order_quote():
    engine = RiskEngine(MockSettings())

    allowed, quote, reason = engine.check_entry(
        symbol="NEAR/USDT",
        portfolio={"total_balance": 62.0, "cash_balance": 62.0},
        position=None,
        desired_quote=2.0,
        last_price=2.5,
        risk_limits={"min_order_quote_usdt": 2.5296},
    )

    assert not allowed
    assert quote == 0.0
    assert reason == "too_small_after_risk"
