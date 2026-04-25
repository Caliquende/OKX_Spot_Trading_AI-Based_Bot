from __future__ import annotations

"""
ANA BOT AKISI / MAIN BOT FLOW
=============================

TR:
Bu dosya projenin orkestra sefidir.
Tek basina butun hesaplamalari yapmaz ama hangi parcanin ne zaman calisacagini belirler.

Kabaca akis soyledir:
1. Ayarlar yuklenir.
2. Exchange + DB + yardimci motorlar hazirlanir.
3. Her cycle'da veri cekilir.
4. Indikator + rumor + regime + risk + TP/SL birlikte yorumlanir.
5. Gerekirse order gonderilir.
6. Sonuc log'a ve rapora yazilir.

Sifirdan bakan biri icin basit ozet:
- "Ne alalim / satalim?" sorusunun cevabi burada koordine edilir.
- "Gercekten order nasil gider?" sorusunun cevabi execution_engine'dedir.
- "Pozisyon gercekten acik mi?" sorusunun cevabi reconciler + DB tarafindadir.

EN:
This file is the conductor of the project.
It does not perform every calculation by itself, but it decides which part runs and when.

The flow is roughly:
1. Settings are loaded.
2. Exchange, DB, and helper engines are created.
3. Market data is fetched on every cycle.
4. Indicators, rumor, regime, risk, and TP/SL are interpreted together.
5. Orders are sent if conditions allow.
6. Results are written into logs and reports.

Simple beginner summary:
- The answer to "What should we buy or sell?" is coordinated here.
- The answer to "How is the real order sent?" lives in `execution_engine`.
- The answer to "Is the position really open?" lives in `reconciler` and the DB layer.
"""


import logging
import os
import textwrap
import time
import traceback
import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import ccxt
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
from core.position_manager import PositionManager
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


def is_transient_exchange_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            ccxt.OnMaintenance,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
            ccxt.NetworkError,
            ccxt.DDoSProtection,
            ccxt.RateLimitExceeded,
        ),
    )


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


