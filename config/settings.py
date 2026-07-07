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


STRATEGY_PROFILE_DEFAULTS: dict[str, dict[str, float | int]] = {
    "conservative": {
        "buy_threshold": 5.0,
        "strong_buy_threshold": 8.0,
        "sell_threshold": -3.5,
        "strong_sell_threshold": -6.0,
        "buy_pct": 0.01,
        "strong_buy_pct": 0.02,
        "max_symbol_exposure_pct": 0.10,
        "max_total_exposure_pct": 0.35,
        "max_single_trade_pct": 0.015,
        "max_daily_drawdown_pct": 0.025,
        "scale_in_trigger_streak": 3,
        "strong_scale_in_trigger_streak": 3,
        "scale_in_buy_pct": 0.005,
        "strong_scale_in_buy_pct": 0.01,
        "max_scale_in_count": 1,
        "stop_loss_pct": 0.025,
        "partial_take_profit_pct": 0.02,
        "full_take_profit_pct": 0.045,
        "break_even_activation_pct": 0.015,
        "trailing_take_profit_activation_pct": 0.02,
        "trailing_take_profit_giveback_pct": 0.008,
        "regime_buy_threshold_trend": 4.5,
        "regime_strong_buy_threshold_trend": 7.0,
        "regime_buy_pct_trend": 0.012,
        "regime_strong_buy_pct_trend": 0.02,
        "regime_buy_threshold_range": 5.5,
        "regime_strong_buy_threshold_range": 8.0,
        "regime_buy_pct_range": 0.008,
        "regime_strong_buy_pct_range": 0.015,
        "regime_buy_threshold_chop": 7.0,
        "regime_strong_buy_threshold_chop": 9.0,
        "regime_buy_pct_chop": 0.005,
        "regime_strong_buy_pct_chop": 0.01,
        "regime_buy_threshold_volatile": 6.0,
        "regime_strong_buy_threshold_volatile": 9.0,
        "regime_buy_pct_volatile": 0.008,
        "regime_strong_buy_pct_volatile": 0.015,
    },
    "balanced": {
        "buy_threshold": 4.0,
        "strong_buy_threshold": 8.0,
        "sell_threshold": -4.0,
        "strong_sell_threshold": -9.0,
        "buy_pct": 0.02,
        "strong_buy_pct": 0.04,
        "max_symbol_exposure_pct": 0.15,
        "max_total_exposure_pct": 0.50,
        "max_single_trade_pct": 0.025,
        "max_daily_drawdown_pct": 0.035,
        "scale_in_trigger_streak": 3,
        "strong_scale_in_trigger_streak": 3,
        "scale_in_buy_pct": 0.008,
        "strong_scale_in_buy_pct": 0.015,
        "max_scale_in_count": 2,
        "stop_loss_pct": 0.035,
        "partial_take_profit_pct": 0.025,
        "full_take_profit_pct": 0.06,
        "break_even_activation_pct": 0.018,
        "trailing_take_profit_activation_pct": 0.025,
        "trailing_take_profit_giveback_pct": 0.01,
        "regime_buy_threshold_trend": 4.0,
        "regime_strong_buy_threshold_trend": 7.0,
        "regime_buy_pct_trend": 0.02,
        "regime_strong_buy_pct_trend": 0.035,
        "regime_buy_threshold_range": 5.0,
        "regime_strong_buy_threshold_range": 8.0,
        "regime_buy_pct_range": 0.015,
        "regime_strong_buy_pct_range": 0.025,
        "regime_buy_threshold_chop": 6.5,
        "regime_strong_buy_threshold_chop": 8.5,
        "regime_buy_pct_chop": 0.008,
        "regime_strong_buy_pct_chop": 0.015,
        "regime_buy_threshold_volatile": 5.5,
        "regime_strong_buy_threshold_volatile": 8.5,
        "regime_buy_pct_volatile": 0.012,
        "regime_strong_buy_pct_volatile": 0.025,
    },
    "aggressive": {
        "buy_threshold": 3.5,
        "strong_buy_threshold": 6.0,
        "sell_threshold": -4.0,
        "strong_sell_threshold": -8.0,
        "buy_pct": 0.02,
        "strong_buy_pct": 0.04,
        "max_symbol_exposure_pct": 0.12,
        "max_total_exposure_pct": 0.95,
        "max_single_trade_pct": 0.04,
        "max_daily_drawdown_pct": 0.05,
        "scale_in_trigger_streak": 2,
        "strong_scale_in_trigger_streak": 2,
        "scale_in_buy_pct": 0.01,
        "strong_scale_in_buy_pct": 0.02,
        "max_scale_in_count": 2,
        "stop_loss_pct": 0.032,
        "partial_take_profit_pct": 0.025,
        "full_take_profit_pct": 0.06,
        "break_even_activation_pct": 0.018,
        "trailing_take_profit_activation_pct": 0.025,
        "trailing_take_profit_giveback_pct": 0.012,
        "regime_buy_threshold_trend": 3.5,
        "regime_strong_buy_threshold_trend": 6.0,
        "regime_buy_pct_trend": 0.025,
        "regime_strong_buy_pct_trend": 0.04,
        "regime_buy_threshold_range": 4.0,
        "regime_strong_buy_threshold_range": 7.0,
        "regime_buy_pct_range": 0.018,
        "regime_strong_buy_pct_range": 0.03,
        "regime_buy_threshold_chop": 6.5,
        "regime_strong_buy_threshold_chop": 8.0,
        "regime_buy_pct_chop": 0.01,
        "regime_strong_buy_pct_chop": 0.018,
        "regime_buy_threshold_volatile": 5.0,
        "regime_strong_buy_threshold_volatile": 8.0,
        "regime_buy_pct_volatile": 0.02,
        "regime_strong_buy_pct_volatile": 0.035,
    },
    "scalper": {
        "buy_threshold": 3.0,
        "strong_buy_threshold": 5.0,
        "sell_threshold": -3.0,
        "strong_sell_threshold": -6.0,
        "buy_pct": 0.012,
        "strong_buy_pct": 0.025,
        "max_symbol_exposure_pct": 0.12,
        "max_total_exposure_pct": 0.45,
        "max_single_trade_pct": 0.02,
        "max_daily_drawdown_pct": 0.03,
        "scale_in_trigger_streak": 2,
        "strong_scale_in_trigger_streak": 2,
        "scale_in_buy_pct": 0.006,
        "strong_scale_in_buy_pct": 0.012,
        "max_scale_in_count": 2,
        "stop_loss_pct": 0.018,
        "partial_take_profit_pct": 0.012,
        "full_take_profit_pct": 0.03,
        "break_even_activation_pct": 0.01,
        "trailing_take_profit_activation_pct": 0.014,
        "trailing_take_profit_giveback_pct": 0.006,
        "regime_buy_threshold_trend": 3.0,
        "regime_strong_buy_threshold_trend": 5.0,
        "regime_buy_pct_trend": 0.015,
        "regime_strong_buy_pct_trend": 0.025,
        "regime_buy_threshold_range": 3.8,
        "regime_strong_buy_threshold_range": 6.0,
        "regime_buy_pct_range": 0.012,
        "regime_strong_buy_pct_range": 0.02,
        "regime_buy_threshold_chop": 5.5,
        "regime_strong_buy_threshold_chop": 7.0,
        "regime_buy_pct_chop": 0.006,
        "regime_strong_buy_pct_chop": 0.012,
        "regime_buy_threshold_volatile": 5.0,
        "regime_strong_buy_threshold_volatile": 7.0,
        "regime_buy_pct_volatile": 0.008,
        "regime_strong_buy_pct_volatile": 0.016,
    },
}


