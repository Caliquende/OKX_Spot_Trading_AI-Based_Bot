# OKX Spot Trading Bot

[English README](./README_EN_updated.md)

OKX spot piyasasında çalışmak üzere tasarlanmış; teknik sinyaller, market rejimi, risk kontrolleri, TP/SL mantığı, Telegram takibi ve isteğe bağlı AI araştırma katmanını birleştiren modüler bir trading bot.

## Durum

Bu repo **çalışan bir alpha sürümüdür**.
Öğrenme, test ve iteratif geliştirme için uygundur. **Kusursuz production-grade trading sistemi** gibi sunulmaz.

## Bot Ne Yapar?

Her cycle'da kabaca şunları yapar:

1. Exchange ve veritabanı sağlığını kontrol eder.
2. Her sembol için OHLCV verisi çeker.
3. Teknik indikatörleri hesaplar.
4. Gerekirse araştırma ve sentiment katmanını tazeler.
5. Market rejimini tespit eder.
6. Birleşik skoru üretir.
7. TP/SL, risk ve execution kapılarından geçirir.
8. Uygunsa order gönderir.
9. Order, fill ve position state'ini reconcile ile tekrar senkronlar.

## Mimari Genel Bakış

### Ana akış

- `main.py` ana döngüyü yönetir.
- `core/` exchange, execution, reconcile, risk, TP/SL, health ve portfolio motorlarını içerir.
- `strategy/` sinyal mapping ve market regime mantığını taşır.
- `indicators/` teknik indikatörleri üretir.
- `analysis/` CoinGecko, Exa ve LLM tabanlı araştırma katmanını içerir.
- `db/` kalıcı state ve muhasebe verisini tutar.
- `reporting/` Telegram bildirimlerini yönetir.

### Temel tasarım fikri

Bot **araştırma ve sinyal şekillendirme tarafında AI-based**, fakat **execution güvenliği tarafında deterministik** olacak şekilde tasarlanmıştır.

Pratikte bunun anlamı:
- AI katmanı sentiment ve threshold davranışını etkileyebilir,
- ama execution, locking, reconcile ve muhasebe akışı net ve denetlenebilir kalmalıdır.

## Ana Bileşenler

### Exchange katmanı

`core/exchange.py`, OKX ile ccxt üzerinden konuşur. Sistemin geri kalanı doğrudan ham OKX response'larına bağımlı kalmaz.

### Execution katmanı

`core/execution_engine.py` kararları uygular. Ne alınacağına karar vermez; kararı güvenli şekilde nasıl göndereceğini yönetir.

### Reconcile katmanı

`core/reconciler.py` botun en hassas parçalarından biridir.
Ana görevleri:
- order state'lerini senkronlamak,
- gerçekleşen trade'leri `fills` tablosuna yazmak,
- güncel pozisyon state'ini yeniden üretmek.

Spot için pratik kural:
- açık pozisyon miktarında **exchange balance** ana referanstır,
- **fill geçmişi** ise daha çok ortalama giriş ve gerçekleşmiş PnL için kullanılır.

### Strategy katmanı

- `strategy/scoring_engine.py`: teknik skor -> aksiyon mapping
- `strategy/regime_engine.py`: marketin TREND mi RANGE mi olduğunu belirler

### Risk katmanı

`core/risk_engine.py` şu limitleri kontrol eder:
- toplam exposure,
- sembol başına exposure,
- tek işlem büyüklüğü,
- scale-in kısıtları.

### TP/SL katmanı

`core/tpsl_engine.py` `PARTIAL_CLOSE` veya `FULL_CLOSE` gibi stop-loss / take-profit kararları üretir. Kendi başına order göndermez.

### Araştırma / LLM katmanı

`analysis/rumor_analyzer.py` şu kaynakları kullanabilir:
- CoinGecko news,
- Exa web search,
- Groq ve fallback model zinciri.

Bu katman teknik skorun üstüne research/sentiment girdisi ekler.

## Veritabanı Modeli

Projede SQLite kullanılır.

Ana tablo rolleri:
- `orders`: gönderilmiş order kayıtları
- `fills`: gerçekleşmiş trade satırları
- `positions`: güncel özet pozisyon state'i
- `symbol_locks`: cooldown ve lock bilgisi
- `bot_state`: streak, regime ve pending exit metadata gibi küçük kalıcı state'ler
- `cycle_reports`: cycle özetleri

## Kurulum

### 1. Python ortamını hazırla

Mümkünse sanal environment kullan.

Başlangıç için temel bağımlılıklar:

```bash
pip install ccxt pandas pandas-ta-classic requests python-dotenv
```

### 2. `.env` dosyasını hazırla

