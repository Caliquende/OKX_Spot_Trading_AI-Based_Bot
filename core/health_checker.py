from __future__ import annotations

"""
HEALTH CHECKER
==============

Bu katman monitoring amaçlı sağlık özeti üretir.
Amaç execution pipeline'ını kör şekilde bloklamak değil, gerçekten neyin bozuk olduğunu daha dürüst göstermektir.

Temel prensip:
- Public endpoint bozuldu diye private endpoint'i çalışmıyor varsayma.
- Sandbox yalpalıyor diye execution bug'ı varmış gibi davranma.
- "ok / fail" ikiliğine sıkışma. DEGRADED diye bir gerçek dünya vardır.
"""

import time


class HealthChecker:
    def __init__(self, exchange, db):
        # Sağlık kontrolü için hem exchange hem DB gerekir.
        self.exchange = exchange
        self.db = db

    def run(self) -> dict:
        # Burada üç şeyin çalışıp çalışmadığına bakıyoruz:
        # 1) public endpoint
        # 2) private endpoint
        # 3) DB yazma
        now_ms = int(time.time() * 1000)

        public_ok = True
        private_ok = True
        db_ok = True

        public_error = ""
        private_error = ""
        db_error = ""

        try:
            self.exchange.fetch_ticker("BTC/USDT")
        except Exception as exc:
            public_ok = False
            public_error = str(exc)

        try:
            self.exchange.fetch_balance()
        except Exception as exc:
            private_ok = False
            private_error = str(exc)

        try:
            self.db.execute(
                """
                INSERT INTO bot_state(key, value, updated_at_ms)
                VALUES ('last_healthcheck', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_ms=excluded.updated_at_ms
                """,
                (
                    "ok" if (public_ok and private_ok) else ("degraded" if (public_ok or private_ok) else "fail"),
                    now_ms,
                ),
            )
        except Exception as exc:
            db_ok = False
            db_error = str(exc)

        # Execution açısından private endpoint daha yük taşıyan sinyaldir.
        # Public fail + private ok => DEGRADED. Burada sistemi gereksiz yere kırmızıya boyamıyoruz.
        exchange_ok = private_ok
        if db_ok and private_ok and public_ok:
            severity = "OK"
        elif db_ok and private_ok:
            severity = "DEGRADED"
        else:
            severity = "CRITICAL"

        return {
            "ok": severity != "CRITICAL",
            "severity": severity,
            "exchange_ok": exchange_ok,
            "db_ok": db_ok,
            "public_ok": public_ok,
            "private_ok": private_ok,
            "exchange_error": private_error or public_error,
            "public_error": public_error,
            "private_error": private_error,
            "db_error": db_error,
            "timestamp_ms": now_ms,
        }
