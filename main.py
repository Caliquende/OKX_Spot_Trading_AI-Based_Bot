from __future__ import annotations

"""
ANA BOT AKIŞI
=============

Bu dosya projenin orkestra şefidir.
Tek başına bütün hesaplamaları yapmaz ama hangi parçanın ne zaman çalışacağını belirler.

Kabaca akış şöyledir:
1. Ayarlar yüklenir.
2. Exchange + DB + yardımcı motorlar hazırlanır.
3. Her cycle'da veri çekilir.
4. İndikatör + rumor + regime + risk + TP/SL birlikte yorumlanır.
5. Gerekirse order gönderilir.
6. Sonuç log'a ve rapora yazılır.

Sıfırdan bakan biri için basit özet:
- "Ne alalım / satalım?" sorusunun cevabı burada koordine edilir.
- "Gerçekten order nasıl gider?" sorusunun cevabı execution_engine'dedir.
- "Pozisyon gerçekten açık mı?" sorusunun cevabı reconciler + DB tarafındadır.
"""


import logging
import os
import time
import traceback
import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.rumor_analyzer import aggregate_rumor, clear_refresh_state, refresh_all_if_needed, get_cached_rumors, update_thresholds_if_needed

try:
    from analysis.rumor_analyzer import get_last_bulk_refresh_model, get_last_threshold_update_model, get_last_llm_models
except Exception:
    def get_last_bulk_refresh_model() -> str | None:
        return None

    def get_last_threshold_update_model() -> str | None:
        return None

    def get_last_llm_models() -> dict[str, str | None]:
        return {
            "bulk_refresh": None,
            "threshold_update": None,
        }
from config.settings import settings
from core.exchange import OKXExchange
from core.execution_engine import ExecutionEngine
from core.health_checker import HealthChecker
from core.portfolio_engine import PortfolioEngine
from core.reconciler import Reconciler
from core.risk_engine import RiskEngine
from core.tpsl_engine import TPSLEngine
from db.database import Database
from db.repositories import OrdersRepo, FillsRepo, PositionsRepo, LocksRepo, BotStateRepo
from indicators.indicator_engine import calculate_indicators
from reporting.telegram_bot import TelegramNotifier
from strategy.regime_engine import detect_market_regime, resolve_regime_params
from strategy.scoring_engine import calculate_indicator_score, evaluate_signal

LOCAL_TZ = ZoneInfo("Europe/Istanbul")


