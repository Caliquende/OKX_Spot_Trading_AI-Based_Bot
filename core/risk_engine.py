from __future__ import annotations

"""Risk yardımcıları. Pozisyon büyüklüğü / limit mantığı için tamamlayıcı katmandır."""


import time


class RiskEngine:
    """
    Minimum viable risk engine (non-invasive).

    Amaç:
    - mevcut flow'u bozmadan risk gate eklemek
    - future extensible foundation oluşturmak
    """

    def __init__(self, settings):
        # Risk sınırlarını settings içinden okuyup burada saklıyoruz.
        self.settings = settings
        self.max_total_exposure_pct = float(getattr(settings, "max_total_exposure_pct", 0.9))
        self.max_single_trade_pct = float(getattr(settings, "max_single_trade_pct", 0.1))

    def check_entry(
        self,
        symbol: str,
        portfolio: dict,
        position: dict | None,
        desired_quote: float,
        last_price: float,
        risk_limits: dict | None = None,
    ) -> tuple[bool, float, str]:
        # Yeni entry izinli mi, izinliyse maksimum ne kadar quote kullanılabilir?
        # Bu sorunun cevabını döndürür.

        total_balance = float(portfolio.get("total_balance") or 0.0)
        cash_balance = float(portfolio.get("cash_balance") or 0.0)
        min_free_usdt = float(getattr(self.settings, "min_free_usdt", 0.0) or 0.0)
        limits = risk_limits or {}
        max_single_trade_pct = float(limits.get("max_single_trade_pct", self.max_single_trade_pct))
        max_total_exposure_pct = float(limits.get("max_total_exposure_pct", self.max_total_exposure_pct))
        max_symbol_exposure_pct = float(limits.get("max_symbol_exposure_pct", self.settings.max_symbol_exposure_pct))

        if total_balance <= 0:
            return False, 0.0, "invalid_total_balance"

        # -------- 1. single trade cap --------
        max_trade_quote = total_balance * max_single_trade_pct
        cash_usable_quote = max(0.0, cash_balance - min_free_usdt)

        allowed_quote = min(desired_quote, max_trade_quote, cash_usable_quote)

        # -------- 2. total exposure cap --------
        deployed = total_balance - cash_balance
        current_exposure_pct = deployed / total_balance

        if current_exposure_pct >= max_total_exposure_pct:
            return False, 0.0, "max_total_exposure_reached"

        remaining_total_exposure_quote = max(
            0.0,
            total_balance * max_total_exposure_pct - deployed,
        )
        allowed_quote = min(allowed_quote, remaining_total_exposure_quote)

        # -------- 3. symbol exposure cap --------
        # TR: Bu sınır sadece mevcut pozisyonu olan semboller için değil,
        # yeni açılacak ilk entry için de uygulanmalıdır.
        # EN: This cap must apply not only to symbols with an existing position,
        # but also to a brand-new first entry.
        position_value = 0.0
        if position:
            position_value = float(position["qty"] or 0.0) * last_price
        remaining_symbol_quote = max(
            0.0,
            total_balance * max_symbol_exposure_pct - position_value,
        )
        allowed_quote = min(allowed_quote, remaining_symbol_quote)

        # -------- 4. min order --------
        if allowed_quote < self.settings.min_order_quote_usdt:
            return False, 0.0, "too_small_after_risk"

        return True, allowed_quote, "ok"
