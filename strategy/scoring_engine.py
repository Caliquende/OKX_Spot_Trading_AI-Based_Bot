from __future__ import annotations

"""
SCORING ENGINE
==============

İndikatörlerden gelen sinyalleri puanlar ve bu puanı action/stancelere çevirir.
Tek başına order atmaz. Çıktısı main.py tarafından regime ve TPSL ile birlikte yorumlanır.

BU SÜRÜMDE DÜZELTİLEN KRİTİK HATA
---------------------------------
Önceki bozuk sürümde main.py şu çağrıyı yapıyordu:
    evaluate_signal(total_score, regime_params)

Ama evaluate_signal yalnızca tek parametre kabul ediyordu.
Sonuç:
    TypeError: evaluate_signal() takes 1 positional argument but 2 were given

Bu dosya geriye dönük uyumlu şekilde düzeltildi:
- evaluate_signal(score) çalışır
- evaluate_signal(score, regime_params) da çalışır

Ayrıca regime parametreleri verilirse threshold ve fraction onlar üzerinden okunur.
Verilmezse global settings fallback kullanılır.
"""

from typing import Any

from indicators.indicator_config import WEIGHTS


STANCE_TO_SCORE = {
    "STRONG_SELL": -2,
    "SELL": -1,
    "HOLD": 0,
    "BUY": 1,
    "STRONG_BUY": 2,
}


def calculate_indicator_score(df):
    """
    Teknik skor kapanmış mum üzerinden üretilir.

    Not:
    - df.iloc[-1] yaşayan mum olabilir
    - df.iloc[-2] son kapanmış mumdur
    - df.iloc[-3] bir önceki kapanmış mumdur
    """
    last = df.iloc[-2]
    prev = df.iloc[-3]

    raw = {}

    if last["rsi"] < 30:
        raw["rsi"] = 2
    elif last["rsi"] < 40:
        raw["rsi"] = 1
    elif last["rsi"] > 70:
        raw["rsi"] = -2
    elif last["rsi"] > 60:
        raw["rsi"] = -1
    else:
        raw["rsi"] = 0

    stoch = float(last["stochrsi"]) / 100.0
    if stoch < 0.2:
        raw["stochrsi"] = 2
    elif stoch < 0.4:
        raw["stochrsi"] = 1
    elif stoch > 0.8:
        raw["stochrsi"] = -2
    elif stoch > 0.6:
        raw["stochrsi"] = -1
    else:
        raw["stochrsi"] = 0

    raw["macd_cross"] = 1 if last["macd"] > last["macd_signal"] else -1
    raw["macd_momentum"] = 1 if last["macd_hist"] > prev["macd_hist"] else -1
    raw["ema_trend"] = 2 if last["ema20"] > last["ema50"] else -2

    if last["close"] < last["bb_lower"]:
        raw["bollinger"] = 2
    elif last["close"] > last["bb_upper"]:
        raw["bollinger"] = -2
    else:
        raw["bollinger"] = 0

    raw["vwap"] = 1 if last["close"] > last["vwap"] else -1
    raw["adx"] = 1 if last["adx"] > 25 else 0
    raw["obv"] = 1 if last["obv"] > prev["obv"] else -1
    raw["atr_volatility"] = 1 if last["atr"] > prev["atr"] else 0
    raw["ema_slope"] = 1 if last["ema20"] > prev["ema20"] else -1

    avg_vol = df["volume"].rolling(20).mean().iloc[-2]
    raw["volume_spike"] = 1 if last["volume"] > avg_vol else 0
    raw["price_vs_ema50"] = 1 if last["close"] > last["ema50"] else -1

    weighted_score = 0.0
    for key, value in raw.items():
        weighted_score += float(value) * float(WEIGHTS.get(key, 1.0))

    return round(weighted_score, 2), raw


def _resolve_signal_params(regime_params: dict[str, Any] | None) -> dict[str, float]:
    """
    Signal threshold ve fraction parametrelerini çözer.

    Öncelik:
    1. regime_params içinden gelen değerler
    2. settings fallback
    """
    from config.settings import settings

    regime_params = regime_params or {}

    return {
        "buy_threshold": float(regime_params.get("buy_threshold", settings.buy_threshold)),
        "strong_buy_threshold": float(regime_params.get("strong_buy_threshold", settings.strong_buy_threshold)),
        "sell_threshold": float(regime_params.get("sell_threshold", settings.sell_threshold)),
        "strong_sell_threshold": float(regime_params.get("strong_sell_threshold", settings.strong_sell_threshold)),
        "buy_pct": float(regime_params.get("buy_pct", getattr(settings, "buy_pct", 0.04))),
        "strong_buy_pct": float(regime_params.get("strong_buy_pct", getattr(settings, "strong_buy_pct", 0.08))),
    }


