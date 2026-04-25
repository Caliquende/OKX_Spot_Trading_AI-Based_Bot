import pytest
import sqlite3
import json
import time
from contextlib import contextmanager
from typing import Iterator
from db.database import Database
from db.repositories import OrdersRepo, FillsRepo, PositionsRepo, LocksRepo, BotStateRepo

class TestDatabase(Database):
    def __init__(self):
        self.path = ":memory:"
        self._shared_conn = sqlite3.connect(self.path, check_same_thread=False)
        self._shared_conn.row_factory = sqlite3.Row
        super().__init__(self.path)

    def _connect(self) -> sqlite3.Connection:
        return self._shared_conn

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
            con.commit()
        finally:
            pass

@pytest.fixture
def db():
    return TestDatabase()

def test_orders_repo_final(db):
    repo = OrdersRepo(db)
    repo.create_pending("c1", "BTC/USDT", "buy", "market", 1, 100, "entry", 1000)
    # updated_at_ms is None branch
    repo.update_status("c1", status="FILLED", now_ms=None, updated_at_ms=None)
    # has_pending_or_open True branch
    repo.create_pending("c2", "BTC/USDT", "buy", "market", 1, 100, "entry", 1000)
    assert repo.has_pending_or_open("BTC/USDT")
    # get_sync_candidates
    assert len(repo.get_sync_candidates("BTC/USDT")) > 0
    # get_pending_or_open
    assert len(repo.get_pending_or_open("BTC/USDT")) > 0

def test_orders_repo_update_status_error(db):
    repo = OrdersRepo(db)
    with pytest.raises(ValueError):
        repo.update_status("x", status=None)

def test_fills_repo_final(db):
    repo = FillsRepo(db)
    repo.insert("t1", "o1", "c1", "BTC/USDT", "buy", 1, 100, 100, 0.1, "USDT", 1000, 0, "entry")
    assert repo.exists("t1")
    assert repo.count_all() == 1
    # latest_for_symbol
    assert repo.latest_for_symbol("BTC/USDT") is not None
    # sum_realized_pnl with and without end_ms
    assert repo.sum_realized_pnl_since(1000) == 0.0
    assert repo.sum_realized_pnl_since(1000, 2000) == 0.0
    # summarize_exit_reasons with and without end_ms
    repo.insert("t2", "o2", "c2", "BTC/USDT", "sell", 1, 110, 110, 0.1, "USDT", 1500, 9.9, "exit_ok")
    assert len(repo.summarize_exit_reasons_since(1000)) == 1
    assert len(repo.summarize_exit_reasons_since(1000, 2000)) == 1

def test_positions_repo_final(db):
    repo = PositionsRepo(db)
    repo.upsert("BTC/USDT", "BTC", "USDT", 1.0, 100.0, 0, 0, "OPEN", 1000)
    assert repo.count_open() == 1
    # get_all_open (Satır 361)
    assert len(repo.get_all_open()) == 1
    pos = repo.get("BTC/USDT")
    assert pos["qty"] == 1.0
    repo.upsert("BTC/USDT", "BTC", "USDT", 0.0, 0, 5, 0.2, "CLOSED", 2000)
    assert repo.count_open() == 0

def test_locks_repo_final(db):
    repo = LocksRepo(db)
    repo.lock("BTC/USDT", 2000, "cooldown", 1000)
    assert repo.is_locked("BTC/USDT", 1500)
    assert repo.get_lock_info("BTC/USDT")["reason"] == "cooldown"
    repo.clear_expired(2500)
    assert repo.get_lock_info("BTC/USDT") is None
    repo.clear_all()

def test_bot_state_repo_final(db):
    repo = BotStateRepo(db)
    repo.set("streak", {"count": 3}, 1000)
    assert repo.get("streak")["count"] == 3
    db.execute("INSERT INTO bot_state (key, value, updated_at_ms) VALUES (?, ?, ?)", ("raw", "plain", 1000))
    assert repo.get("raw") == "plain"
    repo.delete("streak")
    assert repo.get("streak") is None

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-vv", "--cov=db.repositories", "--cov-branch"])
