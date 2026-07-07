from __future__ import annotations

"""
TP/SL ENGINE
============
Produces stop-loss and take-profit decisions while a position is open.
"""

import time
from typing import Any


class TPSLEngine:
    def __init__(self, settings, bot_state_repo):
        self.settings = settings
        self.bot_state_repo = bot_state_repo

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def _state_key(self, symbol: str) -> str:
        return f"tpsl_state:{symbol}"

    def _default_state(self, position: dict) -> dict[str, Any]:
        avg = float(position.get("avg_entry_price") or 0.0)
        return {
            "partial_tp_done": False,
            "last_avg_entry_price": avg,
            "last_qty": float(position.get("qty") or 0.0),
            "peak_price": avg,
            "peak_pnl_pct": 0.0,
            "updated_at_ms": self.now_ms(),
        }

    def clear_symbol_state(self, symbol: str) -> None:
        self.bot_state_repo.delete(self._state_key(symbol))

    def _sync_state_with_position(self, symbol: str, position: dict | None) -> dict | None:
        if not position:
            self.clear_symbol_state(symbol)
            return None

        qty = float(position.get("qty") or 0.0)
        avg = float(position.get("avg_entry_price") or 0.0)

        if qty <= 1e-12:
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

        if qty_increased or avg_changed:
            state["partial_tp_done"] = False

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
        result = {
            "triggered": False, "action": None, "reason": "tpsl_not_triggered",
            "stop_loss_price": None, "partial_take_profit_price": None, "full_take_profit_price": None,
            "pnl_pct": None, "peak_price": None, "peak_pnl_pct": None,
            "break_even_stop_price": None, "trailing_retrace_pct": None, "state": None,
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

        if qty <= 1e-12 or last_price <= 0:
            self.clear_symbol_state(symbol)
            result["reason"] = "invalid_open_position"
            return result

        state = self._sync_state_with_position(symbol, position)
        result["state"] = state

        if avg <= 0:
            result["reason"] = "avg_entry_unknown"
            return result

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

        peak_price = max(float(state.get("peak_price") or 0.0), trigger_high_price, avg)
        peak_pnl_pct = (peak_price / avg) - 1.0
        trailing_retrace_pct = max(0.0, 1.0 - (last_price / peak_price))

        state["peak_price"] = peak_price
        state["peak_pnl_pct"] = peak_pnl_pct
        state["updated_at_ms"] = self.now_ms()
        self.bot_state_repo.set(self._state_key(symbol), state, self.now_ms())

        result.update({
            "stop_loss_price": stop_loss_price,
            "partial_take_profit_price": partial_take_profit_price,
            "full_take_profit_price": full_take_profit_price,
            "pnl_pct": pnl_pct,
            "peak_price": peak_price,
            "peak_pnl_pct": peak_pnl_pct,
            "break_even_stop_price": break_even_stop_price,
            "trailing_retrace_pct": trailing_retrace_pct
        })

        if stop_loss_pct > 0 and last_price <= stop_loss_price:
            result.update({"triggered": True, "action": "FULL_CLOSE", "reason": "stop_loss_hit"})
            return result

        if break_even_enabled and break_even_activation_pct > 0:
            if peak_pnl_pct >= break_even_activation_pct and last_price <= break_even_stop_price:
                result.update({"triggered": True, "action": "FULL_CLOSE", "reason": "break_even_stop_hit"})
                return result

        partial_tp_done = bool(state.get("partial_tp_done", False))

        # Full take-profit and partial take-profit are evaluated BEFORE the
        # early-profit protection so the configured TP targets stay reachable.
        if bool(getattr(self.settings, "full_take_profit_enabled", True)):
            if full_take_profit_pct > 0 and trigger_high_price >= full_take_profit_price:
                result.update({"triggered": True, "action": "FULL_CLOSE", "reason": "full_take_profit_hit"})
                return result

        if bool(getattr(self.settings, "partial_take_profit_enabled", True)):
            if partial_take_profit_pct > 0 and not partial_tp_done and trigger_high_price >= partial_take_profit_price:
                result.update({"triggered": True, "action": "PARTIAL_CLOSE", "reason": "partial_take_profit_hit"})
                return result

        # Early profit protection (aggressive trailing).
        # Activation/giveback come from config so they can be tuned to sit
        # above the partial TP target instead of cutting winners too early.
        early_profit_activation_pct = float(getattr(self.settings, "early_profit_activation_pct", 0.04))
        early_profit_giveback_pct = float(getattr(self.settings, "early_profit_giveback_pct", 0.012))
        if peak_pnl_pct >= early_profit_activation_pct:
            if pnl_pct <= 0:
                result.update({"triggered": True, "action": "FULL_CLOSE", "reason": "profit_roundtrip_stop_hit"})
                return result
            if trailing_retrace_pct >= early_profit_giveback_pct:
                result.update({
                    "triggered": True,
                    "action": "FULL_CLOSE" if partial_tp_done else "PARTIAL_CLOSE",
                    "reason": "early_trailing_profit_hit"
                })
                return result

        if trailing_enabled and trailing_activation_pct > 0 and trailing_giveback_pct > 0:
            if peak_pnl_pct >= trailing_activation_pct and trailing_retrace_pct >= trailing_giveback_pct:
                result.update({
                    "triggered": True,
                    "action": "FULL_CLOSE" if partial_tp_done else "PARTIAL_CLOSE",
                    "reason": "trailing_take_profit_hit"
                })
                return result

        return result

    def mark_partial_take_profit_done(self, symbol: str, position: dict | None) -> None:
        if not position: return
        state = self._sync_state_with_position(symbol, position)
        if not state: return
        state["partial_tp_done"] = True
        state["updated_at_ms"] = self.now_ms()
        self.bot_state_repo.set(self._state_key(symbol), state, self.now_ms())
