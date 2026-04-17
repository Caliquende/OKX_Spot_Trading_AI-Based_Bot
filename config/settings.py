from __future__ import annotations

"""
KONFIGURASYON KATMANI / CONFIGURATION LAYER
===========================================

TR:
Bu dosya `.env` -> `Settings` donusumunu yapar.
Botun davranisini degistiren neredeyse tum esikler burada toplanir.

Ana kategori basliklari:
- Exchange / sandbox / dry_run
- Loop ve veri cekme ayarlari
- Entry / exit threshold'lari
- Scale-in parametreleri
- TP/SL parametreleri
- Regime engine esikleri
- Telegram / LLM / debug ayarlari

Onemli not:
Settings katmani statik config icindir. Runtime state burada uretilmemelidir.
Canli calisirken degisen bir bilgi varsa (ornegin streak count, bot state, lock state),
orasi artik DB veya runtime katmanidir; settings degildir.

EN:
This file converts `.env` values into the `Settings` object.
Almost every threshold that changes bot behavior is collected here.

Main categories:
- Exchange / sandbox / dry_run
- Loop and market-data settings
- Entry / exit thresholds
- Scale-in parameters
- TP/SL parameters
- Regime engine thresholds
- Telegram / LLM / debug settings

Important note:
The settings layer is for static configuration.
Runtime state must not be produced here.
If a value changes while the bot is running, it belongs in the DB or runtime layer, not in settings.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv, find_dotenv


def _load_project_env() -> None:
    """
    TR:
    Env yukleme fallback zinciri.

    Neden var?
    - Bazi paketlemelerde .env gizli dosya oldugu icin eksik veya yeniden adlandirilmis gelebiliyor.
    - Kullanici `.env` ya da `_.env` benzeri varyant kullanabiliyor.
    - Calisma dizini degistiginde dotenv'in varsayilan kesfi bosa dusabiliyor.

    EN:
    Fallback chain for loading environment files.

    Why does this exist?
    - In some setups, `.env` may be missing or renamed.
    - Users may keep settings in `.env` or `_.env`.
    - Default dotenv discovery may silently fail when the working directory changes.
    """
    # TR: once dotenv'in kendi standart kesfini deniyoruz.
    # EN: first we try dotenv's normal discovery mechanism.
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)

    # TR: sonra proje kokunde acik fallback adaylarini tek tek deniyoruz.
    # EN: then we try explicit fallback candidates in the project root.
    project_root = Path(__file__).resolve().parent.parent
    for candidate in (project_root / '.env', project_root / '_.env', Path.cwd() / '.env', Path.cwd() / '_.env'):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_project_env()


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    # TR: Tek bir env degiskenini oku. required=True ise eksikse hata ver.
    # EN: Read a single env variable. If required=True and it is missing, raise an error.
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return "" if value is None else str(value)


def _env_first(names: list[str], default: str | None = None, required: bool = False) -> str:
    # TR: Birden fazla isim dene; ilk dolu olani kullan. Eski/yeni env uyumlulugu icin faydalidir.
    # EN: Try multiple env names and use the first non-empty one. Useful for backward compatibility.
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value)
    if required and (default is None or str(default).strip() == ""):
        raise RuntimeError(f"Missing required env vars: {', '.join(names)}")
    return "" if default is None else str(default)


def _env_bool(name: str, default: str = "false") -> bool:
    # TR: "true/1/yes/on" gibi degerleri Python bool'a cevirir.
    # EN: Converts string values such as "true/1/yes/on" into a Python boolean.
    return _env(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: str) -> float:
    # TR: Sayisal env degerini float'a cevir.
    # EN: Convert a numeric env value into float.
    return float(_env(name, default))


def _env_int(name: str, default: str) -> int:
    # TR: Sayisal env degerini int'e cevir.
    # EN: Convert a numeric env value into int.
    return int(_env(name, default))


def _env_list(name: str, default: str) -> list[str]:
    # TR: Virgulle ayrilmis env degerini listeye cevir.
    # EN: Convert a comma-separated env value into a Python list.
    raw = _env(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    """
    Bu sınıf .env dosyasının Python tarafındaki karşılığıdır.

    Yani:
    - `.env` içindeki metinler burada tipli alanlara dönüşür
    - bot diğer katmanlarda bu sınıftan okuyarak davranır
    """
    # ---------------- EXCHANGE ----------------
    okx_api_key: str = _env("OKX_API_KEY", required=True)
    okx_secret: str = _env("OKX_SECRET", required=True)
    okx_passphrase: str = _env("OKX_PASSPHRASE", required=True)
    okx_sandbox: bool = _env_bool("OKX_SANDBOX", "true")
    okx_td_mode: str = _env("OKX_TD_MODE", "auto").strip().lower()

    # ---------------- CORE ----------------
    symbols: list[str] = field(default_factory=lambda: _env_list("SYMBOLS", "BTC/USDT"))
    timeframe: str = _env("TIMEFRAME", "15m")
    ohlcv_limit: int = _env_int("OHLCV_LIMIT", "200")
    loop_seconds: int = _env_int("LOOP_SECONDS", "60")
    db_path: str = _env_first(["DB_PATH", "SQLITE_PATH"], "trading.db")
    dry_run: bool = _env_bool("DRY_RUN", "true")

    # ---------------- ORDER LIMITS ----------------
    min_order_quote_usdt: float = _env_float("MIN_ORDER_QUOTE_USDT", "10")
    min_free_usdt: float = _env_float("MIN_FREE_USDT", "25")

    # ---------------- POSITION LIMITS ----------------
    max_open_positions: int = _env_int("MAX_OPEN_POSITIONS", "3")
    max_symbol_exposure_pct: float = _env_float("MAX_SYMBOL_EXPOSURE_PCT", "0.35")
    max_total_exposure_pct: float = _env_float("MAX_TOTAL_EXPOSURE_PCT", "0.90")
    max_single_trade_pct: float = _env_float("MAX_SINGLE_TRADE_PCT", "0.10")
    max_daily_realized_loss_usdt: float = _env_float("MAX_DAILY_REALIZED_LOSS_USDT", "0")
    max_daily_drawdown_pct: float = _env_float("MAX_DAILY_DRAWDOWN_PCT", "0")

    # ---------------- BASE ENTRY SIZING ----------------
    buy_pct: float = _env_float("BUY_PCT", "0.04")
    strong_buy_pct: float = _env_float("STRONG_BUY_PCT", "0.08")

    # ---------------- SCALE-IN ----------------
    scale_in_enabled: bool = _env_bool("SCALE_IN_ENABLED", "true")
    scale_in_trigger_streak: int = _env_int("SCALE_IN_TRIGGER_STREAK", "2")
    strong_scale_in_trigger_streak: int = _env_int("STRONG_SCALE_IN_TRIGGER_STREAK", "2")
    scale_in_buy_pct: float = _env_float("SCALE_IN_BUY_PCT", "0.10")
    strong_scale_in_buy_pct: float = _env_float("STRONG_SCALE_IN_BUY_PCT", "0.20")
    max_scale_in_count: int = _env_int("MAX_SCALE_IN_COUNT", "3")

    # ---------------- SELL ----------------
    partial_sell_ratio: float = _env_float("PARTIAL_SELL_RATIO", "0.5")

    # ---------------- SIGNAL THRESHOLDS ----------------
    buy_threshold: float = _env_float("BUY_THRESHOLD", "3")
    strong_buy_threshold: float = _env_float("STRONG_BUY_THRESHOLD", "5")
    sell_threshold: float = _env_float("SELL_THRESHOLD", "-3")
    strong_sell_threshold: float = _env_float("STRONG_SELL_THRESHOLD", "-5")

    # ---------------- TP / SL ----------------
    tpsl_enabled: bool = _env_bool("TPSL_ENABLED", "true")
    stop_loss_pct: float = _env_float("STOP_LOSS_PCT", "0.10")
    partial_take_profit_enabled: bool = _env_bool("PARTIAL_TAKE_PROFIT_ENABLED", "true")
    partial_take_profit_pct: float = _env_float("PARTIAL_TAKE_PROFIT_PCT", "0.05")
    full_take_profit_enabled: bool = _env_bool("FULL_TAKE_PROFIT_ENABLED", "true")
    full_take_profit_pct: float = _env_float("FULL_TAKE_PROFIT_PCT", "0.12")
    break_even_stop_enabled: bool = _env_bool("BREAK_EVEN_STOP_ENABLED", "true")
    break_even_activation_pct: float = _env_float("BREAK_EVEN_ACTIVATION_PCT", "0.03")
    break_even_buffer_pct: float = _env_float("BREAK_EVEN_BUFFER_PCT", "0.002")
    trailing_take_profit_enabled: bool = _env_bool("TRAILING_TAKE_PROFIT_ENABLED", "true")
    trailing_take_profit_activation_pct: float = _env_float("TRAILING_TAKE_PROFIT_ACTIVATION_PCT", "0.05")
    trailing_take_profit_giveback_pct: float = _env_float("TRAILING_TAKE_PROFIT_GIVEBACK_PCT", "0.02")

    # ---------------- REGIME ENGINE ----------------
    regime_enabled: bool = _env_bool("REGIME_ENABLED", "false")
    regime_trend_adx_min: float = _env_float("REGIME_TREND_ADX_MIN", "24")
    regime_range_adx_max: float = _env_float("REGIME_RANGE_ADX_MAX", "18")
    regime_volatile_atr_ratio_min: float = _env_float("REGIME_VOLATILE_ATR_RATIO_MIN", "0.018")
    regime_chop_adx_max: float = _env_float("REGIME_CHOP_ADX_MAX", "20")
    regime_chop_ema_dist_max: float = _env_float("REGIME_CHOP_EMA_DIST_MAX", "0.006")
    regime_trend_slope_lookback: int = _env_int("REGIME_TREND_SLOPE_LOOKBACK", "6")
    regime_trend_persistence_min: float = _env_float("REGIME_TREND_PERSISTENCE_MIN", "0.60")

    regime_buy_threshold_trend: float = _env_float("REGIME_BUY_THRESHOLD_TREND", "3")
    regime_strong_buy_threshold_trend: float = _env_float("REGIME_STRONG_BUY_THRESHOLD_TREND", "5")
    regime_sell_threshold_trend: float = _env_float("REGIME_SELL_THRESHOLD_TREND", "-3")
    regime_strong_sell_threshold_trend: float = _env_float("REGIME_STRONG_SELL_THRESHOLD_TREND", "-5")
    regime_buy_pct_trend: float = _env_float("REGIME_BUY_PCT_TREND", "0.05")
    regime_strong_buy_pct_trend: float = _env_float("REGIME_STRONG_BUY_PCT_TREND", "0.10")

    regime_buy_threshold_range: float = _env_float("REGIME_BUY_THRESHOLD_RANGE", "4")
    regime_strong_buy_threshold_range: float = _env_float("REGIME_STRONG_BUY_THRESHOLD_RANGE", "6")
    regime_sell_threshold_range: float = _env_float("REGIME_SELL_THRESHOLD_RANGE", "-4")
    regime_strong_sell_threshold_range: float = _env_float("REGIME_STRONG_SELL_THRESHOLD_RANGE", "-6")
    regime_buy_pct_range: float = _env_float("REGIME_BUY_PCT_RANGE", "0.03")
    regime_strong_buy_pct_range: float = _env_float("REGIME_STRONG_BUY_PCT_RANGE", "0.06")

    regime_buy_threshold_chop: float = _env_float("REGIME_BUY_THRESHOLD_CHOP", "5")
    regime_strong_buy_threshold_chop: float = _env_float("REGIME_STRONG_BUY_THRESHOLD_CHOP", "7")
    regime_sell_threshold_chop: float = _env_float("REGIME_SELL_THRESHOLD_CHOP", "-5")
    regime_strong_sell_threshold_chop: float = _env_float("REGIME_STRONG_SELL_THRESHOLD_CHOP", "-7")
    regime_buy_pct_chop: float = _env_float("REGIME_BUY_PCT_CHOP", "0.02")
    regime_strong_buy_pct_chop: float = _env_float("REGIME_STRONG_BUY_PCT_CHOP", "0.04")

    regime_buy_threshold_volatile: float = _env_float("REGIME_BUY_THRESHOLD_VOLATILE", "4")
    regime_strong_buy_threshold_volatile: float = _env_float("REGIME_STRONG_BUY_THRESHOLD_VOLATILE", "8")
    regime_sell_threshold_volatile: float = _env_float("REGIME_SELL_THRESHOLD_VOLATILE", "-4")
    regime_strong_sell_threshold_volatile: float = _env_float("REGIME_STRONG_SELL_THRESHOLD_VOLATILE", "-8")
    regime_buy_pct_volatile: float = _env_float("REGIME_BUY_PCT_VOLATILE", "0.025")
    regime_strong_buy_pct_volatile: float = _env_float("REGIME_STRONG_BUY_PCT_VOLATILE", "0.05")

    # ---------------- RECON / POSITION SOURCE ----------------
    position_source_mode: str = _env("POSITION_SOURCE_MODE", "hybrid").strip().lower()
    sandbox_ignore_balance_zero: bool = _env_bool("SANDBOX_IGNORE_BALANCE_ZERO", "true")
    live_force_close_on_zero_balance: bool = _env_bool("LIVE_FORCE_CLOSE_ON_ZERO_BALANCE", "true")
    allow_balance_only_positions: bool = _env_bool("ALLOW_BALANCE_ONLY_POSITIONS", "true")
    reconcile_since_ms: int = _env_int("RECONCILE_SINCE_MS", "0")
    reconcile_epsilon: float = _env_float("RECONCILE_EPSILON", "0.000000000001")
    min_position_value_usdt: float = _env_float("MIN_POSITION_VALUE_USDT", "5")
    reconcile_warn_abs_quote_usdt: float = _env_float("RECONCILE_WARN_ABS_QUOTE_USDT", "5")
    reconcile_warn_ratio: float = _env_float("RECONCILE_WARN_RATIO", "0.10")
    reconcile_soft_zero_multiplier: float = _env_float("RECONCILE_SOFT_ZERO_MULTIPLIER", "3.0")
    preserve_position_on_balance_fetch_error: bool = _env_bool("PRESERVE_POSITION_ON_BALANCE_FETCH_ERROR", "true")
    reconcile_rebase_after_mismatch_count: int = _env_int("RECONCILE_REBASE_AFTER_MISMATCH_COUNT", "3")

    # ---------------- COOLDOWN ----------------
    symbol_cooldown_minutes: float = _env_float("SYMBOL_COOLDOWN_MINUTES", "60")
    scale_in_cooldown_minutes: float = _env_float("SCALE_IN_COOLDOWN_MINUTES", "2")
    exit_cooldown_minutes: float = _env_float("EXIT_COOLDOWN_MINUTES", "5")
    unknown_submission_lock_minutes: float = _env_float("UNKNOWN_SUBMISSION_LOCK_MINUTES", "2")

    # ---------------- TELEGRAM ----------------
    telegram_token: str = _env("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = _env("TELEGRAM_CHAT_ID", "")
    notify_every_cycle: bool = _env_bool("NOTIFY_EVERY_CYCLE", "false")
    telegram_poll_interval_seconds: int = _env_int("TELEGRAM_POLL_INTERVAL_SECONDS", "5")

    # ---------------- LLM CORE FLAG ----------------
    llm_enabled: bool = _env_bool("LLM_ENABLED", "false")

    # ---------------- LLM API KEYS ----------------
    openai_api_key: str = _env("OPENAI_API_KEY", "")
    gemini_api_key: str = _env("GEMINI_API_KEY", "")
    claude_api_key: str = _env("CLAUDE_API_KEY", "")
    perplexity_api_key: str = _env("PERPLEXITY_API_KEY", "")
    groq_api_key: str = _env("GROQ_API_KEY", "")
    exa_api_key: str = _env("EXA_API_KEY", "")
    
    coingecko_api_key: str = _env("COINGECKO_API_KEY", "")
    coingecko_demo_api_key: str = _env("COINGECKO_DEMO_API_KEY", "")
    coingecko_pro_api_key: str = _env("COINGECKO_PRO_API_KEY", "")

    # ---------------- LLM MODELS ----------------
    openai_model: str = _env("OPENAI_MODEL", "")
    gemini_model: str = _env("GEMINI_MODEL", "")
    claude_model: str = _env("CLAUDE_MODEL", "")
    perplexity_model: str = _env("PERPLEXITY_MODEL", "")
    groq_model: str = _env("GROQ_MODEL", "")
    groq_fallback_model: str = _env("GROQ_FALLBACK_MODEL", "")
    groq_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_MODEL", "")
    groq_fallback_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_FALLBACK_MODEL", "")
    groq_fallback_fallback_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_FALLBACK_FALLBACK_MODEL", "")
    groq_cache_ttl_seconds: int = _env_int("GROQ_CACHE_TTL_SECONDS", "36000")
    threshold_update_ttl_seconds: int = _env_int("THRESHOLD_UPDATE_TTL_SECONDS", "36000")
    research_context_max_chars: int = _env_int("RESEARCH_CONTEXT_MAX_CHARS", "700")
    bulk_refresh_max_context_chars_per_symbol: int = _env_int("BULK_REFRESH_MAX_CONTEXT_CHARS_PER_SYMBOL", "420")
    threshold_snapshot_max_symbols: int = _env_int("THRESHOLD_SNAPSHOT_MAX_SYMBOLS", "5")

    # ---------------- SIGNAL STREAK ----------------
    signal_hold_decay_after: int = _env_int("SIGNAL_HOLD_DECAY_AFTER", "2")
    signal_hold_decay_step: int = _env_int("SIGNAL_HOLD_DECAY_STEP", "2")
    signal_streak_cap: int = _env_int("SIGNAL_STREAK_CAP", "12")

    # ---------------- DEBUG ----------------
    log_level: str = _env("LOG_LEVEL", "INFO")


settings = Settings()
