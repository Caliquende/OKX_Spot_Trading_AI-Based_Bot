import pytest
import logging
from unittest.mock import MagicMock, patch
from core.reconciler import Reconciler

class MockSettings:
    def __init__(self, **kwargs):
        defaults = {
            "reconcile_epsilon": 1e-12, "position_source_mode": "hybrid", "allow_balance_only_positions": True,
            "sandbox_ignore_balance_zero": True, "live_force_close_on_zero_balance": True,
            "preserve_position_on_balance_fetch_error": True, "reconcile_rebase_after_mismatch_count": 3,
            "min_position_value_usdt": 5.0, "reconcile_warn_abs_quote_usdt": 5.0,
            "reconcile_warn_ratio": 0.10, "reconcile_soft_zero_multiplier": 3.0, "reconcile_since_ms": 0,
        }
        for k, v in defaults.items(): setattr(self, k, v)
        for k, v in kwargs.items(): setattr(self, k, v)

@pytest.fixture
def mock_deps():
    deps = {"exchange": MagicMock(), "orders_repo": MagicMock(), "fills_repo": MagicMock(), "positions_repo": MagicMock(), "bot_state_repo": MagicMock()}
    deps["exchange"].settings = MockSettings(); deps["exchange"].ex.sandbox = False; deps["exchange"].min_amount.return_value = 0.0001; deps["bot_state_repo"].get.return_value = None
    return deps

@pytest.fixture
def reconciler(mock_deps):
    return Reconciler(mock_deps["exchange"], mock_deps["orders_repo"], mock_deps["fills_repo"], mock_deps["positions_repo"], mock_deps["bot_state_repo"])

def test_full_reconcile_flow(reconciler, mock_deps):
    # Comprehensive test to hit almost everything
    mock_deps["orders_repo"].get_sync_candidates.return_value = [{"client_order_id": "c1", "exchange_order_id": "e1"}]
    mock_deps["orders_repo"].is_ghost_cleaned.return_value = False
    mock_deps["exchange"].fetch_order.return_value = {"status": "filled", "id": "e1"}
    
    mock_deps["exchange"].fetch_my_trades.side_effect = [
        [{"id": "t1", "side": "buy", "amount": 1, "price": 100, "cost": 100, "timestamp": 1000, "fee": {"cost": 0, "currency": "USDT"}, "info": {"clOrdId": "c_buy"}}],
        []
    ]
    mock_deps["fills_repo"].exists.return_value = False
    mock_deps["fills_repo"].by_symbol.return_value = [
        {"trade_id": "t1", "side": "buy", "qty": 1, "price": 100, "cost": 100, "fee_cost": 0, "fee_currency": "USDT", "timestamp_ms": 1000}
    ]
    mock_deps["exchange"].fetch_balance.return_value = {}; mock_deps["exchange"].get_asset_total.return_value = 1.0
    mock_deps["exchange"].fetch_ticker.return_value = {"last": 100}
    
    reconciler.sync_symbol("BTC/USDT")
    assert mock_deps["positions_repo"].upsert.called

def test_anchor_and_rebase(reconciler, mock_deps):
    mock_deps["fills_repo"].latest_for_symbol.return_value = {"timestamp_ms": 1000, "trade_id": "t1"}
    reconciler._set_reconcile_anchor(symbol="BTC/USDT", qty=1, avg_entry_price=100, realized_pnl_quote=0, fees_quote=0, reason="test")
    
    # Test consumption logic in _build_from_fills
    mock_deps["bot_state_repo"].get.return_value = {"qty": 1, "avg_entry_price": 100, "last_fill_ts_ms": 1000, "last_trade_id": "t1"}
    mock_deps["fills_repo"].by_symbol.return_value = [{"trade_id": "t1", "timestamp_ms": 1000, "side": "buy", "qty": 1}]
    qty, avg, _, _ = reconciler._build_from_fills("BTC/USDT")
    assert qty == 1.0

def test_sync_trades_exit_reason_and_paging(reconciler, mock_deps):
    # Test exit reason match
    mock_deps["exchange"].fetch_my_trades.side_effect = [
        [{"id": "t_sell", "side": "sell", "amount": 1, "price": 110, "cost": 110, "timestamp": 2000, "fee": {"cost": 0, "currency": "USDT"}, "info": {"clientOrderId": "c2"}}],
        []
    ]
    mock_deps["fills_repo"].exists.return_value = False
    mock_deps["bot_state_repo"].get.return_value = {"client_order_id": "c2", "reason": "STOP_LOSS"}
    reconciler._sync_trades("BTC/USDT")
    args = mock_deps["fills_repo"].insert.call_args[1]
    assert args["exit_reason"] == "STOP_LOSS"

def test_safe_last_price_error_branches(reconciler, mock_deps):
    mock_deps["exchange"].fetch_ticker.side_effect = Exception("API Fail")
    # Should fall back to fills_avg (100) then existing_avg (90) then 0
    assert reconciler._safe_last_price("BTC/USDT", 100, 90) == 100
    assert reconciler._safe_last_price("BTC/USDT", 0, 90) == 90
    assert reconciler._safe_last_price("BTC/USDT", 0, 0) == 0.0

def test_rebuild_position_mismatch_modulo(reconciler, mock_deps):
    # Register mismatch (Modulo not really tested in mini version but registers mismatch)
    mock_deps["exchange"].get_asset_total.return_value = 2.0
    mock_deps["fills_repo"].by_symbol.return_value = [{"trade_id": "t1", "timestamp_ms": 1000, "side": "buy", "qty": 1, "price": 100, "cost": 100, "fee_cost": 0, "fee_currency": "USDT"}]
    mock_deps["exchange"].fetch_ticker.return_value = {"last": 100}
    reconciler._rebuild_position("BTC/USDT")
    assert mock_deps["bot_state_repo"].set.called

def test_apply_fill_accounting_various_fees(reconciler):
    # fee in base_asset for buy
    q, a, p, f = reconciler._apply_fill_accounting(side="buy", fill_qty=1, fill_price=100, fill_cost=100, fee_cost=0.01, fee_ccy="BTC", base_asset="BTC", quote_asset="USDT", current_qty=0, current_avg=0)
    assert q == 0.99
    # fee in base_asset for sell
    q, a, p, f = reconciler._apply_fill_accounting(side="sell", fill_qty=1, fill_price=110, fill_cost=110, fee_cost=0.01, fee_ccy="BTC", base_asset="BTC", quote_asset="USDT", current_qty=1, current_avg=100)
    assert pytest.approx(p) == 9.0 # (110-100)*1 - (0.01*110)

def test_rebuild_position_sandbox_fills_ignore(reconciler, mock_deps):
    mock_deps["exchange"].ex.sandbox = True
    mock_deps["exchange"].get_asset_total.return_value = 0.0 # balance zero
    mock_deps["fills_repo"].by_symbol.return_value = [{"trade_id": "t1", "timestamp_ms": 1000, "side": "buy", "qty": 1, "price": 100, "cost": 100, "fee_cost": 0, "fee_currency": "USDT"}]
    mock_deps["exchange"].fetch_ticker.return_value = {"last": 100}
    reconciler._rebuild_position("BTC/USDT")
    args = mock_deps["positions_repo"].upsert.call_args[1]
    assert args["qty"] == 1.0 # Should use fills in sandbox zero balance

def test_meaningful_mismatch_low_diff(reconciler):
    # Small diff that is NOT meaningful
    assert not reconciler._meaningful_mismatch(1.0001, 1.0, 100, 5.0, 0.10)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-vv", "--cov=core.reconciler", "--cov-branch"])