def evaluate_signal(score: float, regime_params: dict[str, Any] | None = None):
    """
    Skoru aksiyona çevirir.

    Geriye dönük uyumluluk:
    - evaluate_signal(score)
    - evaluate_signal(score, regime_params)

    regime_params beklenen örnek:
    {
        "buy_threshold": 4,
        "strong_buy_threshold": 6,
        "sell_threshold": -4,
        "strong_sell_threshold": -6,
        "buy_pct": 0.03,
        "strong_buy_pct": 0.06,
        "regime": "RANGE",
    }
    """
    params = _resolve_signal_params(regime_params)

    strong_sell_threshold = params["strong_sell_threshold"]
    sell_threshold = params["sell_threshold"]
    buy_threshold = params["buy_threshold"]
    strong_buy_threshold = params["strong_buy_threshold"]
    buy_pct = params["buy_pct"]
    strong_buy_pct = params["strong_buy_pct"]
    # Mantık çok basit:
    # kötü -> PARTIAL_CLOSE
    # çok kötü -> FULL_CLOSE
    # nötr aralık -> HOLD
    if score <= strong_sell_threshold:
        return {
            "action": "FULL_CLOSE",
            "fraction": 1.0,
            "stance": "STRONG_SELL",
        }

    if strong_sell_threshold < score <= sell_threshold:
        # SELL var ama en sert seviye değilse önce riski azaltıyoruz.
        return {
            "action": "PARTIAL_CLOSE",
            "fraction": 0.5,
            "stance": "SELL",
        }

    if sell_threshold < score < buy_threshold:
        return {
            "action": "HOLD",
            "fraction": 0.0,
            "stance": "HOLD",
        }

    if buy_threshold <= score <= strong_buy_threshold:
        return {
            "action": "BUY",
            "fraction": buy_pct,
            "stance": "BUY",
        }

    if score > strong_buy_threshold:
        return {
            "action": "STRONG_BUY",
            "fraction": strong_buy_pct,
            "stance": "STRONG_BUY",
        }

    return {
        "action": "HOLD",
        "fraction": 0.0,
        "stance": "HOLD",
    }


