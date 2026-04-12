"""
LEGACY POSITIONS REPO
=====================

Bu dosya eski pozisyon repo yardımcılarını tutar.
Asıl aktif positions repo artık `db/repositories.py` içindedir.
"""

def upsert(
    self,
    symbol,
    base_asset,
    quote_asset,
    qty,
    avg_entry_price,
    realized_pnl_quote,
    fees_quote,
    status,
    now_ms,
):
    # Pozisyon satırı yoksa ekler, varsa günceller.
    self.db.execute(
        """
        INSERT INTO positions (
            symbol, base_asset, quote_asset,
            qty, avg_entry_price,
            realized_pnl_quote, fees_quote,
            status, updated_at_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
