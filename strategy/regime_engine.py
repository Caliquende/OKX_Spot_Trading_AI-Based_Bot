from __future__ import annotations

"""
REGIME ENGINE
=============

Bu dosya piyasanın hangi karakterde olduğunu anlamaya çalışır.

Basitçe:
- TREND: net yön var
- RANGE: yatay gidiyor
- CHOP: kararsız ve dağınık
- VOLATILE: fazla sert oynuyor
"""


def _safe_ratio(a: float, b: float) -> float:
    # Sıfıra bölme patlamasın diye güvenli oran helper'ı.
    if abs(b) <= 1e-12:
        return 0.0
    return a / b


# Hysteresis buffer: bir regime'den çıkmak için eşiğin ne kadar ötesine geçmek gerekir.
# Örnek: TREND için ADX_MIN=26. Hysteresis=0.15 ise regime'den çıkmak için ADX < 26*(1-0.15)=22.1 gerekir.
# Bu, 25.9 → 26.1 → 25.9 titremesini önler.
_HYSTERESIS = 0.15


def _trend_ok(adx, trend_persistence, ema_dist_ratio, settings) -> bool:
    adx_min = float(settings.regime_trend_adx_min)
    return (
        adx >= adx_min
        and trend_persistence >= float(settings.regime_trend_persistence_min)
        and ema_dist_ratio > float(settings.regime_chop_ema_dist_max)
    )


def _trend_exit(adx, trend_persistence, ema_dist_ratio, settings) -> bool:
    adx_min = float(settings.regime_trend_adx_min)
    adx_exit = adx_min * (1.0 - _HYSTERESIS)
    return (
        adx < adx_exit
        or trend_persistence < float(settings.regime_trend_persistence_min) * (1.0 - _HYSTERESIS)
    )


def _range_ok(adx, ema_dist_ratio, atr_ratio, settings) -> bool:
    return (
        adx <= float(settings.regime_range_adx_max)
        and ema_dist_ratio <= float(settings.regime_chop_ema_dist_max)
        and atr_ratio < float(settings.regime_volatile_atr_ratio_min)
    )


def _range_exit(adx, ema_dist_ratio, atr_ratio, settings) -> bool:
    adx_max = float(settings.regime_range_adx_max)
    adx_exit = adx_max * (1.0 + _HYSTERESIS)
    return adx > adx_exit or atr_ratio >= float(settings.regime_volatile_atr_ratio_min)


def _chop_ok(adx, ema_dist_ratio, settings) -> bool:
    return (
        adx <= float(settings.regime_chop_adx_max)
        and ema_dist_ratio <= float(settings.regime_chop_ema_dist_max)
    )


def _chop_exit(adx, ema_dist_ratio, settings) -> bool:
    adx_max = float(settings.regime_chop_adx_max)
    adx_exit = adx_max * (1.0 + _HYSTERESIS)
    return adx > adx_exit


def _volatile_ok(atr_ratio, settings) -> bool:
    return atr_ratio >= float(settings.regime_volatile_atr_ratio_min)


def _volatile_exit(atr_ratio, settings) -> bool:
    atr_min = float(settings.regime_volatile_atr_ratio_min)
    atr_exit = atr_min * (1.0 - _HYSTERESIS)
    return atr_ratio < atr_exit


