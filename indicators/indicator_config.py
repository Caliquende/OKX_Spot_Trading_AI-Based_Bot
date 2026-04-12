"""
INDICATOR PARAMETRELERİ
=======================

Teknik indikatör hesaplarında kullanılan periyotları ve küçük sabitleri merkezi tutar.
"""

# İstersen ağırlıkları burada değiştirirsin.
# Yoksa 1.0 default gider.
WEIGHTS = {
    # Bu sözlük scoring engine'e "hangi sinyal ne kadar etkili olsun?" cevabını verir.
    # Sayı büyürse o indikatör toplam skoru daha çok etkiler.

    # momentum
    "rsi": 0.7,
    "stochrsi": 0.6,
    "macd_cross": 1.0,
    "macd_momentum": 0.8,

    # trend core
    "ema_trend": 1.5,
    "ema_slope": 1.2,
    "price_vs_ema50": 1.0,

    # regime filter
    "adx": 1.3,

    # volatility context
    "atr_volatility": 0.5,
    "bollinger": 0.6,

    # confirmation
    "vwap": 0.7,
    "obv": 0.5,
    "volume_spike": 0.8,
}
