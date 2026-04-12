"""
LEGACY ORDERS REPO
==================

Bu dosya eski uyumluluk için tutuluyor.
Asıl aktif repository mantığı `db/repositories.py` içindedir.

Basit anlatım:
- Burada order tablosuna erişen küçük yardımcı fonksiyonlar var.
- Yeni kod mümkünse bunu değil merkezi repo katmanını kullanmalı.
"""

def get_recent(self, symbol: str):
    # Bir sembol için en son oluşturulmuş order kayıtlarını getirir.
    return self.db.fetchall(
        """
        SELECT * FROM orders
        WHERE symbol = ?
        ORDER BY created_at_ms DESC
        LIMIT 50
        """,
        (symbol,)
    )
