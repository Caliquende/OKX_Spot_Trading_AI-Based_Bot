from __future__ import annotations

"""
EXECUTION ENGINE
================

Bu dosya sinyali order'a çevirir. Strateji kararı burada üretilmez; burada karar uygulanır.

Temel düzeltmeler:
- UNKNOWN_SUBMISSION state eklendi
- force buy / force sell desteği eklendi
- lock bypass force tarafında kontrollü hale getirildi
- pending/open/unknown submission aktif order kabul edildi
"""

import logging
import time


class ExecutionEngine:
    def __init__(self, settings, exchange, orders_repo, positions_repo, locks_repo, notifier):
        # Burada order göndermek için gereken bütün bağımlılıklar toplanır.
        self.settings = settings
        self.exchange = exchange
        self.orders_repo = orders_repo
        self.positions_repo = positions_repo
        self.locks_repo = locks_repo
        self.notifier = notifier

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def _resolve_cooldown_minutes(self, intent: str) -> float:
        # Aynı sembole ne kadar süre dokunmayacağımız intent'e göre değişebilir.
        intent = str(intent or "").lower()
        if intent == "scale_in":
            return float(self.settings.scale_in_cooldown_minutes)
        if intent == "exit":
            return float(self.settings.exit_cooldown_minutes)
        if intent == "unknown_submission":
            return float(getattr(self.settings, "unknown_submission_lock_minutes", 2.0))
        return float(self.settings.symbol_cooldown_minutes)

    def _is_exit_intent(self, intent: str) -> bool:
        return str(intent or "").lower() == "exit"

    def _lock_symbol(self, symbol: str, reason: str, intent: str) -> None:
        # Bir order sonrası aynı sembolde kısa süre beklemek için kilit koyuyoruz.
        now_ms = self.now_ms()
        cooldown_minutes = self._resolve_cooldown_minutes(intent)
        locked_until = now_ms + int(cooldown_minutes * 60 * 1000)
        logging.info(
            f"[LOCK SET] symbol={symbol} now={now_ms} until={locked_until} "
            f"cooldown_min={cooldown_minutes} reason={reason} intent={intent}"
        )
        self.locks_repo.lock(symbol, locked_until, reason, now_ms)

    def _notify_order_event(self, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send(text)
        except Exception:
            logging.exception("[TELEGRAM ORDER NOTIFY ERROR]")

    def can_submit(self, symbol: str, intent: str = "", bypass_lock: bool = False) -> tuple[bool, str]:
        # Bu order gerçekten gönderilebilir mi diye son kapı kontrolü.
        now_ms = self.now_ms()
        is_exit_intent = self._is_exit_intent(intent)

        has_open_order = self.orders_repo.has_pending_or_open(symbol)
        is_locked = self.locks_repo.is_locked(symbol, now_ms)
        position = self.positions_repo.get(symbol)
        has_position = bool(
            position
            and float(position["qty"] or 0.0) > 1e-12
        )

        logging.info(
            f"[CHECK] symbol={symbol} now={now_ms} intent={intent} "
            f"has_open_order={has_open_order} locked={is_locked} has_position={has_position} bypass_lock={bypass_lock}"
        )

        if has_open_order:
            return False, "pending/open/unknown_submission order exists"

        if is_locked and not is_exit_intent and not bypass_lock:
            logging.info(f"[LOCK HIT] symbol={symbol} reason=symbol cooldown active")
            return False, "symbol cooldown active"

        return True, "ok"

    def _create_pending_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        requested_qty: float | None,
        requested_quote: float | None,
        reason: str,
        now_ms: int,
    ) -> None:
        # Order daha borsaya gitmeden önce DB'de pending satırı açıyoruz.
        # Böylece gönderim sırasında hata olsa bile iz bırakmış oluyoruz.
        self.orders_repo.create_pending(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type="market",
            requested_qty=requested_qty,
            requested_quote=requested_quote,
            reason=reason,
            now_ms=now_ms,
        )

    def _is_hard_rejection(self, exc: Exception) -> bool:
        err = str(exc or "")
        lowered = err.lower()
        hard_markers = (
            "parameter tdmode error",
            "tdmode",
            '"scode":"51000"',
            "insufficient balance",
            "balance not enough",
            "insufficient",
            "parameter error",
        )
        return any(marker in lowered for marker in hard_markers)

    def _mark_unknown_submission(self, client_order_id: str, symbol: str, side: str, exc: Exception) -> dict:
        err = str(exc)

        if self._is_hard_rejection(exc):
            logging.error(f"[{side.upper()} REJECTED] symbol={symbol} err={err}")
            self.orders_repo.update_status(client_order_id, None, "REJECTED", self.now_ms())
            self._notify_order_event(f"[ORDER REJECTED] {symbol} side={side} err={err[:250]}")
            return {"ok": False, "reason": err, "status": "REJECTED", "client_order_id": client_order_id}

        logging.error(f"[{side.upper()} UNKNOWN SUBMISSION] symbol={symbol} err={err}")
        self.orders_repo.update_status(client_order_id, None, "UNKNOWN_SUBMISSION", self.now_ms())
        self._lock_symbol(symbol, f"unknown_submission:{side}", "unknown_submission")
        self._notify_order_event(f"[UNKNOWN SUBMISSION] {symbol} side={side} err={err[:250]}")
        return {"ok": False, "reason": err, "status": "UNKNOWN_SUBMISSION", "client_order_id": client_order_id}

    def place_entry(
        self,
        symbol: str,
        quote_amount: float,
        reason: str,
        intent: str = "entry",
        bypass_lock: bool = False,
    ) -> dict | None:
        # Quote bazlı market buy akışı.
        ok, why = self.can_submit(symbol, intent=intent, bypass_lock=bypass_lock)
        if not ok:
            logging.info(f"[ENTRY BLOCKED] symbol={symbol} reason={why}")
            return {"ok": False, "reason": why}

        if quote_amount < self.settings.min_order_quote_usdt:
            return {"ok": False, "reason": f"quote amount too small: {quote_amount}"}

        client_order_id = self.exchange.make_client_order_id(symbol, "buy")
        now_ms = self.now_ms()
        logging.info(f"[ENTRY TRY] symbol={symbol} quote={quote_amount:.8f} intent={intent} bypass_lock={bypass_lock}")

        self._create_pending_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            requested_qty=None,
            requested_quote=quote_amount,
            reason=reason,
            now_ms=now_ms,
        )

        if self.settings.dry_run:
            self.orders_repo.update_status(client_order_id, None, "FILLED", self.now_ms())
            logging.info(f"[DRY BUY EXECUTED] symbol={symbol}")
            self._lock_symbol(symbol, "dry_run_buy", intent)
            return {"ok": True, "dry_run": True, "client_order_id": client_order_id, "status": "FILLED"}

        try:
            resp = self.exchange.create_market_buy_by_quote(symbol, quote_amount, client_order_id)
            order_id = resp.get("id")
            status = str(resp.get("status") or "open").upper()
            logging.info(f"[BUY SENT] symbol={symbol} oid={order_id} status={status}")
            self.orders_repo.update_status(client_order_id, order_id, status, self.now_ms())
            self._lock_symbol(symbol, "live_buy", intent)
            self._notify_order_event(f"[BUY SENT] {symbol} quote={quote_amount:.4f} status={status}")
            return {
                "ok": True,
                "client_order_id": client_order_id,
                "exchange_order_id": order_id,
                "status": status,
            }
        except Exception as exc:
            return self._mark_unknown_submission(client_order_id, symbol, "buy", exc)

    def place_exit(
        self,
        symbol: str,
        base_qty: float,
        reason: str,
        intent: str = "exit",
        bypass_lock: bool = False,
    ) -> dict | None:
        # Base qty bazlı market sell akışı.
        ok, why = self.can_submit(symbol, intent=intent, bypass_lock=bypass_lock)
        if not ok:
            logging.info(f"[EXIT BLOCKED] symbol={symbol} reason={why}")
            return {"ok": False, "reason": why}

        if base_qty <= 0:
            return {"ok": False, "reason": "base_qty <= 0"}

        try:
            base_qty = self.exchange.amount_to_precision(symbol, base_qty)
        except Exception as exc:
            err = str(exc)
            logging.warning("[EXIT PRECISION BLOCKED] symbol=%s qty=%s err=%s", symbol, base_qty, err)
            return {"ok": False, "reason": f"amount_precision:{err}"}
        if base_qty <= 0:
            return {"ok": False, "reason": "base_qty precision rounded to zero"}

        client_order_id = self.exchange.make_client_order_id(symbol, "sell")
        now_ms = self.now_ms()
        logging.info(f"[EXIT TRY] symbol={symbol} qty={base_qty} intent={intent} bypass_lock={bypass_lock}")

        self._create_pending_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side="sell",
            requested_qty=base_qty,
            requested_quote=None,
            reason=reason,
            now_ms=now_ms,
        )

        if self.settings.dry_run:
            self.orders_repo.update_status(client_order_id, None, "FILLED", self.now_ms())
            logging.info(f"[DRY SELL EXECUTED] symbol={symbol}")
            self._lock_symbol(symbol, "dry_run_sell", intent)
            return {"ok": True, "dry_run": True, "client_order_id": client_order_id, "status": "FILLED"}

        try:
            resp = self.exchange.create_market_sell_by_base(symbol, base_qty, client_order_id)
            order_id = resp.get("id")
            status = str(resp.get("status") or "open").upper()
            logging.info(f"[SELL SENT] symbol={symbol} oid={order_id} status={status}")
            self.orders_repo.update_status(client_order_id, order_id, status, self.now_ms())
            self._lock_symbol(symbol, "live_sell", intent)
            self._notify_order_event(f"[SELL SENT] {symbol} qty={base_qty:.8f} status={status}")
            return {
                "ok": True,
                "client_order_id": client_order_id,
                "exchange_order_id": order_id,
                "status": status,
            }
        except Exception as exc:
            return self._mark_unknown_submission(client_order_id, symbol, "sell", exc)

    def force_buy(self, symbol: str, quote_amount: float, reason: str) -> dict | None:
        return self.place_entry(
            symbol=symbol,
            quote_amount=quote_amount,
            reason=f"force_buy {reason}",
            intent="force_buy",
            bypass_lock=True,
        )

    def force_sell(self, symbol: str, base_qty: float, reason: str) -> dict | None:
        return self.place_exit(
            symbol=symbol,
            base_qty=base_qty,
            reason=f"force_sell {reason}",
            intent="exit",
            bypass_lock=True,
        )
