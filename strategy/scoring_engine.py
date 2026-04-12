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