def apply_regime_execution_policy(
    signal: dict[str, Any],
    score: float,
    regime_params: dict[str, Any] | None = None,
    regime_diag: dict[str, Any] | None = None,
    indicator_details: dict[str, Any] | None = None,
    ai_diag: dict[str, Any] | None = None,
    has_position: bool = False,
    position_pnl_pct: float | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Rejim bazlı son karar katmanı.

    evaluate_signal ham skoru aksiyona çevirir. Bu fonksiyon o aksiyonu piyasa
    rejimine göre filtreler veya risk azaltma tarafında sertleştirir.
    """
    params = _resolve_signal_params(regime_params)
    regime_params = regime_params or {}
    regime_diag = regime_diag or {}
    indicator_details = indicator_details or {}
    ai_diag = ai_diag or {}

    adjusted = dict(signal)
    action = str(adjusted.get("action") or "HOLD").upper()
    regime = str(regime_params.get("regime") or regime_diag.get("regime") or "BASE").upper()
    profile = str(regime_params.get("strategy_profile") or "balanced").lower()
    trend_bias = str(regime_diag.get("trend_bias") or "").upper()
    effective_ai_score = float(ai_diag.get("effective") or 0.0)
    weak_catalyst = bool(ai_diag.get("weak_catalyst"))
    buy_threshold = float(params["buy_threshold"])
    strong_buy_threshold = float(params["strong_buy_threshold"])
    global_market_bearish = bool(regime_diag.get("global_market_bearish", False))

    rsi_signal = float(indicator_details.get("rsi") or 0.0)
    stoch_signal = float(indicator_details.get("stochrsi") or 0.0)
    bollinger_signal = float(indicator_details.get("bollinger") or 0.0)
    ema_trend_signal = float(indicator_details.get("ema_trend") or 0.0)
    ema_slope_signal = float(indicator_details.get("ema_slope") or 0.0)
    price_vs_ema50_signal = float(indicator_details.get("price_vs_ema50") or 0.0)
    volume_spike_signal = float(indicator_details.get("volume_spike") or 0.0)

    def hold(reason: str) -> tuple[dict[str, Any], str]:
        return {"action": "HOLD", "fraction": 0.0, "stance": "HOLD"}, reason

    def promote_full_close(reason: str) -> tuple[dict[str, Any], str]:
        promoted = dict(adjusted)
        promoted.update({"action": "FULL_CLOSE", "fraction": 1.0, "stance": "STRONG_SELL"})
        return promoted, reason

    if not has_position and action in {"PARTIAL_CLOSE", "FULL_CLOSE"}:
        return hold("policy_block_exit_without_position")

    # === GLOBAL MARKET BEARISH FILTER ===
    # Sembollerin çoğu bearish'se yeni giriş yapma
    if action in {"BUY", "STRONG_BUY"} and global_market_bearish and not has_position:
        return hold("policy_block_global_market_bearish")

    # Zarar büyüdüğünde kademeli çıkış yerine tam risk azaltma.
    # Eski loglarda ana zarar indicator_partial_close zincirinden geldi.
    if has_position and action == "PARTIAL_CLOSE" and position_pnl_pct is not None:
        pnl = float(position_pnl_pct)
        if pnl <= -0.02 and regime in {"CHOP", "VOLATILE"}:
            return promote_full_close(f"policy_full_close_loser_{regime.lower()}")
        if pnl <= -0.015 and trend_bias == "DOWN":
            return promote_full_close("policy_full_close_loser_down_bias")
        if pnl < 0:
            return promote_full_close("policy_full_close_loser")

    if has_position and action == "PARTIAL_CLOSE" and regime == "VOLATILE" and trend_bias == "DOWN":
        return promote_full_close("policy_full_close_volatile_down_bias")

    if action not in {"BUY", "STRONG_BUY"}:
        return adjusted, "policy_ok"

    # TREND: yukarı trendde momentum serbest, aşağı trendde sadece güçlü ters dönüş.
    if regime == "TREND":
        if trend_bias == "DOWN":
            if action != "STRONG_BUY" or score < strong_buy_threshold or effective_ai_score <= 0:
                return hold("policy_block_trend_down_without_strong_reversal")
        return adjusted, "policy_ok_trend"

    # RANGE: mean-reversion trade. Dip/alt bant kanıtı yoksa kovalamayı engelle.
    # DOWN trend'de range mean-reversion çalışmaz — fiyat düşmeye devam eder.
    if regime == "RANGE":
        mean_reversion_evidence = rsi_signal > 0 or stoch_signal > 0 or bollinger_signal > 0
        if not mean_reversion_evidence:
            return hold("policy_block_range_without_mean_reversion")
        # DOWN trending range'de asla alım yapma (çift güvence)
        if trend_bias == "DOWN" and action in {"BUY", "STRONG_BUY"}:
            return hold("policy_block_range_down_trend_no_buy")
        if weak_catalyst and effective_ai_score < 0 and score < strong_buy_threshold:
            return hold("policy_block_range_weak_negative_ai")
        return adjusted, "policy_ok_range"

    # CHOP: fırsat kapısı açık ama yalnızca yüksek conviction.
    if regime == "CHOP":
        if profile == "aggressive":
            if score < buy_threshold:
                return hold("policy_block_chop_aggressive_below_buy_threshold")
            if effective_ai_score < 2.0:
                return hold("policy_block_chop_aggressive_needs_ai_conviction")
            if ema_slope_signal <= 0:
                return hold("policy_block_chop_aggressive_needs_positive_slope")
            if trend_bias == "DOWN" and effective_ai_score < 0:
                return hold("policy_block_chop_down_bias_negative_ai")
            adjusted["fraction"] = min(float(adjusted.get("fraction") or 0.0), float(params["buy_pct"]))
            return adjusted, "policy_ok_chop_aggressive_probe"

        if score < strong_buy_threshold:
            return hold("policy_block_chop_needs_strong_score")
        if trend_bias == "DOWN" and effective_ai_score < 0:
            return hold("policy_block_chop_down_bias_negative_ai")
        adjusted["fraction"] = min(float(adjusted.get("fraction") or 0.0), float(params["buy_pct"]))
        return adjusted, "policy_ok_chop_reduced_size"

    # VOLATILE: breakout/continuation şartı. Düşen volatilitede dip yakalama kapalı.
    if regime == "VOLATILE":
        trend_confirmation = (
            trend_bias == "UP"
            and ema_trend_signal > 0
            and (ema_slope_signal > 0 or price_vs_ema50_signal > 0)
        )
        if profile == "aggressive":
            trend_confirmation = trend_confirmation and volume_spike_signal > 0
        strong_reversal = action == "STRONG_BUY" and score >= strong_buy_threshold and effective_ai_score > 0
        if not trend_confirmation and not strong_reversal:
            return hold("policy_block_volatile_without_breakout_confirmation")
        # DOWN trend'de volatile piyasada asla alım yapma — en tehlikeli kombinasyon
        if trend_bias == "DOWN" and action in {"BUY", "STRONG_BUY"}:
            return hold("policy_block_volatile_down_trend")
        adjusted["fraction"] = min(float(adjusted.get("fraction") or 0.0), float(params["buy_pct"]))
        return adjusted, "policy_ok_volatile_reduced_size"

    # BASE: ham sinyali koru, ama minimum buy threshold altında zaten evaluate_signal BUY üretmez.
    if score < buy_threshold:
        return hold("policy_block_base_below_buy_threshold")
    return adjusted, "policy_ok_base"
