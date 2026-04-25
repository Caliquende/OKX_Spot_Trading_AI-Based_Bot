from __future__ import annotations

"""
TP/SL ENGINE
============

TR:
Acik pozisyon varken stop-loss ve take-profit kararlarini uretir.
Bu katman order gondermez; karar uretir. Gonderim `main -> execution_engine` zincirinde olur.

Tasarim prensibi:
- Once hayatta kal, sonra yeni entry dusun.
Bu yuzden TP/SL kontrolu strategy signal yorumundan once calistirilir.

EN:
Produces stop-loss and take-profit decisions while a position is open.
This layer does not send orders; it only decides. Order submission happens through `main -> execution_engine`.

Design principle:
- Survive first, think about new entries second.
That is why TP/SL is evaluated before strategy signals are acted on.
"""


import time
from typing import Any


class TPSLEngine:
    """
    TR:
    TP/SL karar motoru.
    Gorevi order gondermek degil, karar uretmektir.

    Urettigi kararlar:
    - FULL_CLOSE
    - PARTIAL_CLOSE
    - triggered=False

    Bu sinifta iki kritik problem ozellikle ele alindi:
    1. avg_entry_price yoksa TP/SL zorla calistirilmiyor.
    2. Partial TP, order accepted oldugu anda tamam sayilmiyor;
       gercekten qty dustu mu diye bakiliyor.

    EN:
    TP/SL decision engine.
    Its job is not to send orders, but to produce decisions.

    Main outputs:
    - FULL_CLOSE
    - PARTIAL_CLOSE
    - triggered=False

    Two important fixes are built into this class:
    1. TP/SL is not forced when avg_entry_price is unknown.
    2. Partial TP is not considered done just because an order was accepted;
       it is considered done only when the real position quantity drops.
    """

    def __init__(self, settings, bot_state_repo):
        # TR: Ayarlar ve kucuk runtime state burada tutulur.
        # EN: Settings and small runtime state dependencies are stored here.
        self.settings = settings
        self.bot_state_repo = bot_state_repo

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def _state_key(self, symbol: str) -> str:
        # TR: Her sembol icin ayri TP/SL state anahtari.
        # EN: Separate TP/SL state key for each symbol.
        return f"tpsl_state:{symbol}"

    def _default_state(self, position: dict) -> dict[str, Any]:
        """
        Bir symbol için henüz TP/SL state'i yoksa başlangıç state'i.
        """
        return {
            "partial_tp_done": False,
            "last_avg_entry_price": float(position.get("avg_entry_price") or 0.0),
            "last_qty": float(position.get("qty") or 0.0),
            "peak_price": float(position.get("avg_entry_price") or 0.0),
            "peak_pnl_pct": 0.0,
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

        # TR: Scale-in veya yeni maliyet fazi basladiysa partial TP hakki yeniden acilmali.
        # EN: If a scale-in or a new cost phase starts, partial TP should become available again.
        if qty_increased or avg_changed:
            state["partial_tp_done"] = False

        # TR: Eski hata "partial order gonderdim, tamamlandi" varsayimiydi.
        # EN: The old mistake was assuming "partial order submitted" meant "partial TP completed".
        # TR: Dogru kural gercek qty dususune bakmaktir.
        # EN: The correct rule is to look at real quantity reduction.
        if qty_decreased and qty > 1e-12:
            state["partial_tp_done"] = True

        state["last_qty"] = qty
        state["last_avg_entry_price"] = avg
        state["updated_at_ms"] = self.now_ms()
        self.bot_state_repo.set(key, state, self.now_ms())
        return state

    def evaluate(
        self,
        symbol: str,
        position: dict | None,
        last_price: float,
        high_price: float | None = None,
    ) -> dict[str, Any]:
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
            "peak_price": None,
            "peak_pnl_pct": None,
            "break_even_stop_price": None,
            "trailing_retrace_pct": None,
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
        break_even_enabled = bool(getattr(self.settings, "break_even_stop_enabled", True))
        break_even_activation_pct = float(getattr(self.settings, "break_even_activation_pct", 0.03))
        break_even_buffer_pct = float(getattr(self.settings, "break_even_buffer_pct", 0.002))
        trailing_enabled = bool(getattr(self.settings, "trailing_take_profit_enabled", True))
        trailing_activation_pct = float(getattr(self.settings, "trailing_take_profit_activation_pct", 0.05))
        trailing_giveback_pct = float(getattr(self.settings, "trailing_take_profit_giveback_pct", 0.02))

        stop_loss_price = avg * (1.0 - stop_loss_pct)
        partial_take_profit_price = avg * (1.0 + partial_take_profit_pct)
        full_take_profit_price = avg * (1.0 + full_take_profit_pct)
        pnl_pct = (last_price / avg) - 1.0
        trigger_high_price = max(float(high_price or 0.0), last_price)
        break_even_stop_price = avg * (1.0 + break_even_buffer_pct)

        peak_price = max(
            float((state or {}).get("peak_price") or 0.0),
            trigger_high_price,
            avg,
        )
        peak_pnl_pct = (peak_price / avg) - 1.0
        trailing_retrace_pct = 0.0
        if peak_price > 0:
            trailing_retrace_pct = max(0.0, 1.0 - (last_price / peak_price))

        if state is not None:
            state["peak_price"] = peak_price
            state["peak_pnl_pct"] = peak_pnl_pct
            state["updated_at_ms"] = self.now_ms()
            self.bot_state_repo.set(self._state_key(symbol), state, self.now_ms())

        result["stop_loss_price"] = stop_loss_price
        result["partial_take_profit_price"] = partial_take_profit_price
        result["full_take_profit_price"] = full_take_profit_price
        result["pnl_pct"] = pnl_pct
        result["peak_price"] = peak_price
        result["peak_pnl_pct"] = peak_pnl_pct
        result["break_even_stop_price"] = break_even_stop_price
        result["trailing_retrace_pct"] = trailing_retrace_pct

        # TR: SL ilk sirada cunku zarar kontrolu kar kovalamaktan once gelir.
        # EN: SL comes first because loss control comes before profit chasing.
        if stop_loss_pct > 0 and last_price <= stop_loss_price:
            result["triggered"] = True
            result["action"] = "FULL_CLOSE"
            result["reason"] = "stop_loss_hit"
            return result

        # TR: Pozisyon guzel kara girdikten sonra tekrar eksiye donmesine izin vermiyoruz.
        # EN: Once the trade moved nicely into profit, we do not want to let it fall back into loss.
        if break_even_enabled and break_even_activation_pct > 0:
            if peak_pnl_pct >= break_even_activation_pct and last_price <= break_even_stop_price:
                result["triggered"] = True
                result["action"] = "FULL_CLOSE"
                result["reason"] = "break_even_stop_hit"
                return result

        # TR: 4h mumlarda loglar sıkça +2.5% civarı peak sonrası pozisyonun tekrar
        # eksiye döndüğünü gösterdi. Klasik trailing eşiği 5% ise bu kârı korumaz.
        # EN: Logs showed many +2.5% peak round-trips on 4h candles. A 5% trailing
        # threshold does not protect that profit band.
        early_profit_activation_pct = min(
            x for x in (
                partial_take_profit_pct if partial_take_profit_pct > 0 else 0.025,
                trailing_activation_pct if trailing_activation_pct > 0 else 0.025,
                0.025,
            )
        )
        early_profit_giveback_pct = min(
            trailing_giveback_pct if trailing_giveback_pct > 0 else 0.02,
            0.02,
        )
        if peak_pnl_pct >= early_profit_activation_pct and pnl_pct <= 0:
            result["triggered"] = True
            result["action"] = "FULL_CLOSE"
            result["reason"] = "profit_roundtrip_stop_hit"
            return result

        if peak_pnl_pct >= early_profit_activation_pct and trailing_retrace_pct >= early_profit_giveback_pct:
            partial_tp_done = bool((state or {}).get("partial_tp_done", False))
            result["triggered"] = True
            result["action"] = "FULL_CLOSE" if partial_tp_done else "PARTIAL_CLOSE"
            result["reason"] = "early_trailing_profit_hit"
            return result

        # TR: Full TP, partial'dan once kontrol edilir.
        # EN: Full TP is checked before partial TP.
        # TR: Fiyat bir anda full TP'ye degdiyse gereksiz partial ile vakit kaybetmeyiz.
        # EN: If price instantly reaches the full TP level, we do not waste time with a partial exit first.
        if bool(getattr(self.settings, "full_take_profit_enabled", True)):
            if full_take_profit_pct > 0 and trigger_high_price >= full_take_profit_price:
                result["triggered"] = True
                result["action"] = "FULL_CLOSE"
                result["reason"] = "full_take_profit_hit"
                return result

        partial_tp_done = bool((state or {}).get("partial_tp_done", False))
        if bool(getattr(self.settings, "partial_take_profit_enabled", True)):
            if partial_take_profit_pct > 0 and not partial_tp_done and trigger_high_price >= partial_take_profit_price:
                result["triggered"] = True
                result["action"] = "PARTIAL_CLOSE"
                result["reason"] = "partial_take_profit_hit"
                return result

        # TR: Trailing TP, spike sonrasi rollback durumunda kari kilitlemek icin vardir.
        # EN: Trailing TP exists to lock profit after a spike rolls back.
        # TR: Once iyi bir peak, sonra anlamli geri verme gorulmelidir.
        # EN: First we need a meaningful peak, then a meaningful giveback from that peak.
        if trailing_enabled and trailing_activation_pct > 0 and trailing_giveback_pct > 0:
            if peak_pnl_pct >= trailing_activation_pct and trailing_retrace_pct >= trailing_giveback_pct:
                result["triggered"] = True
                result["action"] = "FULL_CLOSE" if partial_tp_done else "PARTIAL_CLOSE"
                result["reason"] = "trailing_take_profit_hit"
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