def setup_logging() -> None:
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    main_log_file = os.path.join(log_dir, "bot.log")

    log_level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    if root_logger.handlers:
        root_logger.handlers.clear()

    file_handler = RotatingFileHandler(
        main_log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


setup_logging()


def build_notifier() -> TelegramNotifier | None:
    token = str(settings.telegram_token or "").strip()
    chat_id = str(settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        logging.warning("[TELEGRAM DISABLED] Missing TELEGRAM token/chat_id")
        return None
    try:
        return TelegramNotifier(token, chat_id)
    except Exception as exc:
        logging.exception("[TELEGRAM INIT ERROR] %s", exc)
        return None


def notify_safe(notifier: TelegramNotifier | None, message: str) -> None:
    if notifier is None:
        return
    try:
        notifier.send(message)
    except Exception as exc:
        logging.exception("[TELEGRAM SEND ERROR] %s", exc)


def extract_reference_price(ticker: dict) -> tuple[float, str]:
    """
    PnL ve debug çıktılarında tek fiyat kaynağı standardı kullan.

    Öncelik:
    1. mark price
    2. index price
    3. last traded price
    """
    info = ticker.get("info", {}) or {}

    candidates = [
        (info.get("markPx"), "markPx"),
        (info.get("markPrice"), "markPrice"),
        (ticker.get("mark"), "mark"),
        (info.get("idxPx"), "idxPx"),
        (info.get("indexPrice"), "indexPrice"),
        (ticker.get("last"), "last"),
    ]

    for raw_value, source in candidates:
        try:
            price = float(raw_value)
            if price > 0:
                return price, source
        except (TypeError, ValueError):
            continue

    raise ValueError("no usable reference price in ticker payload")


def ohlcv_to_df(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").sort_index()


def format_cycle_report(cycle_started_ms: int, health: dict, portfolio: dict, symbol_lines: list[str]) -> str:
    started = pd.to_datetime(cycle_started_ms, unit="ms", utc=True).tz_convert(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"[BOT CYCLE] {started}",
        (
            f"health_ok={health['ok']} severity={health.get('severity', 'NA')} "
            f"exchange_ok={health['exchange_ok']} public_ok={health.get('public_ok')} "
            f"private_ok={health.get('private_ok')} db_ok={health['db_ok']}"
        ),
        (
            f"total_balance={portfolio['total_balance']:.2f} USDT | "
            f"cash={portfolio['cash_balance']:.2f} | cash_ratio={portfolio['cash_ratio']:.4f}"
        ),
        "---",
    ]
    lines.extend(symbol_lines)
    return "\n".join(lines)


def normalize_bias(stance: str) -> str:
    """
    Stance'i streak bias grubuna çevirir.

    SELL ve STRONG_SELL aynı EXIT bias ailesine alınır.

    Neden?
    - Sell-side conviction oluştuğunda önceki long birikimini taşımak istemeyiz.
    - EXIT sonrası veya regime flip sonrasında yapışkan streak'i azaltmak için
      sell tarafı net biçimde ayrı bir aile olmalı.
    """
    stance = str(stance).upper()
    if stance in {"BUY", "STRONG_BUY"}:
        return "LONG"
    if stance in {"SELL", "STRONG_SELL"}:
        # Sell ailesini ayrı tutuyoruz ki eski BUY streak'i gereksiz taşınmasın.
        return "EXIT"
    return "HOLD"


def streak_state_key(symbol: str) -> str:
    return f"signal_state:{symbol}"


def load_signal_state(bot_state_repo: BotStateRepo, symbols: list[str]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for symbol in symbols:
        raw = bot_state_repo.get(streak_state_key(symbol))
        if isinstance(raw, dict):
            state[symbol] = {
                "bias": raw.get("bias"),
                "stance": raw.get("stance"),
                "count": int(raw.get("count") or 0),
                "hold_count": int(raw.get("hold_count") or 0),
            }
    return state


def persist_signal_state(bot_state_repo: BotStateRepo, symbol: str, state: dict) -> None:
    now_ms = int(time.time() * 1000)
    payload = {
        "bias": state.get("bias"),
        "stance": state.get("stance"),
        "count": int(state.get("count") or 0),
        "hold_count": int(state.get("hold_count") or 0),
        "updated_at_ms": now_ms,
    }
    bot_state_repo.set(streak_state_key(symbol), payload, now_ms)


def update_signal_streak(bot_state_repo: BotStateRepo, signal_state: dict, symbol: str, stance: str) -> int:
    """
    Bir symbol için sinyal persistence state'ini günceller.

    Neden var?
    - Scale-in kararı tek cycle'lık anlık BUY sinyaline bakmıyor.
    - Aynı yönlü conviction kaç cycle devam etmiş, onu ölçüyoruz.
    - Bu bilgi restart sonrasında da kaybolmasın diye hem memory'de hem DB'de tutuluyor.

    Mevcut davranış:
    - BUY / STRONG_BUY -> LONG bias ailesi
    - SELL / STRONG_SELL -> EXIT bias ailesi
    - HOLD -> count decay uygular (HOLD_DECAY_AFTER cycle sonra birer birer azalır)

    HOLD decay neden gerekli?
    - Eski davranışta HOLD geldiğinde count donuyordu.
    - 3 BUY + 20 HOLD sonrasında streak=3 taşınıyor ve sonraki tek BUY streak=4 yapıyordu.
    - Bu yapay conviction. Özellikle CHOP/RANGE ortamında scale-in spam riski.
    - Artık HOLD_DECAY_AFTER cycle bekledikten sonra her HOLD count'u 1 azaltır.

    Kritik not:
    - Panic modu ayrı bir risk resetidir.
    - Panic sırasında burada güncelleme yapmak istemiyoruz.
    - Çünkü panic açıkken streak büyürse, panic kapandığı anda bot sanki uzun süredir
      conviction biriktirmiş gibi agresif giriş yapabilir.
    - O davranış yanlıştı. Bu yüzden panic path'i ana loop içinde ayrı ele alınıyor ve
      bu fonksiyon panic aktifken çağrılmıyor.
    """
    HOLD_DECAY_AFTER = max(0, int(getattr(settings, "signal_hold_decay_after", 2) or 2))
    HOLD_DECAY_STEP = max(1, int(getattr(settings, "signal_hold_decay_step", 2) or 2))
    STREAK_CAP = max(1, int(getattr(settings, "signal_streak_cap", 5) or 5))

    bias = normalize_bias(stance)
    prev = signal_state.get(symbol, {"bias": None, "count": 0, "stance": None, "hold_count": 0})

    if bias == "HOLD":
        hold_count = int(prev.get("hold_count") or 0) + 1
        count = int(prev.get("count") or 0)
        if hold_count > HOLD_DECAY_AFTER and count > 0:
            # HOLD uzadıkça eski sinyal gücünü daha hızlı siliyoruz.
            decay_steps = HOLD_DECAY_STEP + (hold_count - HOLD_DECAY_AFTER) * 2
            count = max(0, count - decay_steps)
        signal_state[symbol] = {
            "bias": prev["bias"],
            "stance": stance,
            "count": count,
            "hold_count": hold_count,
        }
        persist_signal_state(bot_state_repo, symbol, signal_state[symbol])
        return count

    count = int(prev.get("count") or 0) + 1 if prev.get("bias") == bias else 1
    count = min(count, STREAK_CAP)
    signal_state[symbol] = {"bias": bias, "stance": stance, "count": count, "hold_count": 0}
    persist_signal_state(bot_state_repo, symbol, signal_state[symbol])
    return count


def decay_signal_streak(
    bot_state_repo: BotStateRepo,
    signal_state: dict,
    symbol: str,
    amount: int,
    reason: str,
) -> int:
    # Bu fonksiyon streak'i tamamen silmez, sadece yumuşatır.
    prev = signal_state.get(symbol, {"bias": None, "count": 0, "stance": "NEUTRAL", "hold_count": 0})
    next_count = max(0, int(prev.get("count") or 0) - max(1, int(amount or 1)))
    next_bias = prev.get("bias") if next_count > 0 else None
    next_stance = prev.get("stance") if next_count > 0 else "NEUTRAL"
    signal_state[symbol] = {
        "bias": next_bias,
        "stance": next_stance,
        "count": next_count,
        "hold_count": 0,
    }
    persist_signal_state(bot_state_repo, symbol, signal_state[symbol])
    logging.info("[STREAK DECAY] %s count=%s reason=%s", symbol, next_count, reason)
    return next_count


def reset_signal_streak(bot_state_repo: BotStateRepo, signal_state: dict, symbol: str, reason: str) -> int:
    """
    Bir symbol'ün signal persistence state'ini sert şekilde sıfırlar.

    Bu helper neden eklendi?
    - Aynı reset mantığı /panic komutunda, /close_all sonrası ve panic-active cycle içinde
      birden fazla yerde lazım.
    - Aynı payload her yerde elle yazılırsa bir yerde HOLD, bir yerde NEUTRAL, bir yerde
      bias temizlenmiş ama count kalmış gibi kirli state üretmek çok kolay olur.
    - Trading botlarda tekrar eden state reset mantığını tek noktaya toplamak, özellikle
      restart-safe persistence kullanılan sistemlerde gereklidir.

    Buradaki sözleşme:
    - bias -> None
    - stance -> NEUTRAL
    - count -> 0

    Neden stance='NEUTRAL'?
    - Çünkü panic bir execution block'tan fazlasıdır; risk resetidir.
    - Sistem "önceki conviction artık güvenilir değil" demiş olur.
    - Bu yüzden panic sırasında eski BUY / SELL yön bilgisi taşınmaz.

    reason parametresi neden var?
    - Şu an payload içine yazmıyoruz.
    - Ama log tarafında resetin neden olduğunu açık söylemek için kullanıyoruz.
    - Trading/debug tarafında "neden sıfırlandı" sorusunun cevabı kritik olduğu için
      helper imzasında reason bırakıldı.
    """
    signal_state[symbol] = {
        "bias": None,
        "stance": "NEUTRAL",
        "count": 0,
        "hold_count": 0,
    }
    persist_signal_state(bot_state_repo, symbol, signal_state[symbol])
    logging.info("[STREAK RESET] %s reason=%s stance=NEUTRAL count=0", symbol, reason)
    # Scale-in counter da sıfırlanmalı: pozisyon kapandı, yeni giriş temiz başlamalı.
    bot_state_repo.delete(f"scale_in_count:{symbol}")
    return 0


def compute_position_value(position: dict | None, last_price: float) -> float:
    if not position:
        return 0.0
    qty = float(position.get("qty") or 0.0)
    if qty <= 0 or last_price <= 0:
        return 0.0
    return qty * last_price


def get_free_base_qty(exchange: OKXExchange, symbol: str) -> float:
    base_asset, _ = symbol.split("/")
    try:
        return float(exchange.get_asset_free(base_asset))
    except Exception as exc:
        logging.warning(f"[FREE BALANCE ERROR] {symbol} err={exc}")
        return 0.0


def get_trading_control_state(bot_state_repo: BotStateRepo) -> dict[str, bool | str]:
    """
    Trading'i gerçekten durduracak merkezi gate state'i burada okuyoruz.

    Neden ayrı helper var?
    - panic_mode ve trading_paused iki farklı bot_state key'i.
    - Ana loop içinde bu ikisini dağınık okumak yerine tek yerden normalize etmek gerekiyor.
    - Böylece log, Telegram status ve execution gate aynı truth source'u kullanıyor.

    Kurallar:
    - panic_mode True ise yeni entry kesin blok.
    - trading_paused True ise yeni entry kesin blok.
    - Bu iki flag sadece entry/scale-in'i durdurur.
      Exit'ler çalışmaya devam eder; çünkü risk azaltan aksiyonlar pause ile engellenmemeli.
    """
    panic_mode = bool(bot_state_repo.get("panic_mode"))
    trading_paused = bool(bot_state_repo.get("trading_paused"))

    if panic_mode:
        entry_block_reason = "blocked:panic_mode"
    elif trading_paused:
        entry_block_reason = "blocked:trading_paused"
    else:
        entry_block_reason = ""

    return {
        "panic_mode": panic_mode,
        "trading_paused": trading_paused,
        "entries_blocked": bool(entry_block_reason),
        "entry_block_reason": entry_block_reason,
    }


def normalize_symbol_input(symbol_text: str) -> str:
    raw = str(symbol_text or "").strip().upper()
    if not raw:
        raise ValueError("symbol missing")
    if "/" not in raw:
        raw = f"{raw}/USDT"
    if raw not in {s.upper(): s for s in settings.symbols}:
        mapping = {s.upper(): s for s in settings.symbols}
        if raw in mapping:
            return mapping[raw]
        raise ValueError(f"unsupported symbol: {raw}")
    return {s.upper(): s for s in settings.symbols}[raw]


def handle_force_command(
    *,
    text: str,
    notifier: TelegramNotifier | None,
    reconciler: Reconciler,
    execution: ExecutionEngine,
    positions_repo: PositionsRepo,
    portfolio_engine: PortfolioEngine,
    exchange: OKXExchange,
    bot_state_repo: BotStateRepo,
    signal_state: dict,
) -> None:

    parts = text.strip().split()
    cmd = parts[0].lower()

    try:

        # -------------------------
        # STATUS
        # -------------------------

        if cmd == "/status":

            portfolio = portfolio_engine.get_snapshot()
            trading_control = get_trading_control_state(bot_state_repo)

            notify_safe(
                notifier,
                (
                    f"[STATUS]\n"
                    f"total={portfolio['total_balance']:.2f}\n"
                    f"cash={portfolio['cash_balance']:.2f}\n"
                    f"cash_ratio={portfolio['cash_ratio']:.4f}\n"
                    f"held={','.join(portfolio['held_assets']) or 'none'}\n"
                    f"panic_mode={trading_control['panic_mode']}\n"
                    f"trading_paused={trading_control['trading_paused']}\n"
                    f"entries_blocked={trading_control['entries_blocked']}\n"
                    f"entry_block_reason={trading_control['entry_block_reason'] or 'none'}"
                ),
            )

            return


        # -------------------------
        # PARAMS
        # -------------------------

        if cmd == "/params":

            ai_thresholds = bot_state_repo.get("ai_thresholds")
            
            lines = ["[CURRENT PARAMS]", ""]
            lines.append("BASE SETTINGS:")
            lines.append(f"buy_threshold={settings.buy_threshold}")
            lines.append(f"strong_buy_threshold={settings.strong_buy_threshold}")
            lines.append(f"sell_threshold={settings.sell_threshold}")
            lines.append(f"strong_sell_threshold={settings.strong_sell_threshold}")
            lines.append(f"buy_pct={settings.buy_pct}")
            lines.append(f"strong_buy_pct={settings.strong_buy_pct}")
            lines.append(f"regime_enabled={settings.regime_enabled}")

            if ai_thresholds and isinstance(ai_thresholds, dict):
                lines.append("")
                lines.append("AI THRESHOLDS (active):")
                lines.append(f"buy_threshold={ai_thresholds.get('buy_threshold')}")
                lines.append(f"strong_buy_threshold={ai_thresholds.get('strong_buy_threshold')}")
                lines.append(f"sell_threshold={ai_thresholds.get('sell_threshold')}")
                lines.append(f"strong_sell_threshold={ai_thresholds.get('strong_sell_threshold')}")
                lines.append(f"buy_pct={ai_thresholds.get('buy_pct')}")
                lines.append(f"strong_buy_pct={ai_thresholds.get('strong_buy_pct')}")
                updated_at = ai_thresholds.get('updated_at', 0)
                if updated_at:
                    age_hours = (time.time() - updated_at) / 3600
                    lines.append(f"updated={age_hours:.1f}h ago")
                if ai_thresholds.get('reasoning'):
                    lines.append(f"reason: {ai_thresholds.get('reasoning', '')[:100]}")
            else:
                lines.append("")
                lines.append("AI THRESHOLDS: not active (using base settings)")

            lines.append("")
            lines.append("REGIME:")
            for symbol in settings.symbols:
                _regime_state_key = f"regime_state:{symbol}"
                _regime = bot_state_repo.get(_regime_state_key) or "UNKNOWN"
                lines.append(f"{symbol}: {_regime}")

            notify_safe(notifier, "\n".join(lines))
            return


        # -------------------------
        # TRIGGER
        # -------------------------

        if cmd == "/trigger":

            bot_state_repo.set(
                "manual_cycle_requested",
                True,
                int(time.time() * 1000)
            )

            notify_safe(notifier, "[TRIGGER] next cycle forced")

            return


        # -------------------------
        # FORCE REFRESH (cache/timer reset)
        # -------------------------

        if cmd == "/force_refresh":

            now_ms = int(time.time() * 1000)
            clear_refresh_state(clear_cache=True)
            bot_state_repo.set("force_refresh_groq_requested", True, now_ms)
            bot_state_repo.set("force_refresh_thresholds_requested", True, now_ms)
            bot_state_repo.set("manual_cycle_requested", True, now_ms)

            notify_safe(notifier, "[FORCE REFRESH] queued. Groq cache cleared and immediate cycle requested.")

            return


        # -------------------------
        # HEALTH
        # -------------------------

        if cmd == "/health":

            hc = HealthChecker(exchange, positions_repo.db)

            result = hc.run()

            notify_safe(
                notifier,
                (
                    "[HEALTH]\n"
                    f"ok={result['ok']}\n"
                    f"severity={result.get('severity')}\n"
                    f"exchange_ok={result['exchange_ok']}\n"
                    f"db_ok={result['db_ok']}"
                ),
            )

            return


        # -------------------------
        # POSITIONS
        # -------------------------

        if cmd == "/positions":

            rows = positions_repo.get_all_open()

            if not rows:
                notify_safe(notifier, "[POSITIONS] none")
                return

            lines = []

            for p in rows:

                lines.append(
                    (
                        f"{p['symbol']}\n"
                        f"qty={float(p['qty']):.6f}\n"
                        f"avg={float(p['avg_entry_price']):.6f}\n"
                        f"realized={float(p['realized_pnl_quote']):.4f}"
                    )
                )

            notify_safe(
                notifier,
                "[POSITIONS]\n\n" + "\n\n".join(lines)
            )

            return


        # -------------------------
        # STREAKS
        # -------------------------

        if cmd == "/streaks":

            lines = []

            for symbol, s in signal_state.items():

                lines.append(
                    f"{symbol} {s.get('stance')} x{s.get('count')}"
                )

            notify_safe(
                notifier,
                "[STREAKS]\n" + "\n".join(lines)
            )

            return


        # -------------------------
        # PNL SNAPSHOT
        # -------------------------

        # -------------------------
        # TRUE PNL SNAPSHOT
        # -------------------------

        if cmd == "/pnl":

            for sym in settings.symbols:
                try:
                    reconciler.sync_symbol(sym)
                except Exception as exc:
                    logging.warning(f"[PNL SYNC FAIL] {sym} err={exc}")

            fills_repo = FillsRepo(positions_repo.db)
            realized_total = fills_repo.sum_realized_pnl_since(0)
            fills_count = fills_repo.count_all()

            unrealized_total = 0.0
            pnl_debug_lines: list[str] = []

            if fills_count == 0:
                notify_safe(
                    notifier,
                    (
                        "[PNL]\n"
                        f"realized_total={realized_total:.2f}\n"
                        f"unrealized=N/A (DB reset sonrası fills verisi yok)\n"
                        f"net_total=N/A\n\n"
                        "Spot trading'de unrealized PnL hesaplanamaz.\n"
                        "Fills tablosu dolunca otomatik hesaplanır."
                    ),
                )
                return

            open_positions = positions_repo.get_all_open()

            for p in open_positions:

                symbol = p["symbol"]

                qty = float(p["qty"] or 0)

                avg = float(p["avg_entry_price"] or 0)

                if qty <= 0 or avg <= 0:
                    continue

                try:

                    ticker = exchange.fetch_ticker(symbol)
                    reference_price, price_source = extract_reference_price(ticker)

                    pnl = qty * (reference_price - avg)
                    unrealized_total += pnl
                    pnl_debug_lines.append(f"{symbol}: qty={qty:.4f} avg={avg:.6f} price={reference_price} pnl={pnl:.2f} ({price_source})")

                except Exception as exc:

                    logging.warning(
                        f"[PNL PRICE FAIL] {symbol} err={exc}"
                    )

            net_total = realized_total + unrealized_total

            notify_safe(
                notifier,
                (
                    "[PNL]\n"
                    f"realized_total={realized_total:.2f}\n"
                    f"unrealized={unrealized_total:.2f}\n"
                    f"net_total={net_total:.2f}\n\n"
                    + "\n".join(pnl_debug_lines) if pnl_debug_lines else ""
                ),
            )

            return

        # -------------------------
        # DAILY PNL
        # -------------------------

        if cmd == "/daily_pnl":

            for sym in settings.symbols:
                try:
                    reconciler.sync_symbol(sym)
                except Exception as exc:
                    logging.warning(f"[DAILY_PNL SYNC FAIL] {sym} err={exc}")

            fills_repo = FillsRepo(positions_repo.db)

            now_ms = int(time.time() * 1000)

            start_of_day_ms = int(
                datetime.datetime.now(LOCAL_TZ)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(datetime.timezone.utc)
                .timestamp() * 1000
            )

            # -------------------------
            # REALIZED
            # -------------------------

            realized = fills_repo.sum_realized_pnl_since(
                start_of_day_ms,
                now_ms
            )

            # -------------------------
            # UNREALIZED
            # -------------------------

            unrealized_total = 0.0
            price_debug_meta: list[str] = []
            skipped_symbols: list[str] = []

            open_positions = positions_repo.get_all_open()

            for p in open_positions:

                symbol = p["symbol"]
                qty = float(p["qty"] or 0)
                avg = float(p["avg_entry_price"] or 0)

                if qty <= 0 or avg <= 0:
                    continue

                try:

                    ticker = exchange.fetch_ticker(symbol)
                    reference_price, price_source = extract_reference_price(ticker)

                    pnl = qty * (reference_price - avg)

                    unrealized_total += pnl
                    price_debug_meta.append(f"{symbol}:{price_source}")

                except Exception as exc:
                    logging.warning("[DAILY PNL PRICE FAIL] %s err=%s", symbol, exc)
                    skipped_symbols.append(symbol)

            # -------------------------
            # NET
            # -------------------------

            net = realized + unrealized_total

            lines = [
                "[DAILY PNL]",
                "",
                f"realized: {realized:.2f} USDT",
                f"unrealized: {unrealized_total:.2f} USDT",
                f"net: {net:.2f} USDT",
            ]
            if price_debug_meta:
                lines.append(f"price_sources: {', '.join(price_debug_meta)}")
            if skipped_symbols:
                lines.append(f"skipped_symbols: {', '.join(skipped_symbols)}")

            notify_safe(notifier, "\n".join(lines))

            return


        if cmd == "/exit_stats":

            fills_repo = FillsRepo(positions_repo.db)
            now_ms = int(time.time() * 1000)
            start_of_day_ms = int(
                datetime.datetime.now(LOCAL_TZ)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(datetime.timezone.utc)
                .timestamp() * 1000
            )

            rows = fills_repo.summarize_exit_reasons_since(start_of_day_ms, now_ms)

            if not rows:
                notify_safe(notifier, "[EXIT STATS] no sell fills today")
                return

            lines = ["[EXIT STATS]", ""]
            for row in rows:
                reason = str(row.get("exit_reason") or "unknown")
                fill_count = int(row.get("fill_count") or 0)
                realized_total = float(row.get("realized_total") or 0.0)
                avg_realized = float(row.get("avg_realized_pnl") or 0.0)
                lines.append(
                    f"{reason}: count={fill_count} total={realized_total:.2f} avg={avg_realized:.2f}"
                )

            notify_safe(notifier, "\n".join(lines))
            return


        if cmd == "/price_debug":

            rows = positions_repo.get_all_open()

            if not rows:
                notify_safe(notifier, "no open positions")
                return

            lines = []

            for p in rows:

                symbol = p["symbol"]

                try:

                    ticker = exchange.fetch_ticker(symbol)

                    last_price = float(ticker.get("last") or 0)
                    info = ticker.get("info", {}) or {}
                    reference_price, reference_source = extract_reference_price(ticker)

                    mark_price = (
                        info.get("markPx")
                        or info.get("markPrice")
                        or ticker.get("mark")
                        or None
                    )

                    index_price = (
                        info.get("idxPx")
                        or info.get("indexPrice")
                        or None
                    )

                    lines.append(
                        (
                            f"{symbol}\n"
                            f"last={last_price}\n"
                            f"mark={mark_price}\n"
                            f"index={index_price}\n"
                            f"reference={reference_price} ({reference_source})"
                        )
                    )

                except Exception as e:

                    lines.append(f"{symbol} price error")

            notify_safe(
                notifier,
                "[PRICE DEBUG]\n\n" + "\n\n".join(lines)
            )

            return

        # -------------------------
        # RECONCILE
        # -------------------------

        if cmd == "/reconcile":

            for s in settings.symbols:

                reconciler.sync_symbol(s)

            notify_safe(
                notifier,
                "[RECONCILE] synced"
            )

            return


        # -------------------------
        # CLOSE ALL
        # -------------------------

        if cmd == "/close_all":

            rows = positions_repo.get_all_open()

            logging.info(
                f"[CLOSE_ALL] found {len(rows)} open positions"
            )

            for p in rows:

                symbol = p["symbol"]
                qty = float(p["qty"])

                if qty <= 0:

                    logging.info(
                        f"[CLOSE_ALL SKIP] {symbol} qty=0"
                    )

                    continue

                result = execution.force_sell(
                    symbol,
                    qty,
                    reason="close_all"
                )

                logging.info(
                    f"[CLOSE_ALL RESULT] {symbol} qty={qty} result={result}"
                )

                if result and result.get("ok"):
                    bot_state_repo.set(
                        f"pending_exit_reason:{symbol}",
                        {"reason": "manual_close", "client_order_id": result.get("client_order_id")},
                        int(time.time() * 1000),
                    )

                # close_all manuel risk azaltma aksiyonudur.
                # Burada pozisyon kapatırken streak state'ini de sıfırlıyoruz.
                # Sebep: kapanmış bir pozisyonun eski conviction state'ini taşımak,
                # sonraki girişte sahte devamlılık yaratır.
                reset_signal_streak(
                    bot_state_repo,
                    signal_state,
                    symbol,
                    "close_all",
                )

            notify_safe(
                notifier,
                "[CLOSE ALL] executed + streak reset"
            )

            return


        # -------------------------
        # PANIC
        # -------------------------

        if cmd == "/panic":

            now_ms = int(time.time() * 1000)

            bot_state_repo.set(
                "panic_mode",
                True,
                now_ms
            )

            rows = positions_repo.get_all_open()

            for p in rows:

                symbol = p["symbol"]

                qty = float(p["qty"])

                execution.force_sell(
                    symbol,
                    qty,
                    reason="panic"
                )

                bot_state_repo.set(
                    f"pending_exit_reason:{symbol}",
                    {"reason": "panic_close", "client_order_id": None},
                    int(time.time() * 1000),
                )

                # close_all manuel risk azaltma aksiyonudur.
                # Burada pozisyon kapatırken streak state'ini de sıfırlıyoruz.
                # Sebep: kapanmış bir pozisyonun eski conviction state'ini taşımak,
                # sonraki girişte sahte devamlılık yaratır.
                reset_signal_streak(
                    bot_state_repo,
                    signal_state,
                    symbol,
                    "close_all",
                )

            notify_safe(
                notifier,
                "[PANIC]\npositions closing\nstreak reset\nnew entries blocked"
            )

            return


        # -------------------------
        # PAUSE
        # -------------------------

        if cmd == "/pause_trading":

            bot_state_repo.set(
                "trading_paused",
                True,
                int(time.time() * 1000)
            )

            notify_safe(
                notifier,
                "[TRADING PAUSED]"
            )

            return


        # -------------------------
        # RESUME
        # -------------------------

        if cmd == "/resume_trading":

            bot_state_repo.set(
                "trading_paused",
                False,
                int(time.time() * 1000)
            )

            bot_state_repo.set(
                "panic_mode",
                False,
                int(time.time() * 1000)
            )

            notify_safe(
                notifier,
                "[TRADING RESUMED]"
            )

            return

        notify_safe(
            notifier,
            f"[UNKNOWN COMMAND] {text}"
        )

    except Exception as exc:

        logging.exception("[COMMAND ERROR]")

        notify_safe(
            notifier,
            f"[COMMAND ERROR]\n{text}\n{str(exc)[:200]}"
        )


def poll_telegram_commands(
    notifier: TelegramNotifier | None,
    offset: int | None,
    *,
    reconciler: Reconciler,
    execution: ExecutionEngine,
    positions_repo: PositionsRepo,
    portfolio_engine: PortfolioEngine,
    exchange: OKXExchange,
    bot_state_repo: BotStateRepo,
    signal_state: dict,
) -> int | None:

    """
    Telegram'dan komut çeker.

    timeout kullanmazsak Telegram API çoğu zaman boş döner.
    Bu yüzden long polling kullanıyoruz.

    Bu fonksiyon:
    - /status
    - /force_buy
    - /force_sell
    komutlarını yakalar
    - offset persist edilir
    """

    if notifier is None or not notifier.enabled:
        return offset

    try:

        commands, next_offset = notifier.poll_commands(
            offset=offset,
            timeout=settings.telegram_poll_interval_seconds
        )

        for cmd in commands:

            text = str(cmd.get("text") or "").strip()

            logging.info(f"[TELEGRAM COMMAND] {text}")

            handle_force_command(
                text=str(cmd.get("text") or ""),
                notifier=notifier,
                reconciler=reconciler,
                execution=execution,
                positions_repo=positions_repo,
                portfolio_engine=portfolio_engine,
                exchange=exchange,
                bot_state_repo=bot_state_repo,
                signal_state=signal_state,
            )


        return next_offset

    except Exception as exc:

        logging.exception("[TELEGRAM POLL ERROR]")

        return offset
    


def main() -> None:
    db = Database(settings.db_path)

    exchange = OKXExchange(
        api_key=settings.okx_api_key,
        secret=settings.okx_secret,
        passphrase=settings.okx_passphrase,
        sandbox=settings.okx_sandbox,
    )
    exchange.settings = settings

    orders_repo = OrdersRepo(db)
    fills_repo = FillsRepo(db)
    positions_repo = PositionsRepo(db)
    locks_repo = LocksRepo(db)
    bot_state_repo = BotStateRepo(db)

    notifier = build_notifier()
    telegram_offset_raw = bot_state_repo.get("telegram_update_offset")
    telegram_offset = int(telegram_offset_raw) if str(telegram_offset_raw or "").isdigit() else None

    portfolio_engine = PortfolioEngine(exchange)
    reconciler = Reconciler(exchange, orders_repo, fills_repo, positions_repo, bot_state_repo)
    execution = ExecutionEngine(settings, exchange, orders_repo, positions_repo, locks_repo, notifier)
    health_checker = HealthChecker(exchange, db)
    tpsl_engine = TPSLEngine(settings, bot_state_repo)
    risk_engine = RiskEngine(settings)

    signal_state = load_signal_state(bot_state_repo, settings.symbols)

    notify_safe(
        notifier,
        f"[BOT START] sandbox={settings.okx_sandbox} dry_run={settings.dry_run} symbols={','.join(settings.symbols)} timeframe={settings.timeframe}",
    )

    logging.info(
        "[RISK ENGINE] enabled max_symbol_exposure_pct=%.4f max_total_exposure_pct=%.4f max_single_trade_pct=%.4f",
        float(settings.max_symbol_exposure_pct),
        float(getattr(risk_engine, "max_total_exposure_pct", 0.0)),
        float(getattr(risk_engine, "max_single_trade_pct", 0.0)),
    )

    while True:
        cycle_started_ms = int(time.time() * 1000)
        symbol_lines: list[str] = []
        status = "OK"

        try:
            locks_repo.clear_expired(int(time.time() * 1000))
            health = health_checker.run()
            portfolio = portfolio_engine.get_snapshot()

            # P0.2:
            # Cycle içi stale portfolio problemini burada çözüyoruz.
            #
            # Eski kötü davranış:
            # - cycle başında tek bir portfolio snapshot alınıyordu
            # - aynı cycle içinde birden fazla BUY / STRONG_BUY / scale_in denemesi olursa
            #   hepsi aynı cash snapshot üzerinden quote_amount hesaplıyordu
            # - yani ilk order sonrası cash azalmış olsa bile ikinci ve üçüncü symbol hala
            #   cycle başındaki nakit varmış gibi davranıyordu
            #
            # Bunun sonucu:
            # - over-allocation denemeleri
            # - exchange reject / too small / low cash türü dengesiz sonuçlar
            # - gerçek portföy davranışı ile bot kararları arasında sapma
            #
            # Bu yüzden cycle boyunca kullanacağımız ayrı bir local değişken tutuyoruz:
            # cycle_free_usdt
            #
            # Kural:
            # - cycle başında gerçek snapshot'tan initialize edilir
            # - başarılı her entry / scale_in sonrasında local olarak azaltılır
            # - exit branch bu değeri artırmaz; çünkü aynı cycle içinde anlık satış nakdini
            #   güvenli ve deterministik biçimde yeniden kullanmak istemiyoruz
            # - bir sonraki cycle başında gerçek snapshot yeniden okunur ve local state sıfırdan kurulur
            cycle_free_usdt = float(portfolio["cash_balance"])
            logging.info("[CASH] cycle_start cash_balance=%.4f cycle_free_usdt=%.4f", float(portfolio["cash_balance"]), cycle_free_usdt)

            # Merkezi trading control state'i cycle başında okuyup logluyoruz.
            # Bu snapshot aynı cycle boyunca entry gate için kullanılıyor.
            # Böylece bir cycle ortasında kararların bir kısmı pause öncesi, bir kısmı pause sonrası gibi davranmaz.
            trading_control = get_trading_control_state(bot_state_repo)
            logging.info(
                "[TRADING CONTROL] panic_mode=%s trading_paused=%s entries_blocked=%s entry_block_reason=%s",
                trading_control["panic_mode"],
                trading_control["trading_paused"],
                trading_control["entries_blocked"],
                trading_control["entry_block_reason"] or "none",
            )

            rumor_report_needed = False
            regime_data: dict[str, dict] = {}
            market_data: dict[str, dict] = {}
            force_groq_refresh = bool(bot_state_repo.get("force_refresh_groq_requested"))
            force_threshold_refresh = bool(bot_state_repo.get("force_refresh_thresholds_requested"))
            
            if settings.llm_enabled:
                for symbol in settings.symbols:
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        last_price, _ = extract_reference_price(ticker)
                        market_data[symbol] = {
                            "price": last_price,
                            "volume": ticker.get("quoteVolume") or ticker.get("volume") or 0,
                            "change_24h": ticker.get("percentage") or 0,
                            "indicators": {},
                        }
                    except Exception:
                        pass

                for symbol in settings.symbols:
                    persisted_regime = bot_state_repo.get(f"regime_state:{symbol}") or "UNKNOWN"
                    regime_data.setdefault(symbol, {
                        "regime": persisted_regime,
                        "adx": None,
                        "trend_bias": "N/A",
                    })

                refreshed = refresh_all_if_needed(
                    settings.symbols,
                    market_data if market_data else None,
                    force=force_groq_refresh,
                )
                rumors = get_cached_rumors(settings.symbols)
                lines = ["[GROQ SENTIMENT REPORT]", ""]
                has_non_zero = False
                for sym, r in rumors.items():
                    groq = next((p for p in r.get("providers", []) if p["provider"] == "groq"), None)
                    if groq:
                        score = groq.get("rumor_score", 0)
                        if score != 0:
                            lines.append(f"{sym}: {groq.get('stance')} ({score}) - {groq.get('summary', '')[:80]}")
                            has_non_zero = True
                        elif groq.get("summary") and "rate limit" in groq.get("summary", "").lower():
                            lines.append(f"{sym}: RATE LIMITED")
                            has_non_zero = True

                llm_models = get_last_llm_models()
                bulk_model = (
                    get_last_bulk_refresh_model()
                    or llm_models.get("bulk_refresh")
                    or str(getattr(settings, "groq_model", "") or "").strip()
                )

                if not refreshed:
                    lines.append("(cached - refresh pending)")
                if bulk_model:
                    lines.append(f"model={bulk_model}")
                if has_non_zero or refreshed or force_groq_refresh:
                    notify_safe(notifier, "\n".join(lines))

                current_settings = {
                    "buy_threshold": settings.buy_threshold,
                    "strong_buy_threshold": settings.strong_buy_threshold,
                    "sell_threshold": settings.sell_threshold,
                    "strong_sell_threshold": settings.strong_sell_threshold,
                    "buy_pct": settings.buy_pct,
                    "strong_buy_pct": settings.strong_buy_pct,
                }
                ai_thresholds = update_thresholds_if_needed(market_data, regime_data, current_settings, force=force_threshold_refresh)
                if ai_thresholds:
                    bot_state_repo.set("ai_thresholds", ai_thresholds, int(time.time() * 1000))
                    llm_models = get_last_llm_models()
                    threshold_model = (
                        str(ai_thresholds.get("model") or "").strip()
                        or get_last_threshold_update_model()
                        or llm_models.get("threshold_update")
                        or str(getattr(settings, "groq_model", "") or "").strip()
                    )
                    notify_safe(
                        notifier,
                        f"[AI THRESHOLDS UPDATED]\n"
                        + (f"model={threshold_model}\n" if threshold_model else "")
                        + f"buy={ai_thresholds['buy_threshold']:.1f} strong_buy={ai_thresholds['strong_buy_threshold']:.1f}\n"
                        + f"sell={ai_thresholds['sell_threshold']:.1f} strong_sell={ai_thresholds['strong_sell_threshold']:.1f}\n"
                        + f"buy_pct={ai_thresholds['buy_pct']:.3f} strong_buy_pct={ai_thresholds['strong_buy_pct']:.3f}\n"
                        + f"reason: {ai_thresholds.get('reasoning', '')[:100]}"
                    )

                if force_groq_refresh:
                    now_ms = int(time.time() * 1000)
                    if refreshed:
                        bot_state_repo.set("force_refresh_groq_requested", False, now_ms)
                        notify_safe(notifier, "[FORCE REFRESH][GROQ] OK")
                    elif not settings.groq_api_key:
                        bot_state_repo.set("force_refresh_groq_requested", False, now_ms)
                        notify_safe(notifier, "[FORCE REFRESH][GROQ] SKIPPED - groq api key missing")
                    else:
                        notify_safe(notifier, "[FORCE REFRESH][GROQ] FAILED - will retry next cycle")

                if force_threshold_refresh:
                    now_ms = int(time.time() * 1000)
                    if ai_thresholds:
                        bot_state_repo.set("force_refresh_thresholds_requested", False, now_ms)
                        notify_safe(notifier, "[FORCE REFRESH][AI THRESHOLDS] OK")
                    elif not settings.groq_api_key:
                        bot_state_repo.set("force_refresh_thresholds_requested", False, now_ms)
                        notify_safe(notifier, "[FORCE REFRESH][AI THRESHOLDS] SKIPPED - groq api key missing")
                    else:
                        notify_safe(notifier, "[FORCE REFRESH][AI THRESHOLDS] FAILED - will retry next cycle")

            else:
                if force_groq_refresh or force_threshold_refresh:
                    now_ms = int(time.time() * 1000)
                    if force_groq_refresh:
                        bot_state_repo.set("force_refresh_groq_requested", False, now_ms)
                    if force_threshold_refresh:
                        bot_state_repo.set("force_refresh_thresholds_requested", False, now_ms)
                    notify_safe(notifier, "[FORCE REFRESH] skipped - llm disabled")

            ai_thresholds = bot_state_repo.get("ai_thresholds")

            for symbol in settings.symbols:
                try:
                    reconcile_ok = True
                    try:
                        reconciler.sync_symbol(symbol)
                    except Exception as e:
                        logging.warning(f"[RECON FAIL] {symbol} err={e}")
                        reconcile_ok = False

                    position = positions_repo.get(symbol)
                    has_position = bool(
                        position
                        and str(position["status"]).upper() == "OPEN"
                        and float(position["qty"]) > 0
                    )

                    ohlcv_rows = exchange.fetch_ohlcv(symbol, settings.timeframe, settings.ohlcv_limit)
                    df = ohlcv_to_df(ohlcv_rows)
                    df = calculate_indicators(df)
                    # last_price: TP/SL ve position value hesapları için live fiyat kullan.
                    # df["close"].iloc[-1] live bar'ın anlık fiyatıdır — henüz kapanmamış.
                    # extract_reference_price: mark > index > last önceliğiyle doğru kaynağı seçer.
                    # fetch_ticker başarısız olursa df close'a fallback yapılır.
                    try:
                        _ticker_for_price = exchange.fetch_ticker(symbol)
                        last_price, _ = extract_reference_price(_ticker_for_price)
                    except Exception:
                        last_price = float(df["close"].iloc[-1])

                    indicator_score, indicator_details = calculate_indicator_score(df)
                    rumor = aggregate_rumor(symbol) if settings.llm_enabled else {
                        "symbol": symbol,
                        "rumor_total_score": 0,
                        "rumor_avg_confidence": 0.0,
                        "providers": [],
                    }
                    total_score = indicator_score + float(rumor["rumor_total_score"])

                    regime = "BASE"
                    regime_params = {
                        "buy_threshold": float(settings.buy_threshold),
                        "strong_buy_threshold": float(settings.strong_buy_threshold),
                        "sell_threshold": float(settings.sell_threshold),
                        "strong_sell_threshold": float(settings.strong_sell_threshold),
                        "buy_pct": float(settings.buy_pct),
                        "strong_buy_pct": float(settings.strong_buy_pct),
                        "regime": "BASE",
                    }
                    regime_diag = {"regime": "BASE"}
                    regime_flipped = False

                    if settings.regime_enabled:
                        _regime_state_key = f"regime_state:{symbol}"
                        _prev_regime_raw = bot_state_repo.get(_regime_state_key)
                        _prev_regime = str(_prev_regime_raw or "BASE") if isinstance(_prev_regime_raw, str) else "BASE"

                        regime, regime_diag = detect_market_regime(df, settings, current_regime=_prev_regime)

                        regime_flipped = regime != _prev_regime
                        if regime_flipped:
                            # Rejim değiştiyse eski conviction'a biraz daha az güveniyoruz.
                            logging.info(f"[REGIME FLIP] {symbol} {_prev_regime} -> {regime}")
                        bot_state_repo.set(_regime_state_key, regime, int(time.time() * 1000))

                        regime_params = resolve_regime_params(settings, regime)
                        
                        if ai_thresholds and isinstance(ai_thresholds, dict):
                            regime_params = {
                                **regime_params,
                                "buy_threshold": float(ai_thresholds.get("buy_threshold", regime_params["buy_threshold"])),
                                "strong_buy_threshold": float(ai_thresholds.get("strong_buy_threshold", regime_params["strong_buy_threshold"])),
                                "sell_threshold": float(ai_thresholds.get("sell_threshold", regime_params["sell_threshold"])),
                                "strong_sell_threshold": float(ai_thresholds.get("strong_sell_threshold", regime_params["strong_sell_threshold"])),
                                "buy_pct": float(ai_thresholds.get("buy_pct", regime_params["buy_pct"])),
                                "strong_buy_pct": float(ai_thresholds.get("strong_buy_pct", regime_params["strong_buy_pct"])),
                            }
                            regime_params["ai_adjusted"] = True

                        regime_data[symbol] = regime_diag
                        if market_data.get(symbol):
                            market_data[symbol]["indicators"] = indicator_details

                        logging.info(f"[REGIME RAW] {symbol} prev={_prev_regime} diag={regime_diag} params={regime_params}")

                    logging.info(
                        f"[DEBUG SCORE] symbol={symbol} indicator={indicator_score}, details={indicator_details}, rumor={rumor}, total={total_score}"
                    )
                    signal = evaluate_signal(total_score, regime_params)
                    ai_tag = " [AI_THRESHOLDS]" if regime_params.get("ai_adjusted") else ""
                    logging.info(f"[DEBUG SIGNAL]{ai_tag} {symbol} signal={signal}")

                    action = str(signal["action"]).upper()
                    fraction = float(signal["fraction"])
                    stance = str(signal["stance"]).upper()

                    # P0.1.1:
                    # panic_mode aktifken streak'in artmasına artık izin vermiyoruz.
                    #
                    # Eski kötü davranış neydi?
                    # - panic aktif
                    # - entry'ler blocked:panic_mode
                    # - ama update_signal_streak yine çalışıyor
                    # - yani bot risk almadan conviction biriktiriyordu
                    # - panic kapanınca bir anda yüksek streak ile agresif entry / scale-in
                    #   denemesi çıkabiliyordu
                    #
                    # Bu mimari olarak yanlıştı. Çünkü panic bir "pause" değil,
                    # risk resetidir. Sistem o anda mevcut conviction'ı çöpe atmalıdır.
                    #
                    # Bu yüzden burada davranış ikiye ayrıldı:
                    # 1) panic_mode=False -> normal streak güncelle
                    # 2) panic_mode=True  -> streak'i zorla 0'a çek ve NEUTRAL state persist et
                    #
                    # Neden cycle içinde de tekrar resetliyoruz?
                    # - Çünkü kullanıcı /panic komutunu verdikten sonra bot açık kalabilir,
                    #   restart olabilir, /trigger atabilir, yeni cycle çalışabilir.
                    # - Her cycle'da panic aktifse state'in tekrar kirlenmediğini garanti etmek istiyoruz.
                    # - Bu sayede panic aktif olduğu sürece streak tekrar büyüyemez.
                    if trading_control["panic_mode"]:
                        streak = reset_signal_streak(
                            bot_state_repo,
                            signal_state,
                            symbol,
                            "panic_mode_active_cycle",
                        )
                    else:
                        streak = update_signal_streak(bot_state_repo, signal_state, symbol, stance)

                    order_result = None
                    execution_note = "no_order"
                    tpsl_note = "none"

                    tpsl_decision = tpsl_engine.evaluate(symbol, position, last_price)
                    if tpsl_decision["triggered"]:
                        action = str(tpsl_decision["action"]).upper()
                        stance = "SELL"
                        tpsl_note = (
                            f"{tpsl_decision['reason']}"
                            f" sl={float(tpsl_decision['stop_loss_price'] or 0.0):.6f}"
                            f" ptp={float(tpsl_decision['partial_take_profit_price'] or 0.0):.6f}"
                            f" ftp={float(tpsl_decision['full_take_profit_price'] or 0.0):.6f}"
                            f" pnl_pct={float(tpsl_decision['pnl_pct'] or 0.0) * 100:.2f}%"
                        )
                        logging.info(f"[TPSL TRIGGER] {symbol} action={action} {tpsl_note}")
                    elif has_position:
                        tpsl_note = str(tpsl_decision.get("reason") or "tpsl_not_triggered")
                        logging.info(
                            f"[TPSL CHECK] {symbol} sl={tpsl_decision['stop_loss_price']} ptp={tpsl_decision['partial_take_profit_price']} ftp={tpsl_decision['full_take_profit_price']} pnl_pct={tpsl_decision['pnl_pct']} reason={tpsl_decision['reason']}"
                        )

                    if action in {"BUY", "STRONG_BUY"}:
                        # P0.1: panic_mode veya trading_paused aktifse yeni entry açmayacağız.
                        # Bunu burada, BUY branch'inin en tepesinde yapıyoruz.
                        # Sebep:
                        # - Hem initial_entry hem scale_in aynı kapıdan geçsin.
                        # - Exit branch etkilenmesin.
                        # - Bot raporunda neden order atılmadığı açıkça görülsün.
                        if trading_control["entries_blocked"]:
                            execution_note = str(trading_control["entry_block_reason"])
                        elif not reconcile_ok:
                            execution_note = "blocked:reconcile_fail"
                        elif not has_position:
                            if positions_repo.count_open() >= settings.max_open_positions:
                                execution_note = "blocked:max_positions"
                            elif float(cycle_free_usdt) < settings.min_free_usdt:
                                # P0.2:
                                # Burada artık cycle başındaki portfolio['cash_balance'] yerine
                                # cycle_free_usdt kullanıyoruz.
                                #
                                # Sebep:
                                # - bu cycle içinde daha önce başarılı entry olduysa local nakit azalmış olabilir
                                # - stale snapshot ile tekrar hesap yaparsak portföyde olmayan nakdi ikinci kez harcamaya kalkarız
                                execution_note = "blocked:low_cash"
                            else:
                                desired_quote = float(portfolio["total_balance"]) * fraction
                                max_cash_usable = max(0.0, float(cycle_free_usdt) - settings.min_free_usdt)
                                quote_amount = min(desired_quote, max_cash_usable)

                                logging.info(
                                    "[ENTRY BUDGET] %s desired_quote=%.4f cycle_free_usdt=%.4f max_cash_usable=%.4f quote_amount=%.4f",
                                    symbol,
                                    desired_quote,
                                    float(cycle_free_usdt),
                                    max_cash_usable,
                                    quote_amount,
                                )

                                if quote_amount >= settings.min_order_quote_usdt:
                                    # P0.4:
                                    # RiskEngine artık gerçekten ana akışın parçası.
                                    # Burada amaç "risk motoru varmış gibi yapmak" değil,
                                    # execution öncesi zorunlu bir onay kapısı koymak.
                                    #
                                    # Önemli detay:
                                    # - portfolio snapshot cycle başında alınmıştı
                                    # - fakat P0.2 ile cycle_free_usdt local olarak azalabiliyor
                                    # - RiskEngine toplam/deployed exposure hesabını cash_balance üzerinden yaptığı için
                                    #   ona stale portfolio vermek tekrar aynı problemi risk katmanına taşır
                                    #
                                    # Bu yüzden risk_portfolio oluşturuyoruz:
                                    # - total_balance aynı kalır
                                    # - cash_balance ise cycle_free_usdt ile güncellenir
                                    # - cash_ratio da bu local state'e göre yeniden hesaplanır
                                    #
                                    # Böylece risk motoru da bu cycle içinde rezerve edilmiş nakdi görür.
                                    risk_portfolio = dict(portfolio)
                                    risk_portfolio["cash_balance"] = float(cycle_free_usdt)
                                    total_balance_for_risk = float(risk_portfolio.get("total_balance") or 0.0)
                                    risk_portfolio["cash_ratio"] = (
                                        float(cycle_free_usdt) / total_balance_for_risk
                                        if total_balance_for_risk > 0
                                        else 0.0
                                    )

                                    allowed, risk_quote_amount, risk_reason = risk_engine.check_entry(
                                        symbol=symbol,
                                        portfolio=risk_portfolio,
                                        position=position,
                                        desired_quote=quote_amount,
                                        last_price=last_price,
                                    )

                                    if not allowed:
                                        execution_note = f"blocked:risk:{risk_reason}"
                                        logging.info(
                                            "[RISK ENTRY] %s allow=%s desired_quote=%.4f adjusted_quote=%.4f reason=%s cash_balance=%.4f total_balance=%.4f",
                                            symbol,
                                            allowed,
                                            quote_amount,
                                            risk_quote_amount,
                                            risk_reason,
                                            float(risk_portfolio.get("cash_balance") or 0.0),
                                            float(risk_portfolio.get("total_balance") or 0.0),
                                        )
                                    else:
                                        # RiskEngine quote küçültmüş olabilir.
                                        # Bu iyi bir şey; çünkü botu tamamen block etmek yerine
                                        # limitler içinde trade etmeye çalışıyoruz.
                                        if float(risk_quote_amount) != float(quote_amount):
                                            logging.info(
                                                "[RISK ENTRY] %s allow=%s desired_quote=%.4f adjusted_quote=%.4f reason=%s",
                                                symbol,
                                                allowed,
                                                quote_amount,
                                                risk_quote_amount,
                                                risk_reason,
                                            )

                                        quote_amount = float(risk_quote_amount)

                                        order_result = execution.place_entry(
                                            symbol=symbol,
                                            quote_amount=quote_amount,
                                            reason=f"initial_entry regime={regime} action={action} score={total_score} streak={streak}",
                                            intent="initial_entry",
                                        )
                                        if order_result and order_result.get("ok"):
                                            # P0.2:
                                            # Entry başarılıysa bu cycle içinde tekrar kullanılabilecek local nakdi düşüyoruz.
                                            cycle_free_usdt = max(0.0, float(cycle_free_usdt) - float(quote_amount))
                                            execution_note = f"initial_entry_sent:{action.lower()}"
                                            logging.info(
                                                "[CASH] %s initial_entry_reserved quote_amount=%.4f cycle_free_usdt=%.4f",
                                                symbol,
                                                quote_amount,
                                                float(cycle_free_usdt),
                                            )
                                            # DRY-RUN SIMULATION: fills ve positions güncelle.
                                            # dry_run=True iken exchange order gönderilmez ama
                                            # DB state güncellenmezse bir sonraki cycle yeniden entry açar.
                                            # Gerçek simulation için fill + position yazılmalı.
                                            if settings.dry_run and order_result.get("dry_run"):
                                                _dry_qty = quote_amount / last_price if last_price > 0 else 0.0
                                                _dry_trade_id = f"dry_{order_result.get('client_order_id', '')}"
                                                try:
                                                    fills_repo.insert(
                                                        trade_id=_dry_trade_id,
                                                        order_id=None,
                                                        client_order_id=order_result.get("client_order_id"),
                                                        symbol=symbol,
                                                        side="buy",
                                                        qty=_dry_qty,
                                                        price=last_price,
                                                        cost=quote_amount,
                                                        fee_cost=quote_amount * 0.001,
                                                        fee_currency="USDT",
                                                        timestamp_ms=int(time.time() * 1000),
                                                        realized_pnl_quote=0.0,
                                                        exit_reason=None,
                                                    )
                                                    reconciler.sync_symbol(symbol)
                                                    logging.info("[DRY SIM BUY] %s qty=%.8f price=%.6f", symbol, _dry_qty, last_price)
                                                except Exception as _dry_exc:
                                                    logging.warning("[DRY SIM BUY ERROR] %s %s", symbol, _dry_exc)
                                        else:
                                            execution_note = f"blocked:entry_failed:{(order_result or {}).get('reason', 'unknown')}"
                                else:
                                    execution_note = "blocked:too_small"
                        else:
                            if not settings.scale_in_enabled:
                                execution_note = "blocked:already_in_position"
                            elif not reconcile_ok:
                                execution_note = "blocked:reconcile_fail"
                            else:
                                trigger = settings.strong_scale_in_trigger_streak if action == "STRONG_BUY" else settings.scale_in_trigger_streak
                                if streak < trigger:
                                    execution_note = f"blocked:scale_in_wait_streak({streak}/{trigger})"
                                else:
                                    _max_scale_in = int(getattr(settings, "max_scale_in_count", 3))
                                    _scale_key = f"scale_in_count:{symbol}"
                                    _scale_state = bot_state_repo.get(_scale_key) or {}
                                    _scale_count = int(_scale_state.get("count") or 0)
                                    if _scale_count >= _max_scale_in:
                                        execution_note = f"blocked:max_scale_in_count({_scale_count}/{_max_scale_in})"
                                        logging.info(
                                            "[SCALE GUARD] %s blocked count=%d max=%d",
                                            symbol, _scale_count, _max_scale_in,
                                        )
                                    else:
                                        position_value = compute_position_value(position, last_price)
                                        total_balance = max(float(portfolio["total_balance"]), 1e-12)
                                        scale_fraction = settings.strong_scale_in_buy_pct if action == "STRONG_BUY" else settings.scale_in_buy_pct
                                        desired_quote = total_balance * scale_fraction
                                        exposure_cap_quote = max(0.0, total_balance * settings.max_symbol_exposure_pct - position_value)
                                        cash_cap_quote = max(0.0, float(cycle_free_usdt) - settings.min_free_usdt)
                                        quote_amount = min(desired_quote, exposure_cap_quote, cash_cap_quote)

                                        logging.info(
                                            "[SCALE BUDGET] %s desired_quote=%.4f exposure_cap_quote=%.4f cycle_free_usdt=%.4f cash_cap_quote=%.4f quote_amount=%.4f",
                                            symbol,
                                            desired_quote,
                                            exposure_cap_quote,
                                            float(cycle_free_usdt),
                                            cash_cap_quote,
                                            quote_amount,
                                        )

                                        if quote_amount >= settings.min_order_quote_usdt:
                                            # P0.4:
                                            # Scale-in tarafı da risk katmanından geçmek zorunda.
                                            # Aksi halde initial entry risk kontrollü olurken
                                            # aynı symbol üzerine scale-in ile limitleri delmiş oluruz.
                                            #
                                            # Burada position parametresi dolu geldiği için RiskEngine
                                            # symbol exposure artışını mevcut pozisyon değeri üzerinden hesaplar.
                                            risk_portfolio = dict(portfolio)
                                            risk_portfolio["cash_balance"] = float(cycle_free_usdt)
                                            total_balance_for_risk = float(risk_portfolio.get("total_balance") or 0.0)
                                            risk_portfolio["cash_ratio"] = (
                                                float(cycle_free_usdt) / total_balance_for_risk
                                                if total_balance_for_risk > 0
                                                else 0.0
                                            )

                                            allowed, risk_quote_amount, risk_reason = risk_engine.check_entry(
                                                symbol=symbol,
                                                portfolio=risk_portfolio,
                                                position=position,
                                                desired_quote=quote_amount,
                                                last_price=last_price,
                                            )

                                            if not allowed:
                                                execution_note = f"blocked:risk:{risk_reason}"
                                                logging.info(
                                                    "[RISK ENTRY] %s allow=%s desired_quote=%.4f adjusted_quote=%.4f reason=%s cash_balance=%.4f total_balance=%.4f",
                                                    symbol,
                                                    allowed,
                                                    quote_amount,
                                                    risk_quote_amount,
                                                    risk_reason,
                                                    float(risk_portfolio.get("cash_balance") or 0.0),
                                                    float(risk_portfolio.get("total_balance") or 0.0),
                                                )
                                            else:
                                                if float(risk_quote_amount) != float(quote_amount):
                                                    logging.info(
                                                        "[RISK ENTRY] %s allow=%s desired_quote=%.4f adjusted_quote=%.4f reason=%s",
                                                        symbol,
                                                        allowed,
                                                        quote_amount,
                                                        risk_quote_amount,
                                                        risk_reason,
                                                    )

                                                quote_amount = float(risk_quote_amount)

                                                order_result = execution.place_entry(
                                                    symbol=symbol,
                                                    quote_amount=quote_amount,
                                                    reason=f"scale_in regime={regime} action={action} score={total_score} streak={streak}",
                                                    intent="scale_in",
                                                )
                                                if order_result and order_result.get("ok"):
                                                    cycle_free_usdt = max(0.0, float(cycle_free_usdt) - float(quote_amount))
                                                    execution_note = f"scale_in_sent:{action.lower()}:streak={streak}"
                                                    logging.info(
                                                        "[CASH] %s scale_in_reserved quote_amount=%.4f cycle_free_usdt=%.4f",
                                                        symbol,
                                                        quote_amount,
                                                        float(cycle_free_usdt),
                                                    )
                                                    _scale_count += 1
                                                    bot_state_repo.set(
                                                        _scale_key,
                                                        {"count": _scale_count, "updated_at_ms": int(time.time() * 1000)},
                                                        int(time.time() * 1000),
                                                    )
                                                    # DRY-RUN SIMULATION: scale-in fill + position güncelle.
                                                    if settings.dry_run and order_result.get("dry_run"):
                                                        _dry_qty = quote_amount / last_price if last_price > 0 else 0.0
                                                        _dry_trade_id = f"dry_{order_result.get('client_order_id', '')}"
                                                        try:
                                                            fills_repo.insert(
                                                                trade_id=_dry_trade_id,
                                                                order_id=None,
                                                                client_order_id=order_result.get("client_order_id"),
                                                                symbol=symbol,
                                                                side="buy",
                                                                qty=_dry_qty,
                                                                price=last_price,
                                                                cost=quote_amount,
                                                                fee_cost=quote_amount * 0.001,
                                                                fee_currency="USDT",
                                                                timestamp_ms=int(time.time() * 1000),
                                                                realized_pnl_quote=0.0,
                                                                exit_reason=None,
                                                            )
                                                            reconciler.sync_symbol(symbol)
                                                            logging.info("[DRY SIM SCALE] %s qty=%.8f price=%.6f", symbol, _dry_qty, last_price)
                                                        except Exception as _dry_exc:
                                                            logging.warning("[DRY SIM SCALE ERROR] %s %s", symbol, _dry_exc)
                                                else:
                                                    execution_note = f"blocked:scale_in_failed:{(order_result or {}).get('reason', 'unknown')}"
                                        else:
                                            execution_note = "blocked:scale_in_too_small"

                    elif action in {"FULL_CLOSE", "PARTIAL_CLOSE"}:
                        if has_position:
                            position_qty = float(position["qty"])
                            target_sell_qty = position_qty if action == "FULL_CLOSE" else position_qty * settings.partial_sell_ratio
                            free_base_qty = get_free_base_qty(exchange, symbol)
                            if free_base_qty <= 1e-12:
                                execution_note = "blocked:no_free_balance"
                                logging.warning(
                                    f"[SAFE EXIT BLOCK] {symbol} position_qty={position_qty} target_sell_qty={target_sell_qty} free_base_qty={free_base_qty}"
                                )
                            else:
                                sell_qty = min(target_sell_qty, free_base_qty)
                                if sell_qty <= 1e-12:
                                    execution_note = "blocked:no_sellable_qty"
                                    logging.warning(
                                        f"[SAFE EXIT BLOCK] {symbol} position_qty={position_qty} target_sell_qty={target_sell_qty} free_base_qty={free_base_qty}"
                                    )
                                else:
                                    order_result = execution.place_exit(
                                        symbol=symbol,
                                        base_qty=sell_qty,
                                        reason=f"action={action} regime={regime} score={total_score} streak={streak} tpsl_reason={tpsl_decision['reason']}",
                                    )
                                    if order_result and order_result.get("ok"):
                                        execution_note = f"exit_sent:{action.lower()}"
                                        _exit_reason = tpsl_decision["reason"] if tpsl_decision["triggered"] else f"indicator_{action.lower()}"
                                        bot_state_repo.set(
                                            f"pending_exit_reason:{symbol}",
                                            {"reason": _exit_reason, "client_order_id": order_result.get("client_order_id")},
                                            int(time.time() * 1000),
                                        )
                                        # PARTIAL_CLOSE veya FULL_CLOSE başarılıysa scale_in_count sıfırla.
                                        bot_state_repo.delete(f"scale_in_count:{symbol}")
                                        # DRY-RUN SIMULATION: sell fill + position güncelle.
                                        # dry_run modunda exchange order gönderilmez ama fill yazılmazsa
                                        # reconciler pozisyonu kapalı göremez → bir sonraki cycle tekrar sat sinyali.
                                        if settings.dry_run and order_result.get("dry_run"):
                                            _dry_trade_id = f"dry_{order_result.get('client_order_id', '')}"
                                            _dry_avg = float((position or {}).get("avg_entry_price") or last_price)
                                            _dry_realized = sell_qty * (last_price - _dry_avg)
                                            try:
                                                fills_repo.insert(
                                                    trade_id=_dry_trade_id,
                                                    order_id=None,
                                                    client_order_id=order_result.get("client_order_id"),
                                                    symbol=symbol,
                                                    side="sell",
                                                    qty=sell_qty,
                                                    price=last_price,
                                                    cost=sell_qty * last_price,
                                                    fee_cost=sell_qty * last_price * 0.001,
                                                    fee_currency="USDT",
                                                    timestamp_ms=int(time.time() * 1000),
                                                    realized_pnl_quote=_dry_realized,
                                                    exit_reason=_exit_reason,
                                                )
                                                reconciler.sync_symbol(symbol)
                                                logging.info("[DRY SIM SELL] %s qty=%.8f price=%.6f realized=%.4f", symbol, sell_qty, last_price, _dry_realized)
                                            except Exception as _dry_exc:
                                                logging.warning("[DRY SIM SELL ERROR] %s %s", symbol, _dry_exc)
                                    else:
                                        execution_note = f"blocked:exit_failed:{(order_result or {}).get('reason', 'unknown')}"
                        else:
                            execution_note = "blocked:no_position"
                    else:
                        execution_note = "hold"

                    if not trading_control["panic_mode"]:
                        if execution_note.startswith("exit_sent:"):
                            # Çıkış emri gerçekten gittiyse streak'i temizliyoruz.
                            streak = reset_signal_streak(
                                bot_state_repo,
                                signal_state,
                                symbol,
                                "exit_sent",
                            )
                        elif not has_position:
                            # Pozisyon yoksa eski streak'i taşımak anlamsız.
                            streak = reset_signal_streak(
                                bot_state_repo,
                                signal_state,
                                symbol,
                                "no_position",
                            )
                        elif regime_flipped:
                            # Rejim değişince eski streak'in de sıfırlanmasını istiyoruz.
                            streak = reset_signal_streak(
                                bot_state_repo,
                                signal_state,
                                symbol,
                                "regime_flip",
                            )

                    pos_text = "none"
                    if has_position and position:
                        pos_text = (
                            f"qty={float(position['qty']):.8f} avg={float(position['avg_entry_price']):.6f} realized={float(position['realized_pnl_quote']):.4f}"
                        )

                    order_text = execution_note if order_result is None else str(order_result)
                    groq_provider = next((p for p in rumor.get("providers", []) if p["provider"] == "groq"), None)
                    groq_text = ""
                    ai_text = ""
                    if groq_provider and settings.llm_enabled:
                        groq_score = groq_provider.get("rumor_score", 0)
                        groq_stance = groq_provider.get("stance", "N/A")
                        groq_summary = groq_provider.get("summary", "")[:50]
                        groq_text = f" | groq={groq_stance}({groq_score}) {groq_summary}..."
                    if regime_params.get("ai_adjusted"):
                        ai_text = " [AI_THRESHOLDS]"
                    log_line = (
                        f"{symbol}{ai_text} | regime={regime} | total={total_score:.2f} | action={action} | stance={stance} | streak={streak} "
                        f"| position={pos_text} | tpsl={tpsl_note} | exec={order_text}{groq_text}"
                    )
                    symbol_lines.append(log_line)
                    logging.info(log_line)

                except Exception as symbol_exc:
                    status = "PARTIAL_FAIL"
                    err_line = f"{symbol} | ERROR | {str(symbol_exc)[:250]}"
                    symbol_lines.append(err_line)
                    logging.exception("symbol loop failed: %s", symbol)
                    notify_safe(notifier, f"[SYMBOL ERROR] {err_line}")

            logging.info("[CASH] cycle_end cycle_free_usdt=%.4f portfolio_cash_balance_snapshot=%.4f", float(cycle_free_usdt), float(portfolio["cash_balance"]))

            cycle_finished_ms = int(time.time() * 1000)
            report_text = format_cycle_report(cycle_started_ms, health, portfolio, symbol_lines)
            db.execute(
                "INSERT INTO cycle_reports(cycle_started_ms, cycle_finished_ms, summary, status) VALUES (?, ?, ?, ?)",
                (cycle_started_ms, cycle_finished_ms, report_text, status),
            )
            # -------------------------
            # POST-CYCLE RECONCILE
            # -------------------------

            for symbol in settings.symbols:

                try:

                    reconciler.sync_symbol(symbol)

                except Exception as e:

                    logging.warning(
                        f"[POST-CYCLE RECON FAIL] {symbol} err={e}"
                    )
            if settings.notify_every_cycle:
                notify_safe(notifier, report_text)

        except Exception as exc:
            status = "FAIL"
            logging.exception("main loop crashed")
            crash_text = f"[MAIN LOOP CRASH]\n{str(exc)}\n\n{traceback.format_exc()[:2000]}"
            notify_safe(notifier, crash_text)
            db.execute(
                "INSERT INTO cycle_reports(cycle_started_ms, cycle_finished_ms, summary, status) VALUES (?, ?, ?, ?)",
                (cycle_started_ms, int(time.time() * 1000), crash_text, status),
            )

        # -------------------------
        # MANUAL TRIGGER CHECK
        # -------------------------

        if bot_state_repo.get("manual_cycle_requested"):

            logging.info("[MANUAL TRIGGER] cycle requested")

            bot_state_repo.set(
                "manual_cycle_requested",
                False,
                int(time.time() * 1000)
            )

            continue

        sleep_until = time.time() + settings.loop_seconds

        while time.time() < sleep_until:

            telegram_offset = poll_telegram_commands(
                notifier,
                telegram_offset,
                reconciler=reconciler,
                execution=execution,
                positions_repo=positions_repo,
                portfolio_engine=portfolio_engine,
                exchange=exchange,
                bot_state_repo=bot_state_repo,
                signal_state=signal_state,
            )

            if telegram_offset is not None:

                bot_state_repo.set(
                    "telegram_update_offset",
                    telegram_offset,
                    int(time.time() * 1000),
                )

            # -------------------------
            # MANUAL TRIGGER CHECK
            # -------------------------

            if bot_state_repo.get("manual_cycle_requested"):

                logging.info("[MANUAL TRIGGER] cycle requested")

                bot_state_repo.set(
                    "manual_cycle_requested",
                    False,
                    int(time.time() * 1000),
                )

                # sleep'i bitir → yeni cycle başlasın
                sleep_until = 0

            time.sleep(
                max(1, int(settings.telegram_poll_interval_seconds))
            )


if __name__ == "__main__":
    main()
