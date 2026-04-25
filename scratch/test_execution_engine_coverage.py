import pytest
import logging
from unittest.mock import MagicMock, patch
from core.execution_engine import ExecutionEngine

class MockSettings:
    def __init__(self, **kwargs):
        defaults = {
            "scale_in_cooldown_minutes": 5.0, "exit_cooldown_minutes": 10.0, "symbol_cooldown_minutes": 15.0,
            "unknown_submission_lock_minutes": 2.0, "min_order_quote_usdt": 10.0, "dry_run": False,
        }
        for k, v in defaults.items(): setattr(self, k, v)
        for k, v in kwargs.items(): setattr(self, k, v)

@pytest.fixture
def mock_deps():
    deps = {"settings": MockSettings(), "exchange": MagicMock(), "orders_repo": MagicMock(), "positions_repo": MagicMock(), "locks_repo": MagicMock(), "notifier": MagicMock()}
    deps["orders_repo"].has_pending_or_open.return_value = False
    deps["locks_repo"].is_locked.return_value = False
    deps["positions_repo"].get.return_value = {"status": "OPEN", "qty": 10.0}
    # No global side_effect here to avoid conflicts in tests
    deps["exchange"].make_client_order_id.return_value = "c_id"
    return deps

@pytest.fixture
def engine(mock_deps):
    return ExecutionEngine(mock_deps["settings"], mock_deps["exchange"], mock_deps["orders_repo"], mock_deps["positions_repo"], mock_deps["locks_repo"], mock_deps["notifier"])

def test_resolve_cooldown_minutes(engine):
    assert engine._resolve_cooldown_minutes("scale_in") == 5.0
    assert engine._resolve_cooldown_minutes("exit") == 10.0
    assert engine._resolve_cooldown_minutes("unknown_submission") == 2.0
    assert engine._resolve_cooldown_minutes("other") == 15.0

def test_can_submit_branches(engine, mock_deps):
    symbol = "BTC/USDT"
    mock_deps["orders_repo"].has_pending_or_open.return_value = True
    assert not engine.can_submit(symbol)[0]
    mock_deps["orders_repo"].has_pending_or_open.return_value = False
    mock_deps["locks_repo"].is_locked.return_value = True
    assert not engine.can_submit(symbol, intent="entry")[0]
    assert engine.can_submit(symbol, intent="exit")[0]
    assert engine.can_submit(symbol, intent="entry", bypass_lock=True)[0]

def test_notify_order_event_branches(engine, mock_deps):
    engine.notifier = None
    engine._notify_order_event("msg")
    engine.notifier = mock_deps["notifier"]
    mock_deps["notifier"].send.side_effect = Exception("fail")
    engine._notify_order_event("msg")

def test_place_entry_branches(engine, mock_deps):
    mock_deps["orders_repo"].has_pending_or_open.return_value = True
    assert not engine.place_entry("BTC/USDT", 100, "reason")["ok"]
    mock_deps["orders_repo"].has_pending_or_open.return_value = False
    assert not engine.place_entry("BTC/USDT", 5, "reason")["ok"]
    engine.settings.dry_run = True
    assert engine.place_entry("BTC/USDT", 100, "reason")["ok"]
    engine.settings.dry_run = False
    mock_deps["exchange"].create_market_buy_by_quote.return_value = {"id": "e1", "status": "filled"}
    assert engine.place_entry("BTC/USDT", 100, "reason")["ok"]
    mock_deps["exchange"].create_market_buy_by_quote.side_effect = Exception("insufficient balance")
    assert engine.place_entry("BTC/USDT", 100, "reason")["status"] == "REJECTED"
    mock_deps["exchange"].create_market_buy_by_quote.side_effect = Exception("timeout")
    assert engine.place_entry("BTC/USDT", 100, "reason")["status"] == "UNKNOWN_SUBMISSION"

def test_place_exit_branches(engine, mock_deps):
    mock_deps["orders_repo"].has_pending_or_open.return_value = True
    assert not engine.place_exit("BTC/USDT", 10, "reason")["ok"]
    mock_deps["orders_repo"].has_pending_or_open.return_value = False
    assert not engine.place_exit("BTC/USDT", 0, "reason")["ok"]
    # Precision zero branch
    mock_deps["exchange"].amount_to_precision.return_value = 0
    assert not engine.place_exit("BTC/USDT", 1, "reason")["ok"]
    # Dry Run branch
    mock_deps["exchange"].amount_to_precision.return_value = 1
    engine.settings.dry_run = True
    assert engine.place_exit("BTC/USDT", 1, "reason")["ok"]
    # Live success branch
    engine.settings.dry_run = False
    mock_deps["exchange"].create_market_sell_by_base.return_value = {"id": "s1"}
    assert engine.place_exit("BTC/USDT", 1, "reason")["ok"]
    # Exception branch
    mock_deps["exchange"].create_market_sell_by_base.side_effect = Exception("fail")
    assert engine.place_exit("BTC/USDT", 1, "reason")["status"] == "UNKNOWN_SUBMISSION"

def test_force_ops(engine, mock_deps):
    mock_deps["exchange"].create_market_buy_by_quote.return_value = {"id": "f1"}
    mock_deps["exchange"].create_market_sell_by_base.return_value = {"id": "f2"}
    mock_deps["exchange"].amount_to_precision.return_value = 1.0
    assert engine.force_buy("BTC/USDT", 100, "reason")["ok"]
    assert engine.force_sell("BTC/USDT", 1, "reason")["ok"]

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-vv", "--cov=core.execution_engine", "--cov-branch"])