def strategy_profile_names() -> set[str]:
    return set(STRATEGY_PROFILE_DEFAULTS)


def get_strategy_profile_values(profile: str) -> dict[str, float | int]:
    normalized = str(profile or "").strip().lower()
    if normalized not in STRATEGY_PROFILE_DEFAULTS:
        allowed = ", ".join(sorted(STRATEGY_PROFILE_DEFAULTS))
        raise RuntimeError(f"STRATEGY_PROFILE must be one of: {allowed}")

    values: dict[str, float | int] = {}
    for name, default_value in STRATEGY_PROFILE_DEFAULTS[normalized].items():
        raw_value = _env(f"STRATEGY_{normalized.upper()}_{name.upper()}", str(default_value))
        values[name] = int(raw_value) if isinstance(default_value, int) else float(raw_value)
    return values


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
    strategy_profile: str = _env("STRATEGY_PROFILE", "balanced").strip().lower()
    strategy_profile_mode: str = _env("STRATEGY_PROFILE_MODE", "dynamic").strip().lower()

    # ---------------- ORDER LIMITS ----------------
    min_order_quote_usdt: float = _env_float("MIN_ORDER_QUOTE_USDT", "10")
    min_trade_pct: float = _env_float("MIN_TRADE_PCT", "0.04")
    min_trade_quote_buffer_pct: float = _env_float("MIN_TRADE_QUOTE_BUFFER_PCT", "0.02")
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
    scale_in_pullback_override_streak: int = _env_int("SCALE_IN_PULLBACK_OVERRIDE_STREAK", "4")

    # ---------------- SELL ----------------
    partial_sell_ratio: float = _env_float("PARTIAL_SELL_RATIO", "0.5")

    # ---------------- SIGNAL THRESHOLDS ----------------
    buy_threshold: float = _env_float("BUY_THRESHOLD", "3")
    strong_buy_threshold: float = _env_float("STRONG_BUY_THRESHOLD", "5")
    sell_threshold: float = _env_float("SELL_THRESHOLD", "-3")
    strong_sell_threshold: float = _env_float("STRONG_SELL_THRESHOLD", "-5")

    # ---------------- TP / SL ----------------
    tpsl_enabled: bool = _env_bool("TPSL_ENABLED", "true")
    stop_loss_pct: float = _env_float("STOP_LOSS_PCT", "0.045")
    partial_take_profit_enabled: bool = _env_bool("PARTIAL_TAKE_PROFIT_ENABLED", "true")
    partial_take_profit_pct: float = _env_float("PARTIAL_TAKE_PROFIT_PCT", "0.025")
    full_take_profit_enabled: bool = _env_bool("FULL_TAKE_PROFIT_ENABLED", "true")
    full_take_profit_pct: float = _env_float("FULL_TAKE_PROFIT_PCT", "0.06")
    break_even_stop_enabled: bool = _env_bool("BREAK_EVEN_STOP_ENABLED", "true")
    break_even_activation_pct: float = _env_float("BREAK_EVEN_ACTIVATION_PCT", "0.018")
    break_even_buffer_pct: float = _env_float("BREAK_EVEN_BUFFER_PCT", "0.001")
    trailing_take_profit_enabled: bool = _env_bool("TRAILING_TAKE_PROFIT_ENABLED", "true")
    trailing_take_profit_activation_pct: float = _env_float("TRAILING_TAKE_PROFIT_ACTIVATION_PCT", "0.025")
    trailing_take_profit_giveback_pct: float = _env_float("TRAILING_TAKE_PROFIT_GIVEBACK_PCT", "0.012")
    # Early-profit korumasi (eskiden tpsl_engine icinde hardcoded 0.025/0.02 idi; full TP'yi olu kod yapiyordu).
    # Config'e baglandi: aktivasyon partial/full TP'nin uzerine cekildi, giveback trailing ile hizalandi.
    early_profit_activation_pct: float = _env_float("EARLY_PROFIT_ACTIVATION_PCT", "0.04")
    early_profit_giveback_pct: float = _env_float("EARLY_PROFIT_GIVEBACK_PCT", "0.012")

    # ---------------- SIGNAL CONFIRMATION ----------------
    # Tek cevrimlik (tek mum) gurultu sinyallerini elemek icin kapanmis-mum/streak teyidi.
    # Giris ve indicator-exit ayri ayri N ardisik teyit ister; 0 = teyit kapali (eski davranis).
    entry_confirmation_streak: int = _env_int("ENTRY_CONFIRMATION_STREAK", "0")
    indicator_exit_confirmation_streak: int = _env_int("INDICATOR_EXIT_CONFIRMATION_STREAK", "0")
    # Kosulsuz `pnl < 0 -> FULL_CLOSE` yerine: sadece bu zarar esigi altinda tam kapat (rejim kapisi scoring'de).
    loser_full_close_min_loss_pct: float = _env_float("LOSER_FULL_CLOSE_MIN_LOSS_PCT", "0.012")
    # Karda pozisyonu indikatör gürültüsünden koru — TP/trailing halleder (veri: ifc %7 win rate).
    profit_protection_pnl_pct: float = _env_float("PROFIT_PROTECTION_PNL_PCT", "0.005")
    # Bu zarar eşiğinin üstündeyken indicator exit'i durdur (küçük dalgalanmayı filtrele).
    indicator_exit_min_loss_pct: float = _env_float("INDICATOR_EXIT_MIN_LOSS_PCT", "0.0")

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

    # ---------------- DUST MAINTENANCE ----------------
    dust_maintenance_mode: str = _env("DUST_MAINTENANCE_MODE", "auto_convert").strip().lower()
    dust_candidate_max_value_usdt: float = _env_float("DUST_CANDIDATE_MAX_VALUE_USDT", "0.05")
    dust_auto_convert_to_ccy: str = _env("DUST_AUTO_CONVERT_TO_CCY", "USDT").strip().upper()
    dust_auto_convert_max_total_usdt: float = _env_float("DUST_AUTO_CONVERT_MAX_TOTAL_USDT", "0.25")
    dust_auto_convert_max_assets_per_run: int = _env_int("DUST_AUTO_CONVERT_MAX_ASSETS_PER_RUN", "5")
    dust_auto_convert_interval_minutes: int = _env_int("DUST_AUTO_CONVERT_INTERVAL_MINUTES", "360")
    dust_easy_convert_source: str = _env("DUST_EASY_CONVERT_SOURCE", "1").strip()

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
    llm_provider_order: str = _env("LLM_PROVIDER_ORDER", "bedrock,groq")

    # ---------------- LLM API KEYS ----------------
    openai_api_key: str = _env("OPENAI_API_KEY", "")
    gemini_api_key: str = _env("GEMINI_API_KEY", "")
    aws_bearer_token_bedrock: str = _env("AWS_BEARER_TOKEN_BEDROCK", "")
    bedrock_api_key: str = _env("BEDROCK_API_KEY", "")
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
    bedrock_model: str = _env("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")
    bedrock_region: str = _env("BEDROCK_REGION", _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")))
    aws_region: str = _env("AWS_REGION", "")
    aws_default_region: str = _env("AWS_DEFAULT_REGION", "")
    claude_model: str = _env("CLAUDE_MODEL", "")
    perplexity_model: str = _env("PERPLEXITY_MODEL", "")
    groq_model: str = _env("GROQ_MODEL", "")
    groq_fallback_model: str = _env("GROQ_FALLBACK_MODEL", "")
    groq_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_MODEL", "")
    groq_fallback_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_FALLBACK_MODEL", "")
    groq_fallback_fallback_fallback_fallback_model: str = _env("GROQ_FALLBACK_FALLBACK_FALLBACK_FALLBACK_MODEL", "")
    llm_cache_ttl_seconds: int = _env_int("LLM_CACHE_TTL_SECONDS", _env("GROQ_CACHE_TTL_SECONDS", "36000"))
    groq_cache_ttl_seconds: int = llm_cache_ttl_seconds
    threshold_update_ttl_seconds: int = _env_int("THRESHOLD_UPDATE_TTL_SECONDS", "36000")
    research_context_max_chars: int = _env_int("RESEARCH_CONTEXT_MAX_CHARS", "500")
    bulk_refresh_max_context_chars_per_symbol: int = _env_int("BULK_REFRESH_MAX_CONTEXT_CHARS_PER_SYMBOL", "260")
    threshold_snapshot_max_symbols: int = _env_int("THRESHOLD_SNAPSHOT_MAX_SYMBOLS", "6")

    # ---------------- SIGNAL STREAK ----------------
    signal_hold_decay_after: int = _env_int("SIGNAL_HOLD_DECAY_AFTER", "2")
    signal_hold_decay_step: int = _env_int("SIGNAL_HOLD_DECAY_STEP", "2")
    signal_streak_cap: int = _env_int("SIGNAL_STREAK_CAP", "12")

    # ---------------- DEBUG ----------------
    log_level: str = _env("LOG_LEVEL", "INFO")

    def __post_init__(self) -> None:
        if self.strategy_profile not in STRATEGY_PROFILE_DEFAULTS:
            allowed = ", ".join(sorted(STRATEGY_PROFILE_DEFAULTS))
            raise RuntimeError(f"STRATEGY_PROFILE must be one of: {allowed}")
        if self.strategy_profile_mode not in {"manual", "dynamic"}:
            raise RuntimeError("STRATEGY_PROFILE_MODE must be one of: manual, dynamic")

        self._apply_strategy_profile()

    def _profile_env_name(self, setting_name: str) -> str:
        return f"STRATEGY_{self.strategy_profile.upper()}_{setting_name.upper()}"

    def _apply_strategy_profile(self) -> None:
        """
        Applies profile defaults, then lets profile-specific env values override them.

        TR: Genel env degerleri once dataclass alanlarina okunur. Aktif strateji
        profili daha sonra uygulanir. Profildeki her alan `.env` tarafindan
        `STRATEGY_<PROFILE>_<SETTING>` formatiyla kontrol edilebilir.
        """
        for name, value in get_strategy_profile_values(self.strategy_profile).items():
            object.__setattr__(self, name, value)


settings = Settings()
