from __future__ import annotations

"""
RECONCILER
==========
Accounting layer for syncing order status, fetching trades, and rebuilding positions.
"""

import logging
import time

class Reconciler:
    def __init__(self, exchange, orders_repo, fills_repo, positions_repo, bot_state_repo):
        self.exchange = exchange
        self.orders_repo = orders_repo
        self.fills_repo = fills_repo
        self.positions_repo = positions_repo
        self.bot_state_repo = bot_state_repo

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def sync_symbol(self, symbol: str) -> None:
        self._sync_orders(symbol)
        self._sync_trades(symbol)
        self._rebuild_position(symbol)

    def _settings(self):
        return getattr(self.exchange, "settings", None)

    def _reconcile_anchor_key(self, symbol: str) -> str:
        return f"reconcile_anchor:{symbol}"

    def _reconcile_mismatch_key(self, symbol: str) -> str:
        return f"reconcile_mismatch_count:{symbol}"

    def _set_reconcile_anchor(self, *, symbol: str, qty: float, avg_entry_price: float, realized_pnl_quote: float, fees_quote: float, reason: str) -> None:
        last_fill = self.fills_repo.latest_for_symbol(symbol)
        payload = {
            "qty": float(qty or 0.0), "avg_entry_price": float(avg_entry_price or 0.0),
            "realized_pnl_quote": float(realized_pnl_quote or 0.0), "fees_quote": float(fees_quote or 0.0),
            "last_fill_ts_ms": int((last_fill or {}).get("timestamp_ms") or 0),
            "last_trade_id": str((last_fill or {}).get("trade_id") or ""),
            "reason": reason, "updated_at_ms": self.now_ms(),
        }
        self.bot_state_repo.set(self._reconcile_anchor_key(symbol), payload, self.now_ms())
        self.bot_state_repo.delete(self._reconcile_mismatch_key(symbol))

    def _register_mismatch(self, symbol: str) -> int:
        key = self._reconcile_mismatch_key(symbol)
        current = int(self.bot_state_repo.get(key) or 0) + 1
        self.bot_state_repo.set(key, current, self.now_ms())
        return current

    def _clear_mismatch(self, symbol: str) -> None:
        self.bot_state_repo.delete(self._reconcile_mismatch_key(symbol))

    def _sync_orders(self, symbol: str) -> None:
        rows = self.orders_repo.get_sync_candidates(symbol)
        for row in rows:
            client_order_id = row["client_order_id"]
            exchange_order_id = row["exchange_order_id"]
            if self.orders_repo.is_ghost_cleaned(client_order_id): continue
            try:
                if exchange_order_id: order = self.exchange.fetch_order(exchange_order_id, symbol)
                else: order = self.exchange.fetch_order_by_client_id(client_order_id, symbol)
                if not order: continue
                status = str(order.get("status") or "open").upper()
                self.orders_repo.update_status(client_order_id, order.get("id") or exchange_order_id, status, self.now_ms())
            except Exception as exc:
                err_text = str(exc)
                if any(x in err_text for x in ["51603", "Order does not exist", "order not found"]):
                    self.orders_repo.mark_ghost_cleaned(client_order_id, exchange_order_id, self.now_ms(), "ghost_order_not_found")
                logging.warning(f"[ORDER SYNC ERROR] {symbol} {client_order_id} err={exc}")

    def _fetch_exchange_trades(self, symbol: str) -> list[dict]:
        all_trades, seen_ids, since = [], set(), None
        settings = self._settings()
        since = int(getattr(settings, "reconcile_since_ms", 0) or 0)
        for _ in range(10):
            try:
                trades = self.exchange.fetch_my_trades(symbol, since=since, limit=100)
                if not trades: break
                new_count = 0
                for t in sorted(trades, key=lambda x: x["timestamp"]):
                    tid = str(t.get("id"))
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid); all_trades.append(t); new_count += 1
                if new_count == 0: break
                since = trades[-1]["timestamp"] + 1
                if len(trades) < 100: break
            except Exception as e:
                logging.warning(f"[TRADES FETCH ERROR] {symbol} err={e}"); break
        return all_trades

    def _apply_fill_accounting(self, *, side, fill_qty, fill_price, fill_cost, fee_cost, fee_ccy, base_asset, quote_asset, current_qty, current_avg):
        qty, avg_entry, realized_pnl, fee_quote = float(current_qty or 0.0), float(current_avg or 0.0), 0.0, 0.0
        if fee_ccy == quote_asset: fee_quote = float(fee_cost or 0.0)
        elif fee_ccy == base_asset: fee_quote = float(fee_cost or 0.0) * float(fill_price or 0.0)

        if side == "buy":
            eff_qty = float(fill_qty or 0.0)
            if fee_ccy == base_asset: eff_qty = max(0.0, eff_qty - float(fee_cost or 0.0))
            total_cost = qty * avg_entry + float(fill_cost or 0.0)
            qty += eff_qty
            avg_entry = total_cost / qty if qty > 1e-12 else 0.0
        elif side == "sell" and qty > 1e-12:
            close_qty = min(qty, float(fill_qty or 0.0))
            realized_pnl = close_qty * (float(fill_price or 0.0) - avg_entry) - fee_quote
            qty -= close_qty
            if qty <= 1e-12: qty, avg_entry = 0.0, 0.0
        return qty, avg_entry, realized_pnl, fee_quote

    def _refresh_fill_realized_pnl(self, symbol: str) -> None:
        fills = sorted(self.fills_repo.by_symbol(symbol), key=lambda x: (x["timestamp_ms"], x["trade_id"]))
        base_asset, quote_asset = symbol.split("/")
        qty, avg_entry = 0.0, 0.0
        for f in fills:
            qty, avg_entry, pnl, _ = self._apply_fill_accounting(
                side=str(f["side"]).lower(), fill_qty=f["qty"], fill_price=f["price"], fill_cost=f["cost"],
                fee_cost=f["fee_cost"], fee_ccy=f.get("fee_currency"), base_asset=base_asset, quote_asset=quote_asset,
                current_qty=qty, current_avg=avg_entry
            )
            self.fills_repo.update_realized_pnl_quote(str(f["trade_id"]), float(pnl))

    def _sync_trades(self, symbol: str) -> None:
        trades = self._fetch_exchange_trades(symbol)
        for t in trades:
            trade_id = str(t.get("id") or "").strip()
            if not trade_id or self.fills_repo.exists(trade_id): continue
            _exit_reason = None
            if str(t.get("side")).lower() == "sell":
                cl_id = (t.get("info") or {}).get("clOrdId") or (t.get("info") or {}).get("clientOrderId")
                pending = self.bot_state_repo.get(f"pending_exit_reason:{symbol}")
                if isinstance(pending, dict) and pending.get("client_order_id") == cl_id:
                    _exit_reason = pending.get("reason")
                    self.bot_state_repo.delete(f"pending_exit_reason:{symbol}")
            self.fills_repo.insert(trade_id=trade_id, order_id=t.get("order"), client_order_id=cl_id if 'cl_id' in locals() else None,
                                  symbol=symbol, side=str(t.get("side")).lower(), qty=float(t["amount"]), price=float(t["price"]),
                                  cost=float(t["cost"]), fee_cost=float(t["fee"]["cost"]), fee_currency=t["fee"]["currency"],
                                  timestamp_ms=int(t["timestamp"]), realized_pnl_quote=0.0, exit_reason=_exit_reason)
        self._refresh_fill_realized_pnl(symbol)

    def _build_from_fills(self, symbol: str) -> tuple[float, float, float, float]:
        fills = sorted(self.fills_repo.by_symbol(symbol), key=lambda x: (x["timestamp_ms"], x["trade_id"]))
        base_asset, quote_asset = symbol.split("/")
        anchor = self.bot_state_repo.get(self._reconcile_anchor_key(symbol))
        qty, avg_entry, realized, fees = float((anchor or {}).get("qty") or 0.0), float((anchor or {}).get("avg_entry_price") or 0.0), float((anchor or {}).get("realized_pnl_quote") or 0.0), float((anchor or {}).get("fees_quote") or 0.0)
        a_ts, a_id, consumed = int((anchor or {}).get("last_fill_ts_ms") or 0), str((anchor or {}).get("last_trade_id") or ""), anchor is None
        for f in fills:
            if not consumed:
                if f["timestamp_ms"] < a_ts: continue
                if f["timestamp_ms"] == a_ts and f["trade_id"] == a_id: consumed = True; continue
                consumed = True
            qty, avg_entry, pnl, f_q = self._apply_fill_accounting(side=str(f["side"]).lower(), fill_qty=f["qty"], fill_price=f["price"], fill_cost=f["cost"], fee_cost=f["fee_cost"], fee_ccy=f["fee_currency"], base_asset=base_asset, quote_asset=quote_asset, current_qty=qty, current_avg=avg_entry)
            realized += pnl; fees += f_q
        return qty, avg_entry, realized, fees

    def _safe_last_price(self, symbol: str, f_avg: float, e_avg: float) -> float:
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if float(ticker.get("last") or 0) > 0: return float(ticker["last"])
        except: pass
        return f_avg if f_avg > 0 else (e_avg if e_avg > 0 else 0.0)

    def _value(self, qty: float, ref_price: float) -> float:
        return qty * ref_price if qty > 0 and ref_price > 0 else 0.0

    def _meaningful_mismatch(self, f_qty, b_total, ref, warn_abs, warn_ratio) -> bool:
        diff = abs(f_qty - b_total)
        return diff > 0 and (diff * ref >= warn_abs) and (diff / max(abs(f_qty), abs(b_total), 1e-12) >= warn_ratio)

    def _rebuild_position(self, symbol: str) -> None:
        s = self._settings()
        mode = str(getattr(s, "position_source_mode", "hybrid")).lower()
        pres_err = bool(getattr(s, "preserve_position_on_balance_fetch_error", True))
        w_abs = float(getattr(s, "reconcile_warn_abs_quote_usdt", 5.0))
        w_rat = float(getattr(s, "reconcile_warn_ratio", 0.10))
        epsilon = max(float(getattr(s, "reconcile_epsilon", 1e-12) or 1e-12), 1e-12)

        f_qty, f_avg, realized, fees = self._build_from_fills(symbol)
        existing = self.positions_repo.get(symbol) or {}
        e_avg, e_qty = float(existing.get("avg_entry_price") or 0), float(existing.get("qty") or 0)
        
        try:
            bal = self.exchange.fetch_balance()
            b_total = float(self.exchange.get_asset_total(symbol.split("/")[0], balance=bal))
        except: b_total = None

        ref = self._safe_last_price(symbol, f_avg, e_avg)
        
        final_qty, final_avg = 0.0, 0.0
        final_status = "CLOSED"
        if mode == "fills":
            if f_qty > epsilon:
                final_qty, final_avg, final_status = f_qty, f_avg, "OPEN"
        elif mode == "balance":
            if b_total and b_total > epsilon:
                final_qty, final_avg = b_total, (f_avg or e_avg)
                final_status = "OPEN"
        else: # HYBRID
            if b_total is None:
                if pres_err and f_qty > epsilon:
                    final_qty, final_avg, final_status = f_qty, f_avg, "OPEN"
            elif b_total > epsilon:
                final_qty, final_avg = b_total, (f_avg or e_avg)
                final_status = "OPEN"
                if self._meaningful_mismatch(f_qty, b_total, ref, w_abs, w_rat): self._register_mismatch(symbol)
                else: self._clear_mismatch(symbol)
            else:
                sbox = bool(getattr(getattr(self.exchange, "ex", None), "sandbox", False))
                sandbox_ignore_zero = bool(getattr(s, "sandbox_ignore_balance_zero", True))
                live_force_close_zero = bool(getattr(s, "live_force_close_on_zero_balance", True))
                if sbox and sandbox_ignore_zero and f_qty > epsilon:
                    final_qty, final_avg, final_status = f_qty, f_avg, "OPEN"
                elif (not sbox) and (not live_force_close_zero) and f_qty > epsilon:
                    final_qty, final_avg, final_status = f_qty, f_avg, "OPEN"
                else:
                    final_qty, final_avg = 0.0, 0.0
                    self._clear_mismatch(symbol)

        if final_qty <= epsilon:
            final_qty, final_avg, final_status = 0.0, 0.0, "CLOSED"
        
        self.positions_repo.upsert(symbol=symbol, base_asset=symbol.split("/")[0], quote_asset=symbol.split("/")[1],
                                    qty=final_qty, avg_entry_price=final_avg, realized_pnl_quote=realized,
                                    fees_quote=fees, status=final_status, now_ms=self.now_ms())
