from __future__ import annotations

"""
PORTFOLIO SNAPSHOT ENGINE
=========================

Bu katman toplam balance, cash balance, cash ratio, held assets gibi hızlı özet bilgileri üretir.
Telegram cycle raporunda görülen portfolio özetinin kaynağı büyük ölçüde burasıdır.
"""


from typing import Any


class PortfolioEngine:
    """
    Portföy özetini exchange balance'dan çıkarır.
    Ama kritik nokta şu:
    balance'tan pozisyon state üretmiyoruz.
    Pozisyon state DB'deki fills -> positions rebuild zincirinden gelir.
    """

    def __init__(self, exchange):
        # Exchange adapter üzerinden balance/ticker okur.
        self.exchange = exchange

    def get_snapshot(self) -> dict[str, Any]:
        # Bu fonksiyon o anki portföyün kısa özetini üretir.
        balance = self.exchange.fetch_balance()
        free_map = balance.get("free", {}) or {}
        total_map = balance.get("total", {}) or {}

        free_usdt = float(free_map.get("USDT") or 0.0)
        total_usdt_equity = 0.0
        held_assets: list[str] = []

        for coin, amount in total_map.items():
            # Her coin'in yaklaşık USDT karşılığını toplayarak toplam portföy değerini buluyoruz.
            amount = float(amount or 0.0)
            if amount <= 0:
                continue

            if coin == "USDT":
                total_usdt_equity += amount
                continue

            pair = f"{coin}/USDT"
            try:
                ticker = self.exchange.fetch_ticker(pair)
                last = float(ticker.get("last") or 0.0)
                total_usdt_equity += amount * last
                if amount > 0:
                    held_assets.append(coin)
            except Exception:
                # USDT paritesi olmayan veya fetch_ticker patlayan asset'leri sessizce geç.
                continue

        cash_ratio = free_usdt / total_usdt_equity if total_usdt_equity > 0 else 0.0
        return {
            "total_balance": round(total_usdt_equity, 4),
            "cash_balance": round(free_usdt, 4),
            "cash_ratio": round(cash_ratio, 6),
            "held_assets": sorted(set(held_assets)),
        }
