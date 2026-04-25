# OKX Spot Trading Bot

[English README](./README.md)

OKX spot piyasası için hazırlanmış; teknik sinyaller, market rejimi, deterministik execution kontrolleri, risk kontrolleri, TP/SL mantığı, Telegram takibi ve isteğe bağlı AI araştırma katmanını birleştiren modüler bir trading bot.

Yasal Uyarı: Gerçek piyasalarda kullanılan bu tür sistemler finansal risk içerir.

## Güncel Durum

Bu repo, şu odaklara sahip gelişmiş bir alpha sürümdür:
- **Kapsamlı white-box testleri ile doğrulanmış mantık akışı (ana modüllerde %95+ branch coverage).**
- **Yüksek denetlenebilirlik için sadeleştirilmiş ve yenilenmiş muhasebe (`reconciler.py`) katmanı.**
- Öğrenme, test, iteratif geliştirme ve strateji optimizasyonu.
- Kusursuz bir production-grade sistem olarak sunulmaz; strateji geliştirme odaklıdır.

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

## Yapısal İyileştirmeler

Mevcut sürümde şu yapısal ve mantıksal iyileştirmeler yapılmıştır:

- **Reconciler Yenilemesi:** Muhasebe mantığı, 843 satırdan 167 satıra düşürülerek daha güvenilir ve bakımı kolay hale getirildi.
- **Yüksek Test Kapsamı:** `ExecutionEngine`, `TPSLEngine` ve `RegimeEngine` modüllerinde %95-100 arası branch coverage başarısına ulaşıldı.
- **Veri Bütünlüğü:** Repository katmanı, bellek içi (in-memory) SQLite test paketleri ile SQL bazında doğrulandı.
- **Pozisyon Tutarlılığı:** Açık pozisyon miktarı için borsa bakiyesi (exchange balance) esas alınır.
- **AI Esnekliği:** AI model fallback zinciri ve refresh TTL değerleri `.env` üzerinden yönetilebilir.
- **TP/SL Gelişmiş Koruma:** Canlı mumun `high` değerini dikkate alan kar al, başabaş stop ve trailing koruması eklendi.

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
Bot research tarafında AI desteklidir; ancak execution güvenliği deterministik kalır. Order gönderimi, kilitler ve muhasebe katmanı sıkı ve denetlenebilir bir yapıdadır.

## Ana Bileşenler

### Exchange katmanı
`core/exchange.py`, OKX ile ccxt üzerinden konuşur.

### Execution katmanı
`core/execution_engine.py`, kararları güvenli biçimde uygular. %100 branch coverage ile tescillidir.

### Reconcile katmanı
`core/reconciler.py`, projenin en hassas parçasıdır. Botun muhasebesini borsa durumuyla eşitlemek için basitleştirilmiştir. %90 branch coverage ile doğrulanmıştır.

### Strategy katmanı
- `strategy/scoring_engine.py`: skor -> aksiyon haritalama
- `strategy/regime_engine.py`: market rejimi tespiti (%100 coverage)

### TP/SL katmanı
`core/tpsl_engine.py`, Stop Loss, Kısmi/Tam Kar Al, Başabaş Stop ve Trailing koruması ile kârı korur.

## Test Süreci

Proje, white-box testleri ile mantıksal doğrulamaya büyük önem verir. Kapsamlı test paketleri `scratch/` dizininde yer almaktadır.

### Tüm testleri kapsama raporuyla çalıştırın
```bash
$env:PYTHONPATH="."
pytest scratch/ --cov=core --cov=db.repositories --cov=strategy --cov-branch --cov-report=term-missing
```

### Kapsama Durumu
- `strategy/regime_engine.py`: **%100 Branch Coverage**
- `core/execution_engine.py`: **%100 Branch Coverage**
- `core/tpsl_engine.py`: **%96 Branch Coverage**
- `db/repositories.py`: **%90 Branch Coverage** (Bellek içi SQLite tescilli)
- `core/reconciler.py`: **%90 Branch Coverage**

## Ortam Ayarları (.env)

### Minimum Gerekli Alanlar
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

### AI / Araştırma Örneği
```env
LLM_ENABLED=true
GROQ_API_KEY=your_key
GROQ_MODEL=groq/compound
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_FALLBACK_MODEL=llama-3.3-70b-versatile
GROQ_CACHE_TTL_SECONDS=7200
EXA_API_KEY=your_exa_key
COINGECKO_DEMO_API_KEY=your_key
```

### TP/SL Örneği
```env
TPSL_ENABLED=true
STOP_LOSS_PCT=0.06
PARTIAL_TAKE_PROFIT_ENABLED=true
PARTIAL_TAKE_PROFIT_PCT=0.04
FULL_TAKE_PROFIT_ENABLED=true
FULL_TAKE_PROFIT_PCT=0.08
BREAK_EVEN_STOP_ENABLED=true
BREAK_EVEN_ACTIVATION_PCT=0.03
TRAILING_TAKE_PROFIT_ENABLED=true
TRAILING_TAKE_PROFIT_ACTIVATION_PCT=0.05
TRAILING_TAKE_PROFIT_GIVEBACK_PCT=0.02
```

### Günlük Risk Guard Örneği
```env
MAX_DAILY_REALIZED_LOSS_USDT=100
MAX_DAILY_DRAWDOWN_PCT=0.08
```

## Önemli Ayar Alanları

### Risk ve Sizing
- `MIN_ORDER_QUOTE_USDT`, `MIN_FREE_USDT`, `MAX_OPEN_POSITIONS`, `MAX_SYMBOL_EXPOSURE_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_DAILY_REALIZED_LOSS_USDT`.

### Threshold'lar
- `BUY_THRESHOLD`, `STRONG_BUY_THRESHOLD`, `SELL_THRESHOLD`, `STRONG_SELL_THRESHOLD`.

### Scale-in
- `SCALE_IN_ENABLED`, `SCALE_IN_TRIGGER_STREAK`, `MAX_SCALE_IN_COUNT`.

## İzlenecek Loglar
Önemli loglar: `[RECON]`, `[POSITION]`, `[POSITION MISMATCH]`, `[BUY SENT]`, `[TPSL TRIGGER]`, `[GROQ REFRESH]`, `[GHOST ORDER CLEANED]`.

## Güvenlik Uyarısı

Bu proje borsa üzerinde emir verebilir. Gerçek hesaba geçmeden önce:
- sandbox üzerinde test edin,
- `DRY_RUN=true` ile sistemi izleyin,
- canlı sermayeye güvenmeden önce muhasebe (reconcile) davranışını doğrulayın.
