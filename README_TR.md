# OKX Spot Trading Bot - Teknik Kılavuz

## Genel Bakış
Teknik göstergeler, market rejimi tespiti ve deterministik execution (emir yürütme) prensiplerine dayalı modüler OKX spot trading botu. Sistem mimarisi, muhasebe bütünlüğü ve risk yönetimi tarafında yüksek denetlenebilirlik hedeflenerek tasarlanmıştır.

## Proje Yapısı ve Modül Fonksiyonları

### 1. Yürütme ve Orkestrasyon (`/`)
- `main.py`: Giriş noktası. Sonsuz ticaret döngüsünü yönetir, cycle zamanlamasını koordine eder ve global istisnaları (exception) yakalar.
- `desktop_app.py`: Bot durumunu ve logları izlemek için kullanılan isteğe bağlı grafik arayüz (GUI).

### 2. Çekirdek Ticaret Mantığı (`core/`)
- `exchange.py`: OKX için düşük seviyeli CCXT wrapper katmanı. Özel/genel API çağrılarını, sayfalandırmayı (pagination) ve hata normalizasyonunu yönetir.
- `execution_engine.py`: Atomik emir iletim katmanı. Client Order ID'lerini, soğuma kilitlerini (cooldown locks) ve dry-run simülasyonlarını yönetir. %100 branch coverage ile doğrulanmıştır.
- `reconciler.py`: Muhasebe çekirdeği. Yerel state'i borsa bakiyeleri ve gerçekleşen emirler (fills) ile senkronize eder. Ortalama giriş fiyatını ve gerçekleşen kâr/zararı (PnL) yeniden hesaplar. 843 satırdan 167 satıra indirilerek sadeleştirilmiştir.
- `tpsl_engine.py`: Çok aşamalı çıkış yönetimi. Zarar Durdur (SL), Kısmi TP, Tam TP, Başabaş (Break-Even) ve Trailing tetikleyicileri için canlı fiyatları izler.
- `risk_manager.py`: Günlük maksimum zarar ve drawdown korumaları. Eşik değerler aşıldığında yeni girişleri engeller.
- `portfolio_manager.py`: Toplam maruziyeti (exposure) ve varlık dağılımını hesaplar.

### 3. Strateji ve İndikatörler (`strategy/`, `indicators/`)
- `scoring_engine.py`: Teknik ve AI skorlarını birleştirerek AL/SAT/BEKLE kararları üretir.
- `regime_engine.py`: Fiyat hareketlerini analiz ederek Trend, Yatay (Range) veya Volatil rejimlerini tespit eder.
- `technical_indicators.py`: RSI, MACD, Bollinger Bantları vb. için özel veya kütüphane tabanlı hesaplamalar.

### 4. Analiz ve Yapay Zeka (`analysis/`)
- `rumor_analyzer.py`: Haber ve duygu analizi için Exa, CoinGecko ve Groq entegrasyonu.
- `model_bridge.py`: LLM fallback zincirlerini yönetir (Örn: Groq hatasında OpenAI veya Llama modellerine geçiş).

### 5. Veri Kalıcılığı (`db/`)
- `database.py`: SQLite ilkleme, şema migrasyonları ve bağlantı yönetimi.
- `repositories.py`: Veri erişim nesneleri:
    - `OrdersRepo`: Durum takibi ve hayalet emir (ghost order) temizliği.
    - `FillsRepo`: İşlem geçmişi ve kâr/zarar toplamları.
    - `PositionsRepo`: Güncel açık pozisyon özetleri.
    - `LocksRepo`: Sembol bazlı geçici işlem kilitleri.
    - `BotStateRepo`: Streak ve rejim state'i için anahtar-değer depolama.

## Yapılandırma Parametreleri (.env)

### Risk Yönetimi
- `MAX_OPEN_POSITIONS`: Maksimum eşzamanlı sembol sayısı.
- `MAX_SYMBOL_EXPOSURE_PCT`: Bir sembole ayrılan maksimum sermaye yüzdesi.
- `MAX_DAILY_REALIZED_LOSS_USDT`: Günlük zarar limiti (yeni girişleri durdurur).
- `MAX_DAILY_DRAWDOWN_PCT`: Sermaye bazlı drawdown limiti.

### TP/SL Yapılandırması
- `STOP_LOSS_PCT`: Ortalama girişten itibaren sabit zarar durdurma yüzdesi.
- `PARTIAL_TAKE_PROFIT_PCT`: %50 çıkışı tetikleyen kâr seviyesi.
- `FULL_TAKE_PROFIT_PCT`: Tam çıkışı tetikleyen kâr seviyesi.
- `BREAK_EVEN_STOP_ENABLED`: Kâr belirli bir noktaya ulaştığında SL'i giriş fiyatına çeker.
- `TRAILING_TAKE_PROFIT_ENABLED`: Takip eden kâr al mantığını etkinleştirir.

## Veritabanı Şema Detayları

- **orders**: `client_order_id`, `status` (PENDING, OPEN, FILLED, CANCELED), `qty`, `created_at_ms`.
- **fills**: `trade_id`, `order_id`, `qty`, `price`, `realized_pnl_quote`, `exit_reason`.
- **positions**: `symbol`, `qty`, `avg_entry_price`, `realized_pnl_quote`, `status`.

## Test Metodolojisi

Sistem, mantıksal doğrulama için `pytest` ve `pytest-cov` kullanır.

### `scratch/` İçindeki Test Senaryoları:
- **Repositories:** Disk yan etkisi olmadan SQL bütünlüğünü test etmek için bellek içi SQLite (`:memory:`) kullanır.
- **Execution Engine:** Borsa hata senaryolarını test etmek için mock exchange yanıtları kullanılır.
- **Reconciler:** Borsa bakiyesi ile yerel veritabanı arasındaki "Uyumsuzluk" senaryoları simüle edilir.

### Komut
```bash
$env:PYTHONPATH="."
pytest scratch/ -vv --cov-branch --cov-report=term-missing
```

### Doğrulanmış Kapsama Oranları
- `regime_engine.py`: %100 Branch
- `execution_engine.py`: %100 Branch
- `tpsl_engine.py`: %96 Branch
- `repositories.py`: %90 Branch
- `reconciler.py`: %90 Branch