def detect_market_regime(df, settings, current_regime: str = "BASE") -> tuple[str, dict]:
    """
    Piyasa rejimini tespit eder.

    current_regime parametresi hysteresis için gerekli.
    Mevcut regime'den çıkmak için eşiğin belirli bir margin ötesine geçmek gerekir.
    Bu sayede ADX=25.9 → 26.1 → 25.9 titremi cycle bazlı threshold değişimi üretmez.

    current_regime main loop'tan geçirilmelidir.
    Geçirilmezse (default "BASE") hysteresis çalışmaz ama fonksiyon yine de doğru sonuç üretir.
    """
    last = df.iloc[-2]
    slope_lookback = max(int(getattr(settings, "regime_trend_slope_lookback", 6)), 2)
    start_idx = max(len(df) - 2 - slope_lookback, 0)
    window = df.iloc[start_idx:-1].copy()

    adx = float(last.get("adx") or 0.0)
    atr = float(last.get("atr") or 0.0)
    close = float(last.get("close") or 0.0)
    ema20 = float(last.get("ema20") or 0.0)
    ema50 = float(last.get("ema50") or 0.0)
    dmp = float(last.get("dmp") or 0.0)
    dmn = float(last.get("dmn") or 0.0)

    atr_ratio = _safe_ratio(atr, close)
    ema_dist_ratio = abs(_safe_ratio(ema20 - ema50, close))
    ema20_slope_ratio = 0.0
    trend_persistence = 0.0

    if len(window) >= 2:
        first_ema20 = float(window["ema20"].iloc[0] or 0.0)
        last_ema20 = float(window["ema20"].iloc[-1] or 0.0)
        ema20_slope_ratio = _safe_ratio(last_ema20 - first_ema20, close)

        ema_diff = (window["ema20"] - window["ema50"]).astype(float)
        if len(ema_diff) > 0:
            sign = 1 if ema20 >= ema50 else -1
            aligned = (ema_diff * sign) > 0
            trend_persistence = float(aligned.mean())

    trend_bias = "UP" if dmp >= dmn else "DOWN"

    # --- Hysteresis logic ---
    # Mevcut regime'deyken çıkış için daha yüksek eşik gerekir.
    # Yeni bir regime'e girmek için normal giriş eşiği yeterlidir.

    regime = current_regime if current_regime in {"TREND", "RANGE", "CHOP", "VOLATILE"} else None

    # Mevcut VOLATILE ise: low-ADX chop/range karakteri baskınsa yeniden değerlendir.
    # Sadece ATR eşiğini geçti diye VOLATILE'da yapışmak, loglarda sıradan 4h chop'u
    # "volatile trend" gibi yönetmeye neden oluyordu.
    if regime == "VOLATILE":
        if _chop_ok(adx, ema_dist_ratio, settings):
            regime = None
        elif not _volatile_exit(atr_ratio, settings):
            pass  # kalıyoruz, aşağıda tekrar atanacak
        else:
            regime = None  # yeniden değerlendir

    # Mevcut TREND ise
    if regime == "TREND":
        if not _trend_exit(adx, trend_persistence, ema_dist_ratio, settings):
            pass
        else:
            regime = None

    # Mevcut RANGE ise
    if regime == "RANGE":
        if not _range_exit(adx, ema_dist_ratio, atr_ratio, settings):
            pass
        else:
            regime = None

    # Mevcut CHOP ise
    if regime == "CHOP":
        if not _chop_exit(adx, ema_dist_ratio, settings):
            pass
        else:
            regime = None

    # Regime çözülemediyse veya başlangıçta yoksa fresh detection
    if regime is None:
        if _trend_ok(adx, trend_persistence, ema_dist_ratio, settings):
            regime = "TREND"
        elif _range_ok(adx, ema_dist_ratio, atr_ratio, settings):
            regime = "RANGE"
        elif _chop_ok(adx, ema_dist_ratio, settings):
            regime = "CHOP"
        elif _volatile_ok(atr_ratio, settings):
            regime = "VOLATILE"
        else:
            regime = "RANGE"

    diagnostics = {
        "regime": regime,
        "trend_bias": trend_bias,
        "adx": round(adx, 4),
        "atr_ratio": round(atr_ratio, 6),
        "ema_dist_ratio": round(ema_dist_ratio, 6),
        "ema20_slope_ratio": round(ema20_slope_ratio, 6),
        "trend_persistence": round(trend_persistence, 4),
        "dmp": round(dmp, 4),
        "dmn": round(dmn, 4),
    }
    return regime, diagnostics


def resolve_regime_params(settings, regime: str) -> dict:
    # Tespit edilen regime'e göre hangi threshold ve yüzde seti kullanılacak, burada seçilir.
    regime = str(regime or "").upper()
    suffix = regime.lower()

    def get(name: str, fallback_name: str):
        return float(getattr(settings, name, getattr(settings, fallback_name)))

    if regime in {"TREND", "RANGE", "CHOP", "VOLATILE"}:
        return {
            "buy_threshold": get(f"regime_buy_threshold_{suffix}", "buy_threshold"),
            "strong_buy_threshold": get(f"regime_strong_buy_threshold_{suffix}", "strong_buy_threshold"),
            "sell_threshold": get(f"regime_sell_threshold_{suffix}", "sell_threshold"),
            "strong_sell_threshold": get(f"regime_strong_sell_threshold_{suffix}", "strong_sell_threshold"),
            "buy_pct": get(f"regime_buy_pct_{suffix}", "buy_pct"),
            "strong_buy_pct": get(f"regime_strong_buy_pct_{suffix}", "strong_buy_pct"),
            "regime": regime,
        }

    return {
        "buy_threshold": float(settings.buy_threshold),
        "strong_buy_threshold": float(settings.strong_buy_threshold),
        "sell_threshold": float(settings.sell_threshold),
        "strong_sell_threshold": float(settings.strong_sell_threshold),
        "buy_pct": float(settings.buy_pct),
        "strong_buy_pct": float(settings.strong_buy_pct),
        "regime": "BASE",
    }