Proje `.env` ve `_.env` dosyalarını okuyabilir.

Minimum gerekli alanlar:

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

AI/research kullanacaksan örnek:

```env
LLM_ENABLED=true
GROQ_API_KEY=your_groq_key
GROQ_MODEL=groq/compound
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_FALLBACK_MODEL=llama-3.3-70b-versatile
EXA_API_KEY=your_exa_key
COINGECKO_DEMO_API_KEY=your_demo_key
```

Telegram kullanacaksan örnek:

```env
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Çalıştır

```bash
python main.py
```

Başladıktan sonra:
- runtime logları `logs/` altına yazılır,
- SQLite veritabanı `DB_PATH` altında oluşur.

## Güvenli İlk Başlangıç

İlk deneme için şunu kullan:

```env
OKX_SANDBOX=true
DRY_RUN=true
LLM_ENABLED=false
```

Anlamı:
- gerçek hesap yerine sandbox kullan,
- gerçek order gönderme,
- önce execution ve reconcile davranışını test et.

## Önemli Ayar Alanları

### Trade ve risk

- `MIN_ORDER_QUOTE_USDT`
- `MIN_FREE_USDT`
- `MAX_OPEN_POSITIONS`
- `MAX_SYMBOL_EXPOSURE_PCT`
- `MAX_TOTAL_EXPOSURE_PCT`
- `MAX_SINGLE_TRADE_PCT`

### Sinyal eşikleri

- `BUY_THRESHOLD`
- `STRONG_BUY_THRESHOLD`
- `SELL_THRESHOLD`
- `STRONG_SELL_THRESHOLD`

### Scale-in

- `SCALE_IN_ENABLED`
- `SCALE_IN_TRIGGER_STREAK`
- `STRONG_SCALE_IN_TRIGGER_STREAK`
- `MAX_SCALE_IN_COUNT`

### TP/SL

- `TPSL_ENABLED`
- `STOP_LOSS_PCT`
- `PARTIAL_TAKE_PROFIT_PCT`
- `FULL_TAKE_PROFIT_PCT`

### Reconcile

- `POSITION_SOURCE_MODE`
- `MIN_POSITION_VALUE_USDT`
- `RECONCILE_WARN_ABS_QUOTE_USDT`
- `RECONCILE_WARN_RATIO`
- `LIVE_FORCE_CLOSE_ON_ZERO_BALANCE`

### AI / research

- `LLM_ENABLED`
- `GROQ_MODEL`
- `GROQ_FALLBACK_MODEL`
- `GROQ_FALLBACK_FALLBACK_MODEL`
- `EXA_API_KEY`
- `COINGECKO_DEMO_API_KEY`

## İzlenecek Loglar

Önemli log aileleri:
- `[RECON]`
- `[POSITION]`
- `[POSITION MISMATCH]`
- `[BUY SENT]` / `[SELL SENT]`
- `[TPSL CHECK]` / `[TPSL TRIGGER]`
- `[GROQ REFRESH]`
- `[AI THRESHOLD]`
- `[GHOST ORDER CLEANED]`

Önemli dosyalar:
- `logs/bot.log`
- `logs/okx_debug.log`

## Telegram Komutları

Komut parsing mantığı `main.py` içindedir.
Telegram transport katmanı `reporting/telegram_bot.py` içindedir.

Bu README bilinçli olarak yüksek seviyede tutuldu. Yetkili komut listesi için `main.py` içindeki slash-command bloklarına bak.

## Bu Projeyi Okuma Sırası

Projeyi hızlı anlamak için şu sırayla ilerle:

1. `README.md`
2. `main.py`
3. `core/reconciler.py`
4. `core/execution_engine.py`
5. `strategy/scoring_engine.py`
6. `strategy/regime_engine.py`
7. `analysis/rumor_analyzer.py`
8. `db/database.py`
9. `db/repositories.py`

## Tasarım Notları

- Bu bot **spot trading** odaklıdır.
- Reconcile ve position accounting en kırılgan katmanlardır.
- `positions` tablosu geçmiş değil, güncel özet state tutar.
- `fills` tablosu muhasebe geçmişidir.
- AI katmanı kararı şekillendirebilir ama execution güvenliği deterministik kalmalıdır.

## Güvenlik Uyarısı

Gerçek hesapta kullanmadan önce:
- sandbox test et,
- `DRY_RUN=true` ile birkaç cycle izle,
- muhafazakâr limitlerle başla,
- `bot.log` dosyasını dikkatle oku,
- canlı sermayeye güvenmeden önce reconcile davranışını doğrula.

Bu proje order gönderebilir.
Yanlış `.env` yapılandırması gerçek finansal zarara yol açabilir.
