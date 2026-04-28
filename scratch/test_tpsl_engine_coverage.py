import pytest
import time
from core.tpsl_engine import TPSLEngine

class MockSettings:
    def __init__(self, **kwargs):
        defaults = {
            "tpsl_enabled": True,
            "stop_loss_pct": 0.05,
            "partial_take_profit_enabled": True,
            "partial_take_profit_pct": 0.03,
            "full_take_profit_enabled": True,
            "full_take_profit_pct": 0.08,
            "break_even_stop_enabled": True,
            "break_even_activation_pct": 0.04,
            "break_even_buffer_pct": 0.002,
            "trailing_take_profit_enabled": True,
            "trailing_take_profit_activation_pct": 0.06,
            "trailing_take_profit_giveback_pct": 0.02,
        }
        for k, v in defaults.items():
            setattr(self, k, v)
        for k, v in kwargs.items():
            setattr(self, k, v)

class MockRepo:
    def __init__(self):
        self.storage = {}
    def get(self, key):
        return self.storage.get(key)
    def set(self, key, value, ttl=None):
        self.storage[key] = value
    def delete(self, key):
        if key in self.storage:
            del self.storage[key]

@pytest.fixture
def repo():
    return MockRepo()

@pytest.fixture
def engine(repo):
    settings = MockSettings()
    return TPSLEngine(settings, repo)

def test_tpsl_disabled(engine):
    engine.settings.tpsl_enabled = False
    res = engine.evaluate("BTC/USDT", {"qty": 1}, 100)
    assert res["reason"] == "tpsl_disabled"

def test_no_position(engine, repo):
    res = engine.evaluate("BTC/USDT", None, 100)
    assert res["reason"] == "no_position"

def test_invalid_position_branches(engine):
    res = engine.evaluate("BTC/USDT", {"qty": 1, "status": "CLOSED"}, 100)
    assert res["reason"] == "invalid_open_position"
    res = engine.evaluate("BTC/USDT", {"qty": 0, "status": "OPEN"}, 100)
    assert res["reason"] == "invalid_open_position"
    res = engine.evaluate("BTC/USDT", {"qty": 1, "status": "OPEN"}, 0)
    assert res["reason"] == "invalid_open_position"

def test_avg_entry_unknown(engine):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 0}
    res = engine.evaluate("BTC/USDT", pos, 100)
    assert res["reason"] == "avg_entry_unknown"

def test_sync_state_logic(engine, repo):
    symbol = "BTC/USDT"
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    state = engine._sync_state_with_position(symbol, pos)
    assert state["partial_tp_done"] is False
    
    # Qty increased
    repo.set(f"tpsl_state:{symbol}", {"partial_tp_done": True, "last_qty": 1, "last_avg_entry_price": 100})
    pos["qty"] = 1.5
    state = engine._sync_state_with_position(symbol, pos)
    assert state["partial_tp_done"] is False
    
    # Avg changed
    repo.set(f"tpsl_state:{symbol}", {"partial_tp_done": True, "last_qty": 1.5, "last_avg_entry_price": 100})
    pos["avg_entry_price"] = 105
    state = engine._sync_state_with_position(symbol, pos)
    assert state["partial_tp_done"] is False

    # Qty decreased
    pos["qty"] = 0.7
    state = engine._sync_state_with_position(symbol, pos)
    assert state["partial_tp_done"] is True

def test_stop_loss_hit(engine):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    res = engine.evaluate("BTC/USDT", pos, 94)
    assert res["reason"] == "stop_loss_hit"
    engine.settings.stop_loss_pct = 0
    res = engine.evaluate("BTC/USDT", pos, 94)
    assert not res["triggered"]

def test_break_even_hit(engine):
    engine.settings.break_even_activation_pct = 0.01
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    engine.evaluate("BTC/USDT", pos, 101.5) 
    res = engine.evaluate("BTC/USDT", pos, 100.1)
    assert res["reason"] == "break_even_stop_hit"
    engine.settings.break_even_stop_enabled = False
    res = engine.evaluate("BTC/USDT", pos, 100.1)
    assert not res["triggered"]

def test_profit_roundtrip_hit(engine):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    engine.evaluate("BTC/USDT", pos, 103) 
    res = engine.evaluate("BTC/USDT", pos, 100)
    assert res["reason"] == "profit_roundtrip_stop_hit"

def test_early_trailing_hit(engine):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    engine.evaluate("BTC/USDT", pos, 103)
    res = engine.evaluate("BTC/USDT", pos, 100.5)
    assert res["reason"] == "early_trailing_profit_hit"

def test_full_tp_hit(engine):
    # Set FTP lower than early profit (2.5%)
    engine.settings.full_take_profit_pct = 0.02
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    res = engine.evaluate("BTC/USDT", pos, 102.1)
    assert res["reason"] == "full_take_profit_hit"
    engine.settings.full_take_profit_enabled = False
    res = engine.evaluate("BTC/USDT", pos, 102.1)
    assert not res["triggered"]

def test_partial_tp_hit(engine):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    res = engine.evaluate("BTC/USDT", pos, 103, high_price=104) 
    assert res["reason"] == "partial_take_profit_hit"
    engine.settings.partial_take_profit_enabled = False
    res = engine.evaluate("BTC/USDT", pos, 103, high_price=104)
    assert not res["triggered"]

def test_trailing_tp_hit(engine):
    # Disable partial TP
    engine.settings.partial_take_profit_enabled = False
    # Use giveback < 2% (early) but > own
    engine.settings.trailing_take_profit_giveback_pct = 0.01
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    engine.evaluate("BTC/USDT", pos, 107) 
    res = engine.evaluate("BTC/USDT", pos, 105.5)
    assert res["reason"] == "trailing_take_profit_hit"
    engine.settings.trailing_take_profit_enabled = False
    res = engine.evaluate("BTC/USDT", pos, 105.5)
    assert not res["triggered"]

def test_mark_partial_done(engine, repo):
    pos = {"qty": 1, "status": "OPEN", "avg_entry_price": 100}
    engine.mark_partial_take_profit_done("BTC/USDT", pos)
    state = repo.get("tpsl_state:BTC/USDT")
    assert state["partial_tp_done"] is True
    engine.mark_partial_take_profit_done("BTC/USDT", None)
    repo.delete("tpsl_state:BTC/USDT")
    engine.mark_partial_take_profit_done("BTC/USDT", pos)
    assert repo.get("tpsl_state:BTC/USDT")["partial_tp_done"]
    # Missing branch: status not OPEN
    engine.mark_partial_take_profit_done("X", {"status": "CLOSED"})

def test_peak_price_logic_unreachable(engine):
    pass 

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-vv", "--cov=core.tpsl_engine", "--cov-branch"])
