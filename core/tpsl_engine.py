from __future__ import annotations

"""
TP/SL ENGINE
============

Açık pozisyon varken stop-loss ve take-profit kararlarını üretir.
Bu katman order göndermez; karar üretir. Gönderim main -> execution_engine zincirinde olur.

Tasarım prensibi:
- Önce hayatta kal, sonra yeni entry düşün.
Bu yüzden TP/SL kontrolü strategy signal yorumundan önce çalıştırılır.
"""


import time
from typing import Any


class TPSLEngine:
    """
    TP/SL karar motoru.

    Bu motorun görevi order göndermek değil, karar üretmektir.
    Order gönderimi main.py -> ExecutionEngine zincirinde yapılır.

    Ürettiği kararlar:
    - FULL_CLOSE   -> tam satış
    - PARTIAL_CLOSE -> kısmi satış
    - triggered=False -> hiçbir şey yapma

    Bu sınıfta özellikle iki büyük problem çözüldü:

    1. avg_entry_price yoksa TP/SL zorla çalıştırılmıyor.
       Çünkü avg yokken TP/SL hesaplamak finansal olarak çöptür.

    2. Partial TP, order accepted olduğu anda "done" sayılmıyor.
       Bu eski yaklaşım yanlıştı.
       Doğru yaklaşım: gerçekten qty düşmüş mü, ona bak.
       Çünkü order accepted -> fill oldu demek değildir.
    """

    def __init__(self, settings, bot_state_repo):
        # Ayarlar ve küçük runtime state burada kullanılır.
        self.settings = settings
        self.bot_state_repo = bot_state_repo

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def _state_key(self, symbol: str) -> str:
        # Her sembol için ayrı TP/SL state anahtarı.
        return f"tpsl_state:{symbol}"

    def _default_state(self, position: dict) -> dict[str, Any]:
        """
        Bir symbol için henüz TP/SL state'i yoksa başlangıç state'i.
        """
        return {
            "partial_tp_done": False,
            "last_avg_entry_price": float(position.get("avg_entry_price") or 0.0),
            "last_qty": float(position.get("qty") or 0.0),
            "updated_at_ms": self.now_ms(),
        }

    def clear_symbol_state(self, symbol: str) -> None:
        """
        Pozisyon kapandıysa ya da geçersizse symbol state'ini temizler.
        """
        self.bot_state_repo.delete(self._state_key(symbol))

    def _sync_state_with_position(self, symbol: str, position: dict | None) -> dict | None:
        """
        DB'deki açık pozisyon ile TP/SL iç state'ini senkronlar.

        Kritik mantık:
        - qty arttıysa -> büyük ihtimal scale-in oldu -> partial TP hakkını resetle
        - avg değiştiyse -> maliyet değişti -> partial TP hakkını resetle
        - qty düştüyse -> büyük ihtimal partial gerçekleşti -> partial_tp_done=True yap

        Buradaki felsefe:
        State'i order response'a göre değil, GERÇEK pozisyon değişimine göre yönet.
        """
        if not position:
            self.clear_symbol_state(symbol)
            return None

        qty = float(position.get("qty") or 0.0)
        avg = float(position.get("avg_entry_price") or 0.0)
        status = str(position.get("status") or "").upper()

        if status != "OPEN" or qty <= 1e-12:
            self.clear_symbol_state(symbol)
            return None

        key = self._state_key(symbol)
        state = self.bot_state_repo.get(key)

        if not isinstance(state, dict):
            state = self._default_state(position)
            self.bot_state_repo.set(key, state, self.now_ms())
            return state

        prev_qty = float(state.get("last_qty") or 0.0)
        prev_avg = float(state.get("last_avg_entry_price") or 0.0)

        qty_increased = qty > prev_qty + 1e-12
        qty_decreased = qty < prev_qty - 1e-12
        avg_changed = prev_avg > 0 and avg > 0 and abs(avg - prev_avg) / max(prev_avg, 1e-12) > 1e-9

        # Scale-in veya yeni maliyet fazı başladıysa partial TP tekrar kullanılabilir olmalı.
        if qty_increased or avg_changed:
            state["partial_tp_done"] = False

        # Eski hata:
        # "partial order gönderdim, done oldu" demekti.
        # Doğrusu:
        # gerçekten qty azaldıysa done kabul et.
        if qty_decreased and qty > 1e-12:
            state["partial_tp_done"] = True

        state["last_qty"] = qty
        state["last_avg_entry_price"] = avg
        state["updated_at_ms"] = self.now_ms()
        self.bot_state_repo.set(key, state, self.now_ms())
        return state

    def evaluate(self, symbol: str, position: dict | None, last_price: float) -> dict[str, Any]:
        """
        Son fiyat ve mevcut pozisyona göre TP/SL kararı üretir.

        Dönüş formatı intentionally detaylı:
        - triggered
        - action
        - reason
        - sl/ptp/ftp seviyeleri
        - pnl_pct
        - state snapshot

        Böylece log'tan neden tetiklenmediğini kör olmadan okuyabiliyoruz.
        """
        result = {
            "triggered": False,
            "action": None,
            "reason": "tpsl_not_triggered",
            "stop_loss_price": None,
            "partial_take_profit_price": None,
            "full_take_profit_price": None,
            "pnl_pct": None,
            "state": None,
        }

        if not bool(getattr(self.settings, "tpsl_enabled", True)):
            result["reason"] = "tpsl_disabled"
            return result

        if not position:
            self.clear_symbol_state(symbol)
            result["reason"] = "no_position"
            return result

        qty = float(position.get("qty") or 0.0)
        avg = float(position.get("avg_entry_price") or 0.0)
        status = str(position.get("status") or "").upper()

        if status != "OPEN" or qty <= 1e-12 or last_price <= 0:
            self.clear_symbol_state(symbol)
            result["reason"] = "invalid_open_position"
            return result

        if avg <= 0:
            state = self._sync_state_with_position(symbol, position)
            result["state"] = state
            result["reason"] = "avg_entry_unknown"
            return result

        state = self._sync_state_with_position(symbol, position)
        result["state"] = state

        stop_loss_pct = float(self.settings.stop_loss_pct)
        partial_take_profit_pct = float(self.settings.partial_take_profit_pct)
        full_take_profit_pct = float(self.settings.full_take_profit_pct)

        stop_loss_price = avg * (1.0 - stop_loss_pct)
        partial_take_profit_price = avg * (1.0 + partial_take_profit_pct)
        full_take_profit_price = avg * (1.0 + full_take_profit_pct)
        pnl_pct = (last_price / avg) - 1.0

        result["stop_loss_price"] = stop_loss_price
        result["partial_take_profit_price"] = partial_take_profit_price
        result["full_take_profit_price"] = full_take_profit_price
        result["pnl_pct"] = pnl_pct

        # SL ilk sırada. Çünkü zarar kontrolü kâr kovalamaktan önce gelir.
        if stop_loss_pct > 0 and last_price <= stop_loss_price:
            result["triggered"] = True
            result["action"] = "FULL_CLOSE"
            result["reason"] = "stop_loss_hit"
            return result

        # Full TP, partial'dan önce bakılıyor.
        # Sebep:
        # fiyat bir anda full TP seviyesine geldiyse gereksiz partial ile oyalamıyoruz.
        if bool(getattr(self.settings, "full_take_profit_enabled", True)):
            if full_take_profit_pct > 0 and last_price >= full_take_profit_price:
                result["triggered"] = True
                result["action"] = "FULL_CLOSE"
                result["reason"] = "full_take_profit_hit"
                return result

        partial_tp_done = bool((state or {}).get("partial_tp_done", False))
        if bool(getattr(self.settings, "partial_take_profit_enabled", True)):
            if partial_take_profit_pct > 0 and not partial_tp_done and last_price >= partial_take_profit_price:
                result["triggered"] = True
                result["action"] = "PARTIAL_CLOSE"
                result["reason"] = "partial_take_profit_hit"
                return result

        return result

    def mark_partial_take_profit_done(self, symbol: str, position: dict | None) -> None:
        """
        Bu metod geriye dönük uyumluluk için tutuldu.

        Ana mantık artık qty düşüşünden partial gerçekleştiğini anlamak olduğu için
        bu metodu main akışında zorunlu kullanmıyoruz.
        Ama dışarıdan bir yerde explicit state güncellemek gerekirse güvenli şekilde çalışır.
        """
        if not position:
            return

        state = self._sync_state_with_position(symbol, position)
        if not state:
            return

        state["partial_tp_done"] = True
        state["updated_at_ms"] = self.now_ms()
        self.bot_state_repo.set(self._state_key(symbol), state, self.now_ms())
