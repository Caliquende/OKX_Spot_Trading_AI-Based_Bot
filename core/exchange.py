from __future__ import annotations

"""
EXCHANGE ADAPTER
================

Bu dosya ccxt/OKX benzeri dış dünya ile bot arasındaki adaptasyon katmanıdır.
Amaç raw response karmaşasını botun geri kalanına sızdırmamaktır.
"""


import os
import time
import uuid
from typing import Any
import logging
import ccxt

os.makedirs("logs", exist_ok=True)
_debug_file = open("logs/okx_debug.log", "a")

def _debug_log(msg: str) -> None:
    # Borsa adaptörünün ham debug notlarını ayrı dosyaya yazar.
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {msg}\n"
    _debug_file.write(line)
    _debug_file.flush()

logger = logging.getLogger(__name__)


class OKXExchange:
    """
    Exchange adapter.

    Bu katmanın görevi:
    - Botun geri kalanını ccxt detaylarından korumak
    - Tekrarlanan raw balance parse saçmalığını tek yere toplamak
    - Order gönderirken doğru OKX params setini kullanmak

    NOT:
    Eski versiyonda botun farklı katmanları balance'ı kendi kafasına göre parse ediyordu.
    Bu hem sandbox hem live tarafta tutarsızlığa yol açtı. Bu yüzden asset free/total helper'ları eklendi.
    """

    def __init__(self, api_key: str, secret: str, passphrase: str, sandbox: bool):
        self.ex = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": passphrase,
                "enableRateLimit": True,
                "timeout": 20000,
                "options": {},
            }
        )
        self.ex.set_sandbox_mode(sandbox)
        _debug_log(f"[INIT] sandbox={sandbox}")
        self.ex.load_markets()

        # main.py tarafında settings inject edilir.
        # Reconciler ve diğer katmanlar gerekirse buradan ayarlara bakabilir.
        self.settings = None
        self._okx_account_config_cache: dict[str, Any] | None = None

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def load_markets(self) -> dict[str, Any]:
        return self.ex.load_markets()

    def market(self, symbol: str) -> dict[str, Any]:
        return self.ex.market(symbol)

    def fetch_balance(self) -> dict[str, Any]:
        # Ham balance verisini borsadan getirir.
        last_exc: Exception | None = None
        retryable_errors = (
            ccxt.OnMaintenance,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
            ccxt.NetworkError,
            ccxt.DDoSProtection,
            ccxt.RateLimitExceeded,
        )
        for attempt in range(1, 4):
            try:
                return self.ex.fetch_balance()
            except retryable_errors as exc:
                last_exc = exc
                if attempt >= 3:
                    break
                logger.warning("[BALANCE RETRY] attempt=%s err=%s", attempt, exc)
                time.sleep(2 * attempt)

        if last_exc is not None:
            raise last_exc
        return self.ex.fetch_balance()

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return self.ex.fetch_ticker(symbol)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        return self.ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.ex.fetch_open_orders(symbol)

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.ex.fetch_order(order_id, symbol)

    def fetch_order_by_client_id(self, client_order_id: str, symbol: str) -> dict[str, Any]:
        return self.ex.fetch_order(id=None, symbol=symbol, params={"clOrdId": client_order_id})

    def fetch_my_trades(self, symbol: str, since: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.ex.fetch_my_trades(symbol, since=since, limit=limit)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.ex.amount_to_precision(symbol, amount))

    def price_to_precision(self, symbol: str, price: float) -> float:
        return float(self.ex.price_to_precision(symbol, price))

    def cost_to_precision(self, symbol: str, cost: float) -> float:
        return float(self.ex.cost_to_precision(symbol, cost))

    def min_amount(self, symbol: str) -> float:
        # Borsanın o sembolde izin verdiği minimum miktarı döndürür.
        market = self.market(symbol)
        return float((market.get("limits") or {}).get("amount", {}).get("min") or 0.0)

    def min_quote_amount(self, symbol: str, last_price: float = 0.0) -> float:
        """
        Returns the minimum quote value needed for an order when it can be inferred.

        OKX spot instruments expose minSz as base currency size. For quote-based
        market buys we still need enough quote to buy at least that base size.
        """
        market = self.market(symbol) or {}
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        info = market.get("info") or {}

        min_quote = 0.0
        for raw_cost in (cost_limits.get("min"), info.get("minQuoteSz")):
            try:
                min_quote = max(min_quote, float(raw_cost or 0.0))
            except Exception:
                pass

        min_base = 0.0
        for raw_amount in (amount_limits.get("min"), info.get("minSz")):
            try:
                min_base = max(min_base, float(raw_amount or 0.0))
            except Exception:
                pass

        price = float(last_price or 0.0)
        if min_base > 0 and price > 0:
            min_quote = max(min_quote, min_base * price)

        return min_quote

    def _asset_info_from_balance(self, asset: str, balance: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        ccxt balance response exchange'e göre küçük sapmalar gösterebiliyor.
        Bu helper free/used/total erişimini tek yere toplar.
        """
        if balance is None:
            balance = self.fetch_balance()

        direct = balance.get(asset)
        if isinstance(direct, dict):
            return direct

        free_map = balance.get("free") or {}
        used_map = balance.get("used") or {}
        total_map = balance.get("total") or {}
        parsed = {
            "free": free_map.get(asset, 0.0),
            "used": used_map.get(asset, 0.0),
            "total": total_map.get(asset, 0.0),
        }
        if any(float(parsed.get(key) or 0.0) for key in ("free", "used", "total")):
            return parsed

        # OKX raw balance can carry the real asset rows under info.data[].details[]
        # even when ccxt's normalized maps are empty for a currency.
        info = balance.get("info") or {}
        data = info.get("data") or []
        if isinstance(data, list):
            for account in data:
                details = (account or {}).get("details") or []
                if not isinstance(details, list):
                    continue
                for detail in details:
                    if str((detail or {}).get("ccy") or "").upper() != asset.upper():
                        continue
                    total = (
                        detail.get("eq")
                        or detail.get("cashBal")
                        or detail.get("bal")
                        or detail.get("availBal")
                        or 0.0
                    )
                    free = detail.get("availBal") or detail.get("availEq") or total
                    used = detail.get("frozenBal") or detail.get("ordFrozen") or 0.0
                    return {"free": free, "used": used, "total": total}

        return parsed

    def get_asset_free(self, asset: str, balance: dict[str, Any] | None = None) -> float:
        info = self._asset_info_from_balance(asset, balance=balance)
        return float(info.get("free") or 0.0)

    def get_asset_total(self, asset: str, balance: dict[str, Any] | None = None) -> float:
        info = self._asset_info_from_balance(asset, balance=balance)
        return float(info.get("total") or 0.0)

    def fetch_easy_convert_currency_list(self, source: str = "1") -> dict[str, Any]:
        return self.ex.privateGetTradeEasyConvertCurrencyList({"source": str(source)})

    def easy_convert(self, from_ccy: list[str], to_ccy: str = "USDT", source: str = "1") -> dict[str, Any]:
        payload = {
            "fromCcy": [str(ccy).upper() for ccy in from_ccy],
            "toCcy": str(to_ccy).upper(),
            "source": str(source),
        }
        return self.ex.privatePostTradeEasyConvert(payload)

    def _fetch_okx_account_config(self) -> dict[str, Any] | None:
        if self._okx_account_config_cache is not None:
            return self._okx_account_config_cache

        for attr in ("private_get_account_config", "privateGetAccountConfig"):
            fn = getattr(self.ex, attr, None)
            if not callable(fn):
                continue
            try:
                resp = fn()
                data = (resp or {}).get("data") or []
                if data and isinstance(data, list):
                    self._okx_account_config_cache = data[0] or {}
                else:
                    self._okx_account_config_cache = {}
                _debug_log(f"[ACCOUNT CONFIG] {self._okx_account_config_cache}")
                return self._okx_account_config_cache
            except Exception as exc:
                _debug_log(f"[ACCOUNT CONFIG ERROR] attr={attr} err={exc}")

        self._okx_account_config_cache = {}
        return self._okx_account_config_cache

    def _configured_td_mode(self) -> str:
        raw = str(getattr(self.settings, "okx_td_mode", "auto") or "auto").strip().lower()
        if raw in {"cash", "cross", "isolated", "auto"}:
            return raw
        return "auto"

    def _infer_td_mode_from_account_config(self, symbol: str) -> str | None:
        market = self.market(symbol) or {}
        is_spot = bool(market.get("spot")) or str(market.get("type") or "").lower() == "spot"
        config = self._fetch_okx_account_config() or {}
        acct_lv = str(config.get("acctLv") or "").strip()

        # OKX docs:
        # acctLv 1 -> spot mode
        # acctLv 2 -> futures mode
        # acctLv 3 -> multi-currency margin
        # acctLv 4 -> portfolio margin
        if acct_lv in {"1", "2"}:
            return "cash" if is_spot else "cross"
        if acct_lv in {"3", "4"}:
            return "cross"
        return None

    def _candidate_td_modes(self, symbol: str) -> list[str]:
        # OKX bazen tdMode konusunda hassastır.
        # Bu yüzden olası modları bir sırayla deneriz.
        market = self.market(symbol) or {}
        is_spot = bool(market.get("spot")) or str(market.get("type") or "").lower() == "spot"

        configured = self._configured_td_mode()
        if configured != "auto":
            return [configured]

        preferred = self._infer_td_mode_from_account_config(symbol)
        if preferred:
            candidates = [preferred]
        else:
            candidates = ["cash" if is_spot else "cross"]

        if is_spot:
            fallbacks = ["cash", "cross", "isolated"]
        else:
            fallbacks = ["cross", "isolated"]

        for mode in fallbacks:
            if mode not in candidates:
                candidates.append(mode)
        return candidates

    def _is_td_mode_error(self, exc: Exception | str) -> bool:
        text = str(exc)
        lowered = text.lower()
        return "tdmode" in lowered or ("51000" in text and "parameter" in lowered)

    def _create_order_with_td_mode_retry(
        self,
        *,
        symbol: str,
        order_type: str,
        side: str,
        amount: float | None,
        price: float | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        # Order gönderirken farklı tdMode seçenekleri denenir.
        # Amaç "yanlış mod yüzünden order gitmedi" problemini azaltmak.
        last_exc: Exception | None = None
        candidates = self._candidate_td_modes(symbol)

        for td_mode in candidates:
            attempt_params = dict(params)
            attempt_params["tdMode"] = td_mode
            _debug_log(
                f"[ORDER TRY] symbol={symbol} side={side} type={order_type} tdMode={td_mode} amount={amount} price={price} params={attempt_params}"
            )
            try:
                result = self.ex.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=attempt_params,
                )
                _debug_log(f"[ORDER RESP] symbol={symbol} side={side} tdMode={td_mode} result={result}")
                return result
            except Exception as exc:
                last_exc = exc
                _debug_log(f"[ORDER ERROR] symbol={symbol} side={side} tdMode={td_mode} error={exc}")
                if not self._is_td_mode_error(exc):
                    raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"order submission failed without concrete exception: {symbol} {side}")

    def create_market_buy_by_quote(self, symbol: str, quote_amount: float, client_order_id: str) -> dict[str, Any]:
        """
        Spot market buy için quote bazlı alış.
        OKX'te market buy için kritik nokta tgtCcy ve sz'nin quote olarak verilmesidir.
        """
        quote_amount = self.cost_to_precision(symbol, quote_amount)
        params = {
            "clOrdId": client_order_id,
            "tgtCcy": "quote_ccy",
            "sz": str(quote_amount),
        }
        logger.info("[OKX BUY PARAMS] symbol=%s quote_amount=%s td_mode_candidates=%s", symbol, quote_amount, self._candidate_td_modes(symbol))
        return self._create_order_with_td_mode_retry(
            symbol=symbol,
            order_type="market",
            side="buy",
            amount=None,
            price=None,
            params=params,
        )

    def create_market_sell_by_base(self, symbol: str, base_amount: float, client_order_id: str) -> dict[str, Any]:
        """
        Spot market sell için base bazlı satış.
        """
        base_amount = self.amount_to_precision(symbol, base_amount)
        params = {
            "clOrdId": client_order_id,
            "tgtCcy": "base_ccy",
        }
        logger.info("[OKX SELL PARAMS] symbol=%s base_amount=%s td_mode_candidates=%s", symbol, base_amount, self._candidate_td_modes(symbol))
        return self._create_order_with_td_mode_retry(
            symbol=symbol,
            order_type="market",
            side="sell",
            amount=base_amount,
            price=None,
            params=params,
        )

    @staticmethod
    def make_client_order_id(symbol: str, side: str) -> str:
        # Bizim tarafta takip edilecek benzersiz order id üretir.
        base = symbol.replace("/", "")[:6]
        side_flag = "B" if side.lower() == "buy" else "S"
        uid = uuid.uuid4().hex[:12]
        return f"{base}{side_flag}{uid}"

    def fetch_position(self, symbol: str) -> dict[str, Any] | None:
        try:
            position = self.ex.fetch_position(symbol)
            return position
        except Exception:
            try:
                params = {"instId": symbol.replace("/", "-")}
                position = self.ex.fetch_positions([symbol], params=params)
                if position:
                    return position[0]
            except Exception:
                pass
            return None
