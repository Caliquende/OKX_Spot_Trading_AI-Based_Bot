# OKX Spot Trading Bot

[English README](./README.md)

OKX spot piyasası için hazırlanmış; teknik sinyaller, market rejimi, deterministik execution kontrolleri, risk kontrolleri, TP/SL mantığı, Telegram takibi ve isteğe bağlı AI araştırma katmanını birleştiren modüler bir trading bot.

Yasal Uyarı: Gerçek piyasalarda kullanılan bu tür sistemler finansal risk içerir.

## Güncel Durum

Bu repo çalışan bir alpha sürümdür.

**Mantıksal Doğrulama Durumu:**
- `strategy/regime_engine.py`: **%100 Branch Coverage**
- `core/execution_engine.py`: **%100 Branch Coverage**
- `core/tpsl_engine.py`: **%96 Branch Coverage**
- `db/repositories.py`: **%90 Branch Coverage**
- `core/reconciler.py`: **%90 Branch Coverage** (Yenilenmiş mimari)

Ana kullanım alanı:
- öğrenme,
- test,
- iteratif geliştirme,
- strateji ayarı.

Kusursuz production-grade sistem olarak sunulmaz.

## Bot Ne Yapar?

Her cycle'da kabaca şunları yapar:

1. Exchange ve veritabanı sağlığını kontrol eder.
2. Her sembol için OHLCV verisi çeker.
3. Teknik indikatörleri hesaplar.
4. TTL dolduysa AI research context'i yeniler.
5. Market rejimini TREND, RANGE, CHOP veya VOLATILE olarak sınıflandırır.
6. Teknik skor ve AI skoru üretir.
7. Bu iki tarafın ortalamasıyla birleşik skor üretir.
8. TP/SL, risk, lock ve execution kapılarından geçirir.
9. Order, fill ve position state'ini SQLite içinde tekrar reconcile eder.

## Son Büyük Değişiklikler

Mevcut sürümde şu stratejik ve altyapısal iyileştirmeler vardır:

- **Reconciler Yenilemesi:** Muhasebe mantığı (`reconciler.py`), 843 satırdan 167 satıra düşürülerek sadeleştirildi.
- **Test Paketi:** `scratch/` dizini altında kapsamlı white-box testleri eklendi.
- Spot pozisyon miktarında ana source of truth artık exchange balance tarafıdır.
- Fill geçmişi daha çok ortalama giriş ve realized PnL için kullanılır.
- Ghost order cleanup tarafı daha kontrollü ve daha az spam üretir.
- AI model fallback zinciri `.env` üzerinden yönetilir.
- AI refresh TTL `.env` üzerinden ayarlanır.
- AI skor bandı artık `-24 .. 24` aralığındadır.
- Varsayılan AI stance fallback skorları artık:
  - `SELL = -8`
  - `STRONG_SELL = -16`
  - `BUY = 8`
  - `STRONG_BUY = 16`
- Birleşik skor artık:
  - `(technical_score + ai_score) / 2`
- TP/SL motoru artık:
  - canlı mumun `high` değerini dikkate alır,
  - break-even stop kullanabilir,
  - trailing rollback koruması uygular.

## Mimari

### Ana akış

- `main.py` ana runtime döngüsünü yönetir.
- `core/` exchange, execution, reconcile, risk, TP/SL, portfolio ve health mantığını içerir.
- `strategy/` sinyal mapping ve rejim mantığını taşır.
- `indicators/` teknik indikatörleri üretir.
- `analysis/` CoinGecko, Exa ve Groq tabanlı AI araştırmasını yönetir.
- `db/` kalıcı state ve muhasebe verisini tutar.
- `reporting/` Telegram mesajlaşmasını yönetir.

### Temel tasarım ilkesi

Bot research ve skor şekillendirme tarafında AI desteklidir; execution güvenliği tarafında ise deterministik kalır.

Yani:
- AI sentiment ve threshold davranışını etkileyebilir,
- ama order gönderimi, lock, reconcile ve muhasebe katmanı sıkı ve denetlenebilir kalmalıdır.

## Ana Bileşenler

### Exchange katmanı

`core/exchange.py`, OKX ile ccxt üzerinden konuşur.

### Execution katmanı

`core/execution_engine.py`, daha önce verilmiş kararları güvenli biçimde uygular. %100 branch coverage ile doğrulanmıştır.

### Reconcile katmanı

`core/reconciler.py`, projenin en hassas parçalarından biridir. Sadeleştirilmiş mimari ile yeniden inşa edildi.

Ana görevleri:
- order state senkronu,
- fill kayıtlarını yazma,
- güncel pozisyonları yeniden üretme,
- bot muhasebesini exchange state'ine yakın tutma.

### Strategy katmanı

- `strategy/scoring_engine.py`: skor -> action mapping
- `strategy/regime_engine.py`: market rejimi tespiti

### TP/SL katmanı

`core/tpsl_engine.py` artık kârı daha iyi korumak için şu araçları kullanabilir:
- stop loss,
- partial take profit,
- full take profit,
- break-even stop,
- trailing rollback koruması.

