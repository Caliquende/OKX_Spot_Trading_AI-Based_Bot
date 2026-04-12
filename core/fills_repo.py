"""
LEGACY FILLS REPO
=================

Bu dosya eski yardımcı fonksiyonları tutar.
Ana aktif kullanım artık `db/repositories.py` tarafındadır.

Basit özet:
- fills = borsada gerçekten olmuş trade satırlarıdır.
- Bu dosya o satırları okuma/yazma için küçük helper sağlar.
"""

def exists(self, trade_id: str) -> bool:
    # Aynı trade daha önce yazılmış mı kontrol eder.
    row = self.db.fetchone(
        "SELECT 1 FROM fills WHERE trade_id = ?",
        (trade_id,)
    )
    return row is not None


def insert(
    self,
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
):
    # Yeni bir fill satırını DB'ye ekler.
    self.db.execute(
        """
        INSERT INTO fills (
            trade_id, order_id, client_order_id,
            symbol, side, qty, price, cost,
            fee_cost, fee_currency, timestamp_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )


def by_symbol(self, symbol: str):
    # Bir sembolün tüm fill geçmişini zaman sırasıyla getirir.
    return self.db.fetchall(
        """
        SELECT * FROM fills
        WHERE symbol = ?
        ORDER BY timestamp_ms ASC
        """,
        (symbol,)
    )