def _clamp_float(value: object, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(min_value, min(max_value, parsed))


def _rumor_summaries(rumor: dict) -> str:
    providers = rumor.get("providers", []) if isinstance(rumor, dict) else []
    summaries: list[str] = []
    for provider in providers:
        if isinstance(provider, dict):
            summaries.append(str(provider.get("summary") or ""))
    return " ".join(summaries).lower()


def normalize_ai_score_for_strategy(rumor: dict) -> tuple[float, dict]:
    raw_score = _clamp_float((rumor or {}).get("rumor_total_score"), 0.0, -24.0, 24.0)
    confidence = _clamp_float((rumor or {}).get("rumor_avg_confidence"), 0.0, 0.0, 1.0)
    summary_text = _rumor_summaries(rumor or {})
    weak_catalyst = any(
        marker in summary_text
        for marker in (
            "no clear asset-specific catalyst",
            "no meaningful catalyst",
            "provider disabled",
            "api error",
            "cache miss",
            "rate limited",
        )
    )

    adjusted = raw_score
    if adjusted > 0:
        if weak_catalyst:
            adjusted = min(adjusted, 2.0)
        if confidence < 0.55:
            adjusted = min(adjusted, 1.5)
        confidence_multiplier = 0.25 + (0.75 * confidence)
    elif adjusted < 0:
        if weak_catalyst:
            adjusted = max(adjusted, -8.0)
        confidence_multiplier = 0.50 + (0.50 * confidence)
    else:
        confidence_multiplier = 0.0

    effective = round(adjusted * confidence_multiplier, 2)
    return effective, {
        "raw": raw_score,
        "effective": effective,
        "confidence": confidence,
        "weak_catalyst": weak_catalyst,
    }


def combine_strategy_score(indicator_score: float, effective_ai_score: float, raw_ai_score: float, has_position: bool) -> float:
    ai_weight = 0.25
    if has_position and raw_ai_score < 0:
        ai_weight = 0.40
    indicator_weight = 1.0 - ai_weight
    return round((float(indicator_score) * indicator_weight) + (float(effective_ai_score) * ai_weight), 2)


def apply_ai_thresholds_conservatively(regime_params: dict, ai_thresholds: dict) -> dict:
    params = dict(regime_params)
    if not isinstance(ai_thresholds, dict):
        return params

    base_buy = float(params["buy_threshold"])
    base_strong_buy = float(params["strong_buy_threshold"])
    base_sell = float(params["sell_threshold"])
    base_strong_sell = float(params["strong_sell_threshold"])
    base_buy_pct = float(params["buy_pct"])
    base_strong_buy_pct = float(params["strong_buy_pct"])

    buy_threshold = max(base_buy, _clamp_float(ai_thresholds.get("buy_threshold"), base_buy, 1.0, 15.0))
    strong_buy_threshold = max(
        buy_threshold,
        base_strong_buy,
        _clamp_float(ai_thresholds.get("strong_buy_threshold"), base_strong_buy, 1.0, 18.0),
    )
    sell_threshold = max(base_sell, _clamp_float(ai_thresholds.get("sell_threshold"), base_sell, -18.0, -1.0))
    strong_sell_threshold = max(
        base_strong_sell,
        _clamp_float(ai_thresholds.get("strong_sell_threshold"), base_strong_sell, -20.0, -1.0),
    )
    strong_sell_threshold = min(strong_sell_threshold, sell_threshold)

    params.update(
        {
            "buy_threshold": buy_threshold,
            "strong_buy_threshold": strong_buy_threshold,
            "sell_threshold": sell_threshold,
            "strong_sell_threshold": strong_sell_threshold,
            "buy_pct": min(base_buy_pct, _clamp_float(ai_thresholds.get("buy_pct"), base_buy_pct, 0.001, 0.20)),
            "strong_buy_pct": min(
                base_strong_buy_pct,
                _clamp_float(ai_thresholds.get("strong_buy_pct"), base_strong_buy_pct, 0.001, 0.25),
            ),
            "ai_adjusted": True,
        }
    )
    params["strong_buy_pct"] = max(params["strong_buy_pct"], params["buy_pct"])
    return params


def ohlcv_to_df(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp").sort_index()


def build_price_action_snapshot(df: pd.DataFrame, live_price: float) -> dict[str, float | str]:
    """
    Bulk LLM context için yalın bir price action özeti üretir.

    Burada özellikle:
    - son yapı (higher highs / lower lows benzeri)
    - yakın destek / direnç
    - son swing bazlı Fibonacci seviyeleri
    - fiyatın range içindeki konumu
    çıkarılır.
    """
    closed = df.iloc[:-1].copy() if len(df.index) > 1 else df.copy()
    if closed.empty:
        return {}

    short_window = closed.tail(min(len(closed.index), 20))
    swing_window = closed.tail(min(len(closed.index), 48))
    structure_window = closed.tail(min(len(closed.index), 6))

    last_closed = closed.iloc[-1]
    prev_closed = closed.iloc[-2] if len(closed.index) >= 2 else last_closed

    recent_support = float(short_window["low"].min())
    recent_resistance = float(short_window["high"].max())
    swing_low = float(swing_window["low"].min())
    swing_high = float(swing_window["high"].max())
    range_size = max(swing_high - swing_low, 1e-12)

    fib_candidates = [
        ("fib_0.236", swing_high - range_size * 0.236),
        ("fib_0.382", swing_high - range_size * 0.382),
        ("fib_0.500", swing_high - range_size * 0.500),
        ("fib_0.618", swing_high - range_size * 0.618),
        ("fib_0.786", swing_high - range_size * 0.786),
    ]
    level_candidates = [
        ("support_recent", recent_support),
        ("resistance_recent", recent_resistance),
        ("swing_low", swing_low),
        ("swing_high", swing_high),
        *fib_candidates,
    ]

    below_levels = sorted(
        [(label, float(value)) for label, value in level_candidates if float(value) <= live_price],
        key=lambda item: item[1],
        reverse=True,
    )
    above_levels = sorted(
        [(label, float(value)) for label, value in level_candidates if float(value) >= live_price],
        key=lambda item: item[1],
    )

    structure = "range_or_compression"
    if len(structure_window.index) >= 6:
        earlier = structure_window.iloc[:3]
        later = structure_window.iloc[-3:]
        higher_highs = float(later["high"].max()) > float(earlier["high"].max())
        higher_lows = float(later["low"].min()) > float(earlier["low"].min())
        lower_highs = float(later["high"].max()) < float(earlier["high"].max())
        lower_lows = float(later["low"].min()) < float(earlier["low"].min())
        if higher_highs and higher_lows:
            structure = "higher_highs_higher_lows"
        elif lower_highs and lower_lows:
            structure = "lower_highs_lower_lows"

    range_position = min(1.0, max(0.0, (live_price - swing_low) / range_size))
    last_close = float(last_closed["close"] or 0.0)
    prev_close = float(prev_closed["close"] or 0.0)
    last_close_change_pct = ((last_close / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0

    breakout_state = "inside_range"
    if live_price > recent_resistance:
        breakout_state = "breaking_above_recent_resistance"
    elif live_price < recent_support:
        breakout_state = "breaking_below_recent_support"
    elif range_position >= 0.85:
        breakout_state = "near_range_high"
    elif range_position <= 0.15:
        breakout_state = "near_range_low"

    nearest_support_label, nearest_support_value = below_levels[0] if below_levels else ("none", 0.0)
    nearest_resistance_label, nearest_resistance_value = above_levels[0] if above_levels else ("none", 0.0)

    return {
        "structure": structure,
        "recent_support": recent_support,
        "recent_resistance": recent_resistance,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "nearest_support_label": nearest_support_label,
        "nearest_support_value": nearest_support_value,
        "nearest_resistance_label": nearest_resistance_label,
        "nearest_resistance_value": nearest_resistance_value,
        "fib_0_236": float(fib_candidates[0][1]),
        "fib_0_382": float(fib_candidates[1][1]),
        "fib_0_500": float(fib_candidates[2][1]),
        "fib_0_618": float(fib_candidates[3][1]),
        "fib_0_786": float(fib_candidates[4][1]),
        "range_position": range_position,
        "breakout_state": breakout_state,
        "last_close_change_pct": last_close_change_pct,
    }


def format_cycle_report(cycle_started_ms: int, health: dict, portfolio: dict, symbol_lines: list[str]) -> str:
    started = pd.to_datetime(cycle_started_ms, unit="ms", utc=True).tz_convert(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = _telegram_title("Bot Cycle")
    lines += [
        "",
        f"Started: {started}",
        "",
        "Health",
        f"  Overall: {_telegram_bool(health.get('ok'))}",
        f"  Severity: {_telegram_text(health.get('severity', 'NA'))}",
        f"  Exchange: {_telegram_bool(health.get('exchange_ok'))}",
        f"  Public API: {_telegram_bool(health.get('public_ok'))}",
        f"  Private API: {_telegram_bool(health.get('private_ok'))}",
        f"  DB: {_telegram_bool(health.get('db_ok'))}",
        "",
        "Portfolio",
        f"  Total: {float(portfolio.get('total_balance', 0.0)):.2f} USDT",
        f"  Cash: {float(portfolio.get('cash_balance', 0.0)):.2f} USDT",
        f"  Cash ratio: {float(portfolio.get('cash_ratio', 0.0)):.4f}",
        "",
        "Symbols",
    ]
    for line in symbol_lines:
        lines.extend(_format_cycle_symbol_line(line))
    return "\n".join(lines)


def _telegram_title(title: str) -> list[str]:
    clean = str(title or "").strip().upper()
    return [clean, "-" * len(clean)]


def _telegram_bool(value: object) -> str:
    return "yes" if bool(value) else "no"


def _telegram_text(value: object, fallback: str = "none") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _telegram_shorten(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _telegram_wrap(text: object, width: int = 88) -> list[str]:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ["none"]
    return textwrap.wrap(clean, width=width) or [clean]


def _format_cycle_symbol_line(raw_line: str) -> list[str]:
    raw = str(raw_line or "").strip()
    if not raw:
        return []

    parts = [part.strip() for part in raw.split("|")]
    if len(parts) >= 3 and parts[1].upper() == "ERROR":
        return [
            "",
            f"{parts[0]}",
            f"  Error: {_telegram_shorten(parts[2], 180)}",
        ]

    header = parts[0]
    ai_adjusted = "[AI_THRESHOLDS]" in header
    symbol = header.replace("[AI_THRESHOLDS]", "").strip()

    fields: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()

    symbol_label = f"{symbol} [AI]" if ai_adjusted else symbol
    lines = [
        "",
        symbol_label,
        (
            f"  Signal: {fields.get('action', 'N/A')} | "
            f"stance {fields.get('stance', 'N/A')} | "
            f"total {fields.get('total', 'N/A')} | "
            f"streak {fields.get('streak', 'N/A')}"
        ),
        f"  Regime: {fields.get('regime', 'N/A')}",
    ]

    position = fields.get("position")
    if position:
        lines.append(f"  Position: {_telegram_shorten(position, 150)}")

    tpsl = fields.get("tpsl")
    execution = fields.get("exec")
    if tpsl or execution:
        lines.append(
            f"  Risk/Exec: tpsl={_telegram_shorten(tpsl, 58)} | exec={_telegram_shorten(execution, 86)}"
        )

    groq = fields.get("groq")
    if groq:
        lines.append(f"  Groq: {_telegram_shorten(groq, 130)}")

    return lines


def format_status_message(portfolio: dict, trading_control: dict) -> str:
    held_assets = ",".join(portfolio.get("held_assets") or []) or "none"
    lines = _telegram_title("Status")
    lines += [
        "",
        "Portfolio",
        f"  Total: {float(portfolio.get('total_balance', 0.0)):.2f} USDT",
        f"  Cash: {float(portfolio.get('cash_balance', 0.0)):.2f} USDT",
        f"  Cash ratio: {float(portfolio.get('cash_ratio', 0.0)):.4f}",
        f"  Held: {held_assets}",
        "",
        "Trading",
        f"  Panic mode: {_telegram_bool(trading_control.get('panic_mode'))}",
        f"  Paused: {_telegram_bool(trading_control.get('trading_paused'))}",
        f"  Entries blocked: {_telegram_bool(trading_control.get('entries_blocked'))}",
        f"  Block reason: {_telegram_text(trading_control.get('entry_block_reason'))}",
        "",
        "Auto Risk Guard",
        f"  Enabled: {_telegram_bool(trading_control.get('auto_risk_guard_enabled'))}",
        f"  Active: {_telegram_bool(trading_control.get('auto_risk_guard_active'))}",
        f"  Reason: {_telegram_text(trading_control.get('auto_risk_guard_reason'))}",
        f"  Realized today: {float(trading_control.get('auto_risk_realized_today', 0.0)):.2f} USDT",
        f"  Drawdown: {float(trading_control.get('auto_risk_drawdown_pct', 0.0)) * 100:.2f}%",
    ]
    return "\n".join(lines)


def format_health_message(health: dict) -> str:
    lines = _telegram_title("Health")
    lines += [
        "",
        "Summary",
        f"  Overall: {_telegram_bool(health.get('ok'))}",
        f"  Severity: {_telegram_text(health.get('severity', 'NA'))}",
        "",
        "Checks",
        f"  Exchange: {_telegram_bool(health.get('exchange_ok'))}",
        f"  Public API: {_telegram_bool(health.get('public_ok'))}",
        f"  Private API: {_telegram_bool(health.get('private_ok'))}",
        f"  DB: {_telegram_bool(health.get('db_ok'))}",
    ]

    error_lines: list[str] = []
    for label, key in (
        ("Exchange", "exchange_error"),
        ("Public API", "public_error"),
        ("Private API", "private_error"),
        ("DB", "db_error"),
    ):
        value = str(health.get(key) or "").strip()
        if value:
            error_lines.append(f"  {label}: {_telegram_shorten(value, 140)}")

    if error_lines:
        lines += ["", "Errors"]
        lines.extend(error_lines)

    return "\n".join(lines)


def format_params_message(ai_thresholds: dict | None, bot_state_repo: BotStateRepo) -> str:
    lines = _telegram_title("Current Params")
    lines += [
        "",
        "Base Thresholds",
        f"  Buy: {settings.buy_threshold}",
        f"  Strong buy: {settings.strong_buy_threshold}",
        f"  Sell: {settings.sell_threshold}",
        f"  Strong sell: {settings.strong_sell_threshold}",
        "",
        "Base Sizing",
        f"  Buy pct: {float(settings.buy_pct) * 100:.2f}%",
        f"  Strong buy pct: {float(settings.strong_buy_pct) * 100:.2f}%",
        "",
        "Risk Guard",
        f"  Regime enabled: {_telegram_bool(settings.regime_enabled)}",
        f"  Max daily realized loss: {float(settings.max_daily_realized_loss_usdt):.2f} USDT",
        f"  Max daily drawdown: {float(settings.max_daily_drawdown_pct) * 100:.2f}%",
    ]

    lines += ["", "Active AI Thresholds"]
    if ai_thresholds and isinstance(ai_thresholds, dict):
        lines += [
            f"  Buy: {ai_thresholds.get('buy_threshold')}",
            f"  Strong buy: {ai_thresholds.get('strong_buy_threshold')}",
            f"  Sell: {ai_thresholds.get('sell_threshold')}",
            f"  Strong sell: {ai_thresholds.get('strong_sell_threshold')}",
            f"  Buy pct: {float(ai_thresholds.get('buy_pct') or 0.0) * 100:.2f}%",
            f"  Strong buy pct: {float(ai_thresholds.get('strong_buy_pct') or 0.0) * 100:.2f}%",
        ]
        updated_at = ai_thresholds.get("updated_at", 0)
        if updated_at:
            age_hours = (time.time() - float(updated_at)) / 3600
            lines.append(f"  Updated: {age_hours:.1f}h ago")
        if ai_thresholds.get("reasoning"):
            lines += ["  Reason:"]
            lines += [f"    {part}" for part in _telegram_wrap(ai_thresholds.get("reasoning"), width=76)]
    else:
        lines.append("  Not active; using base settings.")

    lines += ["", "Regime State"]
    for symbol in settings.symbols:
        regime = bot_state_repo.get(f"regime_state:{symbol}") or "UNKNOWN"
        lines.append(f"  {symbol}: {regime}")

    return "\n".join(lines)


def format_ai_thresholds_message(ai_thresholds: dict, threshold_model: str) -> str:
    lines = _telegram_title("AI Thresholds Updated")
    if threshold_model:
        lines += ["", f"Model: {threshold_model}"]
    lines += [
        "",
        "Score Thresholds",
        f"  Buy: {ai_thresholds['buy_threshold']:.1f}",
        f"  Strong buy: {ai_thresholds['strong_buy_threshold']:.1f}",
        f"  Sell: {ai_thresholds['sell_threshold']:.1f}",
        f"  Strong sell: {ai_thresholds['strong_sell_threshold']:.1f}",
        "",
        "Sizing",
        f"  Buy pct: {ai_thresholds['buy_pct'] * 100:.2f}%",
        f"  Strong buy pct: {ai_thresholds['strong_buy_pct'] * 100:.2f}%",
        "",
        "Reason",
    ]
    lines += [f"  {part}" for part in _telegram_wrap(ai_thresholds.get("reasoning", ""), width=84)]
    return "\n".join(lines)


def format_groq_sentiment_report(
    rumors: dict,
    refreshed: bool,
    force_groq_refresh: bool,
    bulk_model: str,
) -> tuple[str, bool]:
    lines = _telegram_title("Groq Sentiment Report")
    if bulk_model:
        lines += ["", f"Model: {bulk_model}"]
    if not refreshed:
        lines.append("Status: cached - refresh pending")

    lines += ["", "Signals"]
    has_non_zero = False
    for sym, rumor in rumors.items():
        providers = rumor.get("providers", []) if isinstance(rumor, dict) else []
        groq = next((p for p in providers if p.get("provider") == "groq"), None)
        if not groq:
            continue

        summary = str(groq.get("summary", "") or "").strip()
        score = groq.get("rumor_score", 0)
        if score != 0:
            lines.append(f"  {sym} | {groq.get('stance')} | score {score}")
            lines += [f"    {part}" for part in _telegram_wrap(summary, width=78)[:2]]
            has_non_zero = True
        elif summary and "rate limit" in summary.lower():
            lines.append(f"  {sym} | RATE LIMITED")
            has_non_zero = True

    if not has_non_zero and (refreshed or force_groq_refresh):
        lines.append("  No non-zero Groq signals.")

    return "\n".join(lines), has_non_zero


def normalize_bias(stance: str) -> str:
    """
    TR:
    Stance bilgisini streak bias grubuna cevirir.
    SELL ve STRONG_SELL ayni EXIT ailesine alinır.

    Neden?
    - Sell-side conviction olusunca eski long birikimini tasimak istemeyiz.
    - EXIT sonrasi veya regime flip sonrasi yapiskan streak'i azaltmak isteriz.

    EN:
    Converts a stance into the streak bias family.
    SELL and STRONG_SELL are grouped under the EXIT family.

    Why?
    - When sell-side conviction appears, we do not want to keep old long conviction.
    - After exits or regime flips, we want to reduce sticky streak carryover.
    """
    stance = str(stance).upper()
    if stance in {"BUY", "STRONG_BUY"}:
        return "LONG"
    if stance in {"SELL", "STRONG_SELL"}:
        # TR: Sell ailesini ayri tutuyoruz ki eski BUY streak'i gereksiz tasinmasin.
        # EN: We keep the sell family separate so old BUY streak state is not carried forward unnecessarily.
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


def local_trading_day_context(now_ms: int | None = None) -> tuple[str, int]:
    """
    TR: Istanbul gunu icin hem day_key hem de UTC bazli day-start timestamp uretir.
    EN: Produces both the Istanbul trading day key and the UTC timestamp for that local day start.
    """
    if now_ms is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    else:
        now_utc = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=datetime.timezone.utc)

    now_local = now_utc.astimezone(LOCAL_TZ)
    day_key = now_local.strftime("%Y-%m-%d")
    start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_ms = int(start_of_day_local.astimezone(datetime.timezone.utc).timestamp() * 1000)
    return day_key, start_of_day_ms


def auto_risk_guard_state_key() -> str:
    return "auto_risk_guard_state"


def evaluate_auto_risk_guard(
    bot_state_repo: BotStateRepo,
    fills_repo: FillsRepo,
    portfolio: dict,
    now_ms: int,
    notifier: TelegramNotifier | None = None,
) -> dict[str, float | bool | str]:
    """
    TR:
    Gunluk realized zarar ve gun ici peak-equity drawdown guard'ini degerlendirir.
    Esik asilirsa yeni entry'ler durur, exit'ler ise calismaya devam eder.

    EN:
    Evaluates the daily realized-loss and intraday peak-equity drawdown guard.
    When breached, new entries are blocked while exits continue to work.
    """
    max_daily_realized_loss_usdt = max(0.0, float(getattr(settings, "max_daily_realized_loss_usdt", 0.0) or 0.0))
    max_daily_drawdown_pct = max(0.0, float(getattr(settings, "max_daily_drawdown_pct", 0.0) or 0.0))
    guard_enabled = max_daily_realized_loss_usdt > 0 or max_daily_drawdown_pct > 0

    day_key, start_of_day_ms = local_trading_day_context(now_ms)
    current_equity = float(portfolio.get("total_balance") or 0.0)
    realized_today = float(fills_repo.sum_realized_pnl_since(start_of_day_ms, now_ms))

    raw_state = bot_state_repo.get(auto_risk_guard_state_key())
    prev_state = raw_state if isinstance(raw_state, dict) else {}
    if str(prev_state.get("day_key") or "") != day_key:
        prev_state = {}

    peak_equity = float(prev_state.get("peak_equity") or 0.0)
    if current_equity > 0:
        peak_equity = max(peak_equity, current_equity)

    drawdown_pct = 0.0
    if peak_equity > 0 and current_equity > 0 and current_equity < peak_equity:
        drawdown_pct = (peak_equity - current_equity) / peak_equity

    trigger_reason = ""
    trigger_label = ""
    if guard_enabled:
        if max_daily_realized_loss_usdt > 0 and realized_today <= -max_daily_realized_loss_usdt:
            trigger_reason = "blocked:auto_risk:max_daily_realized_loss"
            trigger_label = "max_daily_realized_loss"
        elif max_daily_drawdown_pct > 0 and peak_equity > 0 and current_equity > 0 and drawdown_pct >= max_daily_drawdown_pct:
            trigger_reason = "blocked:auto_risk:max_daily_drawdown"
            trigger_label = "max_daily_drawdown"

    prev_triggered = bool(prev_state.get("triggered"))
    prev_reason = str(prev_state.get("trigger_reason") or "")
    prev_label = str(prev_state.get("trigger_label") or "")

    # TR: Bu guard bir circuit breaker gibi davranir; ayni gun icinde bir kez patladiysa
    # yeniden acilmasini istemiyoruz.
    # EN: This guard behaves like a circuit breaker; once it trips during the day,
    # we do not want it to reopen entries later on the same day.
    if guard_enabled and prev_triggered and prev_reason:
        trigger_reason = prev_reason
        trigger_label = prev_label or trigger_reason.split(":")[-1]

    triggered = bool(trigger_reason)
    triggered_at_ms = int(prev_state.get("triggered_at_ms") or 0) if triggered else 0

    state: dict[str, float | bool | str] = {
        "enabled": guard_enabled,
        "day_key": day_key,
        "peak_equity": peak_equity,
        "current_equity": current_equity,
        "realized_today": realized_today,
        "drawdown_pct": drawdown_pct,
        "triggered": triggered,
        "trigger_reason": trigger_reason,
        "trigger_label": trigger_label,
        "triggered_at_ms": triggered_at_ms,
        "updated_at_ms": now_ms,
    }

    if triggered and (not prev_triggered or prev_reason != trigger_reason):
        state["triggered_at_ms"] = now_ms
        logging.warning(
            "[AUTO RISK GUARD] day=%s reason=%s realized_today=%.4f limit_realized=%.4f current_equity=%.4f peak_equity=%.4f drawdown_pct=%.4f limit_drawdown=%.4f",
            day_key,
            trigger_label,
            realized_today,
            max_daily_realized_loss_usdt,
            current_equity,
            peak_equity,
            drawdown_pct,
            max_daily_drawdown_pct,
        )
        notify_safe(
            notifier,
            (
                "[AUTO RISK GUARD] TRIGGERED\n"
                f"day={day_key}\n"
                f"reason={trigger_label}\n"
                f"realized_today={realized_today:.2f} USDT\n"
                f"realized_limit={max_daily_realized_loss_usdt:.2f} USDT\n"
                f"equity={current_equity:.2f} USDT\n"
                f"peak_equity={peak_equity:.2f} USDT\n"
                f"drawdown_pct={drawdown_pct * 100:.2f}%\n"
                f"drawdown_limit={max_daily_drawdown_pct * 100:.2f}%\n"
                "effect=new entries blocked; exits still allowed"
            ),
        )

    bot_state_repo.set(auto_risk_guard_state_key(), state, now_ms)
    return state


def get_trading_control_state(bot_state_repo: BotStateRepo) -> dict[str, bool | str | float]:
    """
    Trading'i gerçekten durduracak merkezi gate state'i burada okuyoruz.

    Neden ayrı helper var?
    - panic_mode, trading_paused ve auto risk guard farklı state kaynaklarından geliyor.
    - Ana loop içinde bunlari daginik okumak yerine tek yerden normalize etmek gerekiyor.
    - Böylece log, Telegram status ve execution gate aynı truth source'u kullanıyor.

    Kurallar:
    - panic_mode True ise yeni entry kesin blok.
    - trading_paused True ise yeni entry kesin blok.
    - auto risk guard trigger olduysa yeni entry kesin blok.
    - Bu flag'ler sadece entry/scale-in'i durdurur.
      Exit'ler çalışmaya devam eder; çünkü risk azaltan aksiyonlar pause ile engellenmemeli.
    """
    panic_mode = bool(bot_state_repo.get("panic_mode"))
    trading_paused = bool(bot_state_repo.get("trading_paused"))
    auto_risk_state = bot_state_repo.get(auto_risk_guard_state_key())
    if not isinstance(auto_risk_state, dict):
        auto_risk_state = {}
    auto_risk_guard_active = bool(auto_risk_state.get("triggered"))
    auto_risk_guard_reason = str(auto_risk_state.get("trigger_reason") or "")

    if panic_mode:
        entry_block_reason = "blocked:panic_mode"
    elif trading_paused:
        entry_block_reason = "blocked:trading_paused"
    elif auto_risk_guard_active:
        entry_block_reason = auto_risk_guard_reason or "blocked:auto_risk"
    else:
        entry_block_reason = ""

    return {
        "panic_mode": panic_mode,
        "trading_paused": trading_paused,
        "auto_risk_guard_enabled": bool(auto_risk_state.get("enabled")),
        "auto_risk_guard_active": auto_risk_guard_active,
        "auto_risk_guard_reason": auto_risk_guard_reason,
        "auto_risk_guard_day": str(auto_risk_state.get("day_key") or ""),
        "auto_risk_realized_today": float(auto_risk_state.get("realized_today") or 0.0),
        "auto_risk_drawdown_pct": float(auto_risk_state.get("drawdown_pct") or 0.0),
        "auto_risk_peak_equity": float(auto_risk_state.get("peak_equity") or 0.0),
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
            notify_safe(notifier, format_status_message(portfolio, trading_control))

            return


        # -------------------------
        # PARAMS
        # -------------------------

        if cmd == "/params":

            ai_thresholds = bot_state_repo.get("ai_thresholds")
            notify_safe(notifier, format_params_message(ai_thresholds, bot_state_repo))
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

            notify_safe(notifier, format_health_message(result))

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
    position_manager = PositionManager(settings)
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
            evaluate_auto_risk_guard(
                bot_state_repo=bot_state_repo,
                fills_repo=fills_repo,
                portfolio=portfolio,
                now_ms=cycle_started_ms,
                notifier=notifier,
            )

            # Merkezi trading control state'i cycle başında okuyup logluyoruz.
            # Bu snapshot aynı cycle boyunca entry gate için kullanılıyor.
            # Böylece bir cycle ortasında kararların bir kısmı pause öncesi, bir kısmı pause sonrası gibi davranmaz.
            trading_control = get_trading_control_state(bot_state_repo)
            logging.info(
                "[TRADING CONTROL] panic_mode=%s trading_paused=%s auto_risk_guard_active=%s auto_risk_guard_reason=%s entries_blocked=%s entry_block_reason=%s",
                trading_control["panic_mode"],
                trading_control["trading_paused"],
                trading_control["auto_risk_guard_active"],
                trading_control["auto_risk_guard_reason"] or "none",
                trading_control["entries_blocked"],
                trading_control["entry_block_reason"] or "none",
            )

            rumor_report_needed = False
            regime_data: dict[str, dict] = {}
            market_data: dict[str, dict] = {}
            preloaded_ohlcv: dict[str, list[list[float]]] = {}
            force_groq_refresh = bool(bot_state_repo.get("force_refresh_groq_requested"))
            force_threshold_refresh = bool(bot_state_repo.get("force_refresh_thresholds_requested"))
            
            if settings.llm_enabled:
                for symbol in settings.symbols:
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        last_price, _ = extract_reference_price(ticker)
                        ohlcv_rows = exchange.fetch_ohlcv(symbol, settings.timeframe, settings.ohlcv_limit)
                        preloaded_ohlcv[symbol] = ohlcv_rows
                        price_action = build_price_action_snapshot(ohlcv_to_df(ohlcv_rows), last_price)
                        market_data[symbol] = {
                            "price": last_price,
                            "volume": ticker.get("quoteVolume") or ticker.get("volume") or 0,
                            "change_24h": ticker.get("percentage") or 0,
                            "indicators": {},
                            "price_action": price_action,
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
                    notify_safe(notifier, format_ai_thresholds_message(ai_thresholds, threshold_model))

                refreshed = refresh_all_if_needed(
                    settings.symbols,
                    market_data if market_data else None,
                    force=force_groq_refresh,
                )
                rumors = get_cached_rumors(settings.symbols)
                llm_models = get_last_llm_models()
                bulk_model = (
                    get_last_bulk_refresh_model()
                    or llm_models.get("bulk_refresh")
                    or str(getattr(settings, "groq_model", "") or "").strip()
                )
                sentiment_message, has_non_zero = format_groq_sentiment_report(
                    rumors,
                    refreshed,
                    force_groq_refresh,
                    bulk_model,
                )
                if has_non_zero or refreshed or force_groq_refresh:
                    notify_safe(notifier, sentiment_message)

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

                    ohlcv_rows = preloaded_ohlcv.get(symbol)
                    if not ohlcv_rows:
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
                    ai_score = float(rumor["rumor_total_score"])
                    effective_ai_score, ai_score_diag = normalize_ai_score_for_strategy(rumor)
                    total_score = combine_strategy_score(
                        indicator_score=indicator_score,
                        effective_ai_score=effective_ai_score,
                        raw_ai_score=ai_score,
                        has_position=has_position,
                    )

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
                            regime_params = apply_ai_thresholds_conservatively(regime_params, ai_thresholds)

                        regime_data[symbol] = regime_diag
                        if market_data.get(symbol):
                            market_data[symbol]["indicators"] = indicator_details

                        logging.info(f"[REGIME RAW] {symbol} prev={_prev_regime} diag={regime_diag} params={regime_params}")

                    logging.info(
                        f"[DEBUG SCORE] symbol={symbol} indicator={indicator_score}, ai_score={ai_score}, effective_ai_score={effective_ai_score}, ai_diag={ai_score_diag}, details={indicator_details}, rumor={rumor}, total={total_score}"
                    )
                    signal = evaluate_signal(total_score, regime_params)
                    ai_tag = " [AI_THRESHOLDS]" if regime_params.get("ai_adjusted") else ""
                    logging.info(f"[DEBUG SIGNAL]{ai_tag} {symbol} signal={signal}")

                    action = str(signal["action"]).upper()
                    fraction = float(signal["fraction"])
                    stance = str(signal["stance"]).upper()

                    # P0.1.1:
                    # panic_mode veya auto risk guard aktifken streak'in artmasına artık izin vermiyoruz.
                    #
                    # Eski kötü davranış neydi?
                    # - panic ya da auto risk guard aktif
                    # - entry'ler blocked:*
                    # - ama update_signal_streak yine çalışıyor
                    # - yani bot risk almadan conviction biriktiriyordu
                    # - panic kapanınca bir anda yüksek streak ile agresif entry / scale-in
                    #   denemesi çıkabiliyordu
                    #
                    # Bu mimari olarak yanlıştı. Çünkü bu durumlar basit bir "pause" değil,
                    # risk resetidir. Sistem o anda mevcut conviction'ı çöpe atmalıdır.
                    #
                    # Bu yüzden burada davranış ikiye ayrıldı:
                    # 1) risk reset yok -> normal streak güncelle
                    # 2) risk reset var -> streak'i zorla 0'a çek ve NEUTRAL state persist et
                    #
                    # Neden cycle içinde de tekrar resetliyoruz?
                    # - Çünkü kullanıcı /panic komutunu verdikten sonra bot açık kalabilir,
                    #   restart olabilir, /trigger atabilir, yeni cycle çalışabilir.
                    # - Her cycle'da panic aktifse state'in tekrar kirlenmediğini garanti etmek istiyoruz.
                    # - Bu sayede panic aktif olduğu sürece streak tekrar büyüyemez.
                    if trading_control["panic_mode"] or trading_control["auto_risk_guard_active"]:
                        streak = reset_signal_streak(
                            bot_state_repo,
                            signal_state,
                            symbol,
                            "panic_mode_active_cycle" if trading_control["panic_mode"] else "auto_risk_guard_active_cycle",
                        )
                    else:
                        streak = update_signal_streak(bot_state_repo, signal_state, symbol, stance)

                    order_result = None
                    execution_note = "no_order"
                    tpsl_note = "none"

                    live_bar_high = float(df["high"].iloc[-1]) if len(df.index) > 0 else last_price
                    tpsl_decision = tpsl_engine.evaluate(
                        symbol,
                        position,
                        last_price,
                        high_price=max(last_price, live_bar_high),
                    )
                    if tpsl_decision["triggered"]:
                        action = str(tpsl_decision["action"]).upper()
                        stance = "SELL"
                        tpsl_note = (
                            f"{tpsl_decision['reason']}"
                            f" sl={float(tpsl_decision['stop_loss_price'] or 0.0):.6f}"
                            f" ptp={float(tpsl_decision['partial_take_profit_price'] or 0.0):.6f}"
                            f" ftp={float(tpsl_decision['full_take_profit_price'] or 0.0):.6f}"
                            f" peak={float(tpsl_decision.get('peak_price') or 0.0):.6f}"
                            f" peak_pnl_pct={float(tpsl_decision.get('peak_pnl_pct') or 0.0) * 100:.2f}%"
                            f" retrace_pct={float(tpsl_decision.get('trailing_retrace_pct') or 0.0) * 100:.2f}%"
                            f" pnl_pct={float(tpsl_decision['pnl_pct'] or 0.0) * 100:.2f}%"
                        )
                        logging.info(f"[TPSL TRIGGER] {symbol} action={action} {tpsl_note}")
                    elif has_position:
                        tpsl_note = str(tpsl_decision.get("reason") or "tpsl_not_triggered")
                        logging.info(
                            f"[TPSL CHECK] {symbol} sl={tpsl_decision['stop_loss_price']} be={tpsl_decision.get('break_even_stop_price')} ptp={tpsl_decision['partial_take_profit_price']} ftp={tpsl_decision['full_take_profit_price']} peak={tpsl_decision.get('peak_price')} peak_pnl_pct={tpsl_decision.get('peak_pnl_pct')} retrace_pct={tpsl_decision.get('trailing_retrace_pct')} pnl_pct={tpsl_decision['pnl_pct']} reason={tpsl_decision['reason']}"
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
                                scale_in_allowed, scale_in_reason = position_manager.can_scale_in(position, last_price)
                                if not scale_in_allowed:
                                    execution_note = f"blocked:scale_in:{scale_in_reason}"
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
                        elif (
                            execution_note.startswith("blocked:scale_in:no_scale_in_on_loser")
                            or execution_note.startswith("blocked:max_scale_in_count")
                            or execution_note.startswith("blocked:scale_in:invalid_avg")
                        ):
                            streak = decay_signal_streak(
                                bot_state_repo,
                                signal_state,
                                symbol,
                                max(1, int(getattr(settings, "scale_in_trigger_streak", 3) or 3)),
                                execution_note,
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
            if is_transient_exchange_error(exc):
                status = "SKIPPED"
                logging.warning("[CYCLE SKIPPED] transient exchange error: %s", exc, exc_info=True)
                crash_text = f"[CYCLE SKIPPED]\ntransient_exchange_error\n{str(exc)}"
                notify_safe(notifier, crash_text)
                db.execute(
                    "INSERT INTO cycle_reports(cycle_started_ms, cycle_finished_ms, summary, status) VALUES (?, ?, ?, ?)",
                    (cycle_started_ms, int(time.time() * 1000), crash_text, status),
                )
            else:
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
