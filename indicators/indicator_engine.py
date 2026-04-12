from __future__ import annotations

"""
INDICATOR ENGINE
================

OHLCV dataframe'ini alır ve stratejinin ihtiyaç duyduğu teknik indikatör kolonlarını üretir.
Burada hesaplanan veriler scoring/regime katmanına ham input olur.
"""


import warnings
import pandas as pd
import pandas_ta_classic as ta

warnings.filterwarnings("ignore")


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Burada indikatör hesapları korunuyor.
    Tek kritik fark: karar verirken son oluşan mum yerine son kapanmış mum kullanılacak.
    """
    # copy() kullanıyoruz ki dışarıdan gelen dataframe'i sessizce bozmayalım.
    df = df.copy()

    # RSI = fiyatın aşırı alım / aşırı satım bölgesine yakın olup olmadığını anlamaya yardım eder.
    df["rsi"] = ta.rsi(df["close"], length=14)

    # StochRSI = RSI'ın daha hassas versiyonu gibi düşünülebilir.
    stoch = ta.stochrsi(df["close"])
    df["stochrsi"] = stoch.iloc[:, 0]

    # MACD = momentum ve trend yönü hakkında fikir verir.
    macd = ta.macd(df["close"])
    df["macd"] = macd.iloc[:, 0]
    df["macd_signal"] = macd.iloc[:, 1]
    df["macd_hist"] = macd.iloc[:, 2]

    # Bollinger bantları = fiyat normal alanının dışına mı taşıyor diye bakmak için kullanılır.
    bb = ta.bbands(df["close"], length=20, std=2)
    df["bb_lower"] = bb.iloc[:, 0]
    df["bb_middle"] = bb.iloc[:, 1]
    df["bb_upper"] = bb.iloc[:, 2]

    # OBV = fiyat hareketi hacimle destekleniyor mu diye bakmaya yarar.
    df["obv"] = ta.obv(df["close"], df["volume"])

    # ADX = trend güçlü mü zayıf mı sorusuna yardım eder.
    adx = ta.adx(df["high"], df["low"], df["close"])
    df["adx"] = adx.iloc[:, 0]
    df["dmp"] = adx.iloc[:, 1]
    df["dmn"] = adx.iloc[:, 2]

    # EMA'lar ve VWAP da yön, ortalama maliyet bölgesi ve trend filtresi için kullanılır.
    df["atr"] = ta.atr(df["high"], df["low"], df["close"])
    df["ema20"] = ta.ema(df["close"], length=20)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

    # dropna ile eksik hesap satırlarını atıyoruz.
    # Çünkü indikatörlerin ilk satırlarında doğal olarak boş değer olur.
    return df.dropna().copy()