### Araştırma katmanı

`analysis/rumor_analyzer.py` şu kaynakları kullanabilir:
- CoinGecko news,
- Exa search,
- Groq AI ve çok adımlı fallback zinciri.

## Veritabanı

SQLite kullanılır. Ana tablo rolleri:
- `orders`: gönderilmiş order kayıtları
- `fills`: gerçekleşmiş trade satırları
- `positions`: güncel özet pozisyon state'i
- `symbol_locks`: cooldown ve lock state'i
- `bot_state`: streak, pending exit reason, regime state ve TP/SL state
- `cycle_reports`: cycle özetleri

## Ortam Ayarları

Proje `.env` ve `_.env` okuyabilir.

### Minimum gerekli alanlar

```env
OKX_API_KEY=your_key
OKX_SECRET=your_secret
OKX_PASSPHRASE=your_passphrase

OKX_SANDBOX=true
DRY_RUN=true

SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
TIMEFRAME=15m
LOOP_SECONDS=60
DB_PATH=trading_bot.db
```

### AI / research örneği

```env
LLM_ENABLED=true
GROQ_API_KEY=your_groq_key

GROQ_MODEL=groq/compound
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_FALLBACK_MODEL=llama-3.3-70b-versatile
GROQ_FALLBACK_FALLBACK_FALLBACK_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
GROQ_FALLBACK_FALLBACK_FALLBACK_FALLBACK_MODEL=llama-3.1-8b-instant

GROQ_CACHE_TTL_SECONDS=7200
THRESHOLD_UPDATE_TTL_SECONDS=7200

EXA_API_KEY=your_exa_key
COINGECKO_DEMO_API_KEY=your_demo_key
```

### TP/SL örneği

```env
TPSL_ENABLED=true
STOP_LOSS_PCT=0.06
PARTIAL_TAKE_PROFIT_ENABLED=true
PARTIAL_TAKE_PROFIT_PCT=0.04
FULL_TAKE_PROFIT_ENABLED=true
FULL_TAKE_PROFIT_PCT=0.08

BREAK_EVEN_STOP_ENABLED=true
BREAK_EVEN_ACTIVATION_PCT=0.03
BREAK_EVEN_BUFFER_PCT=0.002

TRAILING_TAKE_PROFIT_ENABLED=true
TRAILING_TAKE_PROFIT_ACTIVATION_PCT=0.05
TRAILING_TAKE_PROFIT_GIVEBACK_PCT=0.02
```

### Günlük risk guard örneği

```env
MAX_DAILY_REALIZED_LOSS_USDT=100
MAX_DAILY_DRAWDOWN_PCT=0.08
```

## Çalıştırma

```bash
python main.py
```

### Testleri Çalıştır
```bash
$env:PYTHONPATH="."
pytest scratch/ --cov=core --cov=db.repositories --cov=strategy --cov-branch --cov-report=term-missing
```

## Güvenli İlk Başlangıç

İlk test için:

```env
OKX_SANDBOX=true
DRY_RUN=true
LLM_ENABLED=false
```

## Önemli Ayar Alanları

### Risk ve sizing

- `MIN_ORDER_QUOTE_USDT`, `MIN_FREE_USDT`, `MAX_OPEN_POSITIONS`, `MAX_SYMBOL_EXPOSURE_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_SINGLE_TRADE_PCT`, `MAX_DAILY_REALIZED_LOSS_USDT`, `MAX_DAILY_DRAWDOWN_PCT`.

### Threshold'lar

- `BUY_THRESHOLD`, `STRONG_BUY_THRESHOLD`, `SELL_THRESHOLD`, `STRONG_SELL_THRESHOLD`.

### Scale-in

- `SCALE_IN_ENABLED`, `SCALE_IN_TRIGGER_STREAK`, `STRONG_SCALE_IN_TRIGGER_STREAK`, `MAX_SCALE_IN_COUNT`.

### TP/SL

- `STOP_LOSS_PCT`, `PARTIAL_TAKE_PROFIT_PCT`, `FULL_TAKE_PROFIT_PCT`, `BREAK_EVEN_ACTIVATION_PCT`, `TRAILING_TAKE_PROFIT_ACTIVATION_PCT`, `TRAILING_TAKE_PROFIT_GIVEBACK_PCT`.

### AI

- `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `GROQ_CACHE_TTL_SECONDS`, `THRESHOLD_UPDATE_TTL_SECONDS`.

## İzlenecek Loglar

Önemli loglar:
- `[RECON]`, `[POSITION]`, `[POSITION MISMATCH]`, `[BUY SENT]`, `[TPSL TRIGGER]`, `[GROQ REFRESH]`, `[GHOST ORDER CLEANED]`.

## Güvenlik Uyarısı

Bu proje order gönderebilir.

Gerçek hesaba geçmeden önce:
- sandbox test et,
- `DRY_RUN=true` ile birden fazla cycle izle,
- muhafazakâr exposure ile başla,
- `bot.log` dosyasını oku,
- canlı sermayeye güvenmeden önce reconcile davranışını doğrula.
