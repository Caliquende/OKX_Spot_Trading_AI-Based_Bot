from __future__ import annotations

"""
REPOSITORY KATMANI
=================

Bu dosya SQL'in çirkinliğini botun geri kalanından ayırır. Amaç şudur:
- main / core katmanları doğrudan SQL stringleriyle uğraşmasın
- tablo bazlı CRUD mantığı merkezi olsun
- muhasebe ve state akışı izlenebilir olsun
"""

import json
from db.database import Database


ACTIVE_ORDER_STATUSES = ("PENDING", "OPEN", "UNKNOWN_SUBMISSION")


class OrdersRepo:
    def __init__(self, db: Database):
        self.db = db

    def create_pending(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        requested_qty: float | None,
        requested_quote: float | None,
        reason: str,
        now_ms: int,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO orders(
                client_order_id, exchange_order_id, symbol, side, order_type,
                requested_qty, requested_quote, status, reason, created_at_ms, updated_at_ms
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
            """,
            (
                client_order_id,
                symbol,
                side,
                order_type,
                requested_qty,
                requested_quote,
                reason,
                now_ms,
                now_ms,
            ),
        )

    def update_status(
        self,
        client_order_id: str,
        exchange_order_id: str | None = None,
        status: str | None = None,
        now_ms: int | None = None,
        updated_at_ms: int | None = None,
    ) -> None:
        if updated_at_ms is None:
            updated_at_ms = now_ms
        if updated_at_ms is None:
            import time
            updated_at_ms = int(time.time() * 1000)
        if status is None:
            raise ValueError("status cannot be None in OrdersRepo.update_status")

        self.db.execute(
            """
            UPDATE orders
            SET exchange_order_id = COALESCE(?, exchange_order_id),
                status = ?,
                updated_at_ms = ?
            WHERE client_order_id = ?
            """,
            (
                exchange_order_id,
                str(status).upper(),
                updated_at_ms,
                client_order_id,
            ),
        )

    def get_pending_or_open(self, symbol: str):
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATUSES)
        return self.db.fetchall(
            f"""
            SELECT * FROM orders
            WHERE symbol = ? AND UPPER(status) IN ({placeholders})
            ORDER BY created_at_ms ASC
            """,
            (symbol, *ACTIVE_ORDER_STATUSES),
        )

    def has_pending_or_open(self, symbol: str) -> bool:
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATUSES)
        row = self.db.fetchone(
            f"""
            SELECT client_order_id FROM orders
            WHERE symbol = ? AND UPPER(status) IN ({placeholders})
            LIMIT 1
            """,
            (symbol, *ACTIVE_ORDER_STATUSES),
        )
        return row is not None

    def get_recent(self, symbol: str):
        return self.db.fetchall(
            """
            SELECT * FROM orders
            WHERE symbol = ?
            ORDER BY created_at_ms DESC
            LIMIT 50
            """,
            (symbol,),
        )

    def get_sync_candidates(self, symbol: str):
        # Reconciler sadece gerçekten ilgilenmesi gereken order'ları alsın.
        placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATUSES)
        return self.db.fetchall(
            f"""
            SELECT *
            FROM orders
            WHERE symbol = ?
              AND UPPER(status) IN ({placeholders})
              AND ghost_cleaned_at_ms IS NULL
            ORDER BY created_at_ms DESC
            LIMIT 50
            """,
            (symbol, *ACTIVE_ORDER_STATUSES),
        )

    def is_ghost_cleaned(self, client_order_id: str) -> bool:
        row = self.db.fetchone(
            """
            SELECT ghost_cleaned_at_ms
            FROM orders
            WHERE client_order_id = ?
            """,
            (client_order_id,),
        )
        return bool(row and row.get("ghost_cleaned_at_ms"))

    def mark_ghost_cleaned(
        self,
        client_order_id: str,
        exchange_order_id: str | None,
        now_ms: int,
        reason: str = "ghost_order_not_found",
    ) -> None:
        # Bir kez ghost diye işaretlenen order bir daha cleanup denemesine girmez.
        if exchange_order_id:
            self.db.execute(
                """
                UPDATE orders
                SET exchange_order_id = ?,
                    status = 'CANCELED',
                    ghost_cleaned_at_ms = COALESCE(ghost_cleaned_at_ms, ?),
                    ghost_cleanup_reason = COALESCE(ghost_cleanup_reason, ?),
                    updated_at_ms = ?
                WHERE client_order_id = ?
                """,
                (exchange_order_id, now_ms, reason, now_ms, client_order_id),
            )
        else:
            self.db.execute(
                """
                UPDATE orders
                SET status = 'CANCELED',
                    ghost_cleaned_at_ms = COALESCE(ghost_cleaned_at_ms, ?),
                    ghost_cleanup_reason = COALESCE(ghost_cleanup_reason, ?),
                    updated_at_ms = ?
                WHERE client_order_id = ?
                """,
                (now_ms, reason, now_ms, client_order_id),
            )


class FillsRepo:
    """
    Fills tablosu repository.
    """

    def __init__(self, db: Database):
        self.db = db

    def count_all(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) as cnt FROM fills")
        return int(row["cnt"]) if row else 0

    def exists(self, trade_id: str) -> bool:
        row = self.db.fetchone("SELECT 1 FROM fills WHERE trade_id = ?", (trade_id,))
        return row is not None

    def insert(
        self,
        trade_id: str,
        order_id: str | None,
        client_order_id: str | None,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        cost: float | None,
        fee_cost: float,
        fee_currency: str | None,
        timestamp_ms: int,
        realized_pnl_quote: float = 0.0,
        exit_reason: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO fills(
                trade_id, order_id, client_order_id, symbol, side, qty, price, cost,
                fee_cost, fee_currency, timestamp_ms, realized_pnl_quote, exit_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                order_id,
                client_order_id,
                symbol,
                side,
                qty,
                price,
                cost,
                fee_cost,
                fee_currency,
                timestamp_ms,
                realized_pnl_quote,
                exit_reason,
            ),
        )

    def update_realized_pnl_quote(self, trade_id: str, realized_pnl_quote: float) -> None:
        """
        Tek bir fill için realized pnl kolonunu günceller.

        Bu helper neden gerekli?
        - Reconciler yeni trade insert ettikten sonra tüm symbol fills zincirini
          muhasebe açısından tekrar yürütür.
        - Böylece eski kayıtlar da backfill edilir ve /daily_pnl eski veriler için de düzelir.
        - INSERT sırasında anlık hesapla yazmak tek başına yetmez; sıralama / eski veri /
          duplicate trade senaryolarında tam doğruluk için toplu refresh daha güvenlidir.
        """
        self.db.execute(
            """
            UPDATE fills
            SET realized_pnl_quote = ?
            WHERE trade_id = ?
            """,
            (realized_pnl_quote, trade_id),
        )

    def by_symbol(self, symbol: str):
        return self.db.fetchall(
            "SELECT * FROM fills WHERE symbol = ? ORDER BY timestamp_ms ASC, trade_id ASC",
            (symbol,),
        )

    def latest_for_symbol(self, symbol: str):
        return self.db.fetchone(
            """
            SELECT * FROM fills
            WHERE symbol = ?
            ORDER BY timestamp_ms DESC, trade_id DESC
            LIMIT 1
            """,
            (symbol,),
        )

    def sum_realized_pnl_since(self, start_ms: int, end_ms: int | None = None) -> float:
        """
        /daily_pnl gibi zaman pencereli raporlar için fill-level realized toplamını döndürür.

        Not:
        - Burada positions tablosu kullanılmaz; çünkü positions tarafı kümülatiftir.
        - Günlük sorguda doğru truth source fills tablosudur.
        - timestamp_ms kullanılır; eski bozuk sorgudaki ts_ms diye bir kolon yoktur.
        """
        if end_ms is None:
            row = self.db.fetchone(
                """
                SELECT COALESCE(SUM(realized_pnl_quote), 0) AS realized_total
                FROM fills
                WHERE timestamp_ms >= ?
                """,
                (start_ms,),
            )
        else:
            row = self.db.fetchone(
                """
                SELECT COALESCE(SUM(realized_pnl_quote), 0) AS realized_total
                FROM fills
                WHERE timestamp_ms >= ? AND timestamp_ms < ?
                """,
                (start_ms, end_ms),
            )
        return float((row or {}).get("realized_total") or 0.0)

    def summarize_exit_reasons_since(self, start_ms: int, end_ms: int | None = None) -> list[dict]:
        """
        Exit reason analytics için özet döndürür.

        Neden ayrı helper var?
        - exit_reason artık fills tablosunda tutuluyor ama ham satırlar karar verdirmez.
        - Operasyonel olarak ihtiyaç duyulan şey, hangi exit tipinin kaç kez ve ne kadar realized pnl ile
          sonuçlandığını tek sorguda görebilmektir.
        - Bu helper sell fillleri reason bazında gruplayıp hem adet hem realized toplamını döndürür.
        """
        if end_ms is None:
            params = (start_ms,)
            where_time = "timestamp_ms >= ?"
        else:
            params = (start_ms, end_ms)
            where_time = "timestamp_ms >= ? AND timestamp_ms < ?"

        rows = self.db.fetchall(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(exit_reason), ''), 'unknown') AS exit_reason,
                COUNT(*) AS fill_count,
                COALESCE(SUM(realized_pnl_quote), 0) AS realized_total,
                COALESCE(AVG(realized_pnl_quote), 0) AS avg_realized_pnl,
                MAX(timestamp_ms) AS last_fill_ts_ms
            FROM fills
            WHERE {where_time}
              AND LOWER(side) = 'sell'
            GROUP BY COALESCE(NULLIF(TRIM(exit_reason), ''), 'unknown')
            ORDER BY fill_count DESC, realized_total DESC
            """,
            params,
        )
        return rows or []


class PositionsRepo:


    def __init__(self, db: Database):
        self.db = db


    def get(self, symbol: str):

        return self.db.fetchone(

            "SELECT * FROM positions WHERE symbol = ?",

            (symbol,),

        )


    def get_all_open(self):

        return self.db.fetchall(

            """

            SELECT *

            FROM positions

            WHERE status = 'OPEN'

            AND qty > 0

            """

        )


    def count_open(self) -> int:

        row = self.db.fetchone(

            "SELECT COUNT(*) AS c FROM positions WHERE status = 'OPEN' AND qty > 0"

        )

        return int(row["c"]) if row else 0


    def upsert(

        self,

        symbol: str,

        base_asset: str,

        quote_asset: str,

        qty: float,

        avg_entry_price: float,

        realized_pnl_quote: float,

        fees_quote: float,

        status: str,

        now_ms: int,

    ) -> None:

        self.db.execute(

            """

            INSERT INTO positions(

                symbol, base_asset, quote_asset, qty, avg_entry_price,

                realized_pnl_quote, fees_quote, status, updated_at_ms

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(symbol) DO UPDATE SET

                qty=excluded.qty,

                avg_entry_price=excluded.avg_entry_price,

                realized_pnl_quote=excluded.realized_pnl_quote,

                fees_quote=excluded.fees_quote,

                status=excluded.status,

                updated_at_ms=excluded.updated_at_ms

            """,

            (

                symbol,

                base_asset,

                quote_asset,

                qty,

                avg_entry_price,

                realized_pnl_quote,

                fees_quote,

                status,

                now_ms,

            ),

        )

class LocksRepo:
    def __init__(self, db):
        self.db = db

    def lock(self, symbol: str, locked_until_ms: int, reason: str, now_ms: int) -> None:
        self.db.execute(
            """
            INSERT INTO symbol_locks(symbol, locked_until_ms, reason, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                locked_until_ms=excluded.locked_until_ms,
                reason=excluded.reason,
                updated_at_ms=excluded.updated_at_ms
            """,
            (symbol, locked_until_ms, reason, now_ms),
        )

    def is_locked(self, symbol: str, now_ms: int) -> bool:
        row = self.db.fetchone(
            "SELECT locked_until_ms FROM symbol_locks WHERE symbol = ?",
            (symbol,),
        )
        return bool(row and int(row["locked_until_ms"]) > now_ms)

    def get_lock_info(self, symbol: str):
        return self.db.fetchone(
            "SELECT symbol, locked_until_ms, reason, updated_at_ms FROM symbol_locks WHERE symbol = ?",
            (symbol,),
        )

    def clear_expired(self, now_ms: int) -> None:
        self.db.execute(
            "DELETE FROM symbol_locks WHERE locked_until_ms <= ?",
            (now_ms,),
        )

    def clear_all(self) -> None:
        self.db.execute("DELETE FROM symbol_locks")


class BotStateRepo:
    """
    bot_state tablosu için generic repo.

    Burada JSON state tutuyoruz.
    TP/SL engine'in partial TP state'i ve signal streak persistence gibi küçük state'ler için yeterli.
    """

    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str):
        row = self.db.fetchone(
            "SELECT value FROM bot_state WHERE key = ?",
            (key,),
        )
        if not row:
            return None
        raw = row["value"]
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def set(self, key: str, value, now_ms: int) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.db.execute(
            """
            INSERT INTO bot_state(key, value, updated_at_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at_ms=excluded.updated_at_ms
            """,
            (key, raw, now_ms),
        )

    def delete(self, key: str) -> None:
        self.db.execute(
            "DELETE FROM bot_state WHERE key = ?",
            (key,),
        )
