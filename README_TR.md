# OKX Spot Trading Bot

[English README](./README.md)

OKX spot piyasası için hazırlanmış; teknik sinyaller, market rejimi, deterministik execution kontrolleri, risk kontrolleri, TP/SL mantığı, Telegram takibi ve isteğe bağlı AI araştırma katmanını birleştiren modüler bir trading bot.

Yasal Uyarı: Gerçek piyasalarda kullanılan bu tür sistemler finansal risk içerir.

## Güncel Durum

Bu repo, şu odaklara sahip gelişmiş bir alpha sürümdür:
- **Kapsamlı white-box testleri ile doğrulanmış mantık akışı (ana modüllerde %95+ branch coverage).**
- Yüksek denetlenebilirlik ve sadeleştirilmiş muhasebe (reconcile) katmanı.
- İteratif geliştirme ve strateji optimizasyonu.

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

- **Reconciler Yenilemesi:** Muhasebe mantığı (`reconciler.py`), daha yüksek okunabilirlik ve hata yönetimi için 843 satırdan 167 satıra düşürülerek sadeleştirildi.
- **Yüksek Test Kapsamı:** `ExecutionEngine`, `TPSLEngine` ve `RegimeEngine` modüllerinde %95-100 arası branch coverage başarısına ulaşıldı.
- **Veri Bütünlüğü:** Repository katmanı, bellek içi (in-memory) SQLite test paketleri ile SQL bazında doğrulandı.
- **Pozisyon Tutarlılığı:** Açık pozisyon miktarı için borsa bakiyesi (exchange balance) birincil kaynak olarak kullanılır.

## Mimari

### Ana akış

- `main.py` ana runtime döngüsünü yönetir.
- `core/` exchange, execution, reconcile, risk, TP/SL ve portfolio mantığını içerir.
- `strategy/` sinyal mapping ve rejim mantığını taşır.
- `indicators/` teknik indikatörleri üretir.
- `db/` kalıcı state ve muhasebe verisini tutar.

### Temel tasarım ilkesi

Bot research tarafında AI desteklidir; ancak execution güvenliği deterministik kalır. Order gönderimi, kilitler ve muhasebe katmanı sıkı ve denetlenebilir bir yapıdadır.

## Ana Bileşenler

### Reconcile Katmanı (`core/reconciler.py`)
Projenin en hassas parçasıdır. Botun muhasebesini borsa durumuyla eşitlemek için order senkronu, fill kayıtları ve pozisyon inşasını yönetir.

### Execution Katmanı (`core/execution_engine.py`)
Kararları borsa emirlerine dönüştürür; cooldown ve lock yönetimini sıkı bir şekilde uygular.

### TP/SL Katmanı (`core/tpsl_engine.py`)
Stop Loss, Kısmi/Tam Kar Al, Başabaş Stop ve Trailing koruması ile kârı korur.

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

## Ortam Ayarları

Proje `.env` ve `_.env` dosyalarını okur. Minimum gerekli alanlar:
```env
OKX_API_KEY=key
OKX_SECRET=secret
OKX_PASSPHRASE=pass
OKX_SANDBOX=true
DRY_RUN=true
SYMBOLS=BTC/USDT,ETH/USDT
TIMEFRAME=15m
LOOP_SECONDS=60
DB_PATH=trading_bot.db
```

## Güvenlik Uyarısı

Gerçek hesaba geçmeden önce:
- sandbox üzerinde test edin,
- `DRY_RUN=true` ile sistemi izleyin,
- canlı sermayeye güvenmeden önce muhasebe (reconcile) davranışını doğrulayın.
