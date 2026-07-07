# OKX Spot Trading AI-Based Bot

[English README](./README.md)

Bu repo, OKX spot piyasası için geliştirilmiş modüler bir Python trading botudur. Bot; teknik indikatörler, market rejimi tespiti, deterministik risk ve execution kontrolleri, TP/SL mantığı, SQLite tabanlı state takibi, Telegram bildirimleri, Tkinter tabanlı masaüstü kontrol paneli ve isteğe bağlı AI destekli research katmanını birlikte kullanır.

Bu proje canlı emir gönderebilir. Canlı hesapta kullanmadan önce `OKX_SANDBOX=true` ve `DRY_RUN=true` ile test etmek gerekir.

## Proje Amacı

Kod tabanındaki mevcut yapıya göre botun ana amacı şudur:

- OKX spot sembollerini periyodik olarak taramak
- Teknik skor ve AI skorunu birleştirerek karar üretmek
- Risk, cooldown, exposure ve günlük kayıp guard'larıyla emir kararını filtrelemek
- Pozisyon, order, fill ve cycle state'ini SQLite içinde kalıcı tutmak
- Telegram ve masaüstü arayüz üzerinden operasyonel görünürlük sağlamak

`main.py` içindeki akış ve `config/settings.py` içindeki ayarlar, sistemin production dağıtımdan çok kontrollü test, iteratif geliştirme ve strateji ayarı odaklı olduğunu gösteriyor. Repo içinde production deployment otomasyonu veya paketlenmiş dağıtım akışı görünmüyor.

## Öne Çıkan Bileşenler

- Teknik analiz: `indicators/indicator_engine.py`
- Rejim tespiti: `strategy/regime_engine.py`
- Sinyal ve skor eşleme: `strategy/scoring_engine.py`
- Exchange erişimi: `core/exchange.py`
- Emir yürütme: `core/execution_engine.py`
- Risk kontrolleri: `core/risk_engine.py`
- TP/SL motoru: `core/tpsl_engine.py`
- Reconcile ve pozisyon doğrulama: `core/reconciler.py`
- AI / dış haber analizi: `analysis/rumor_analyzer.py`
- Kalıcı state ve muhasebe: `db/database.py`, `db/repositories.py`
- Telegram bildirimleri: `reporting/telegram_bot.py`
- Masaüstü kontrol paneli: `desktop_app.py`

## Repo Yapısı

Önemli klasör ve dosyalar:

- `main.py`: ana bot döngüsü ve orkestrasyon
- `desktop_app.py`: Tkinter tabanlı yerel kontrol paneli
- `run_desktop_app.bat`: Windows'ta masaüstü uygulamayı başlatmak için kısa yol
- `config/`: `.env` -> `Settings` dönüşümü ve davranış eşikleri
- `core/`: execution, risk, health, reconcile, portfolio ve pozisyon mantığı
- `strategy/`: rejim ve scoring mantığı
- `indicators/`: teknik indikatör hesaplamaları
- `analysis/`: Bedrock/Groq, Exa, CoinGecko tabanlı research/sentiment katmanı
- `db/`: SQLite şema ve repository katmanı
- `reporting/`: Telegram bildirimleri
- `docs/`: masaüstü uygulama notları ve UI tasarım dökümleri
- `scratch/`: hedefli coverage/test script'leri
- `logs/`: çalışma anında üretilen loglar

## Gereksinimler

- Python 3.11+ önerilir
- OKX API bilgileri
- SQLite, Python ile birlikte gelir
- Bildirimler ve uzaktan kontrol için isteğe bağlı Telegram bot token ve chat ID
- AI/research özellikleri için isteğe bağlı AWS Bedrock API key, Groq, Exa ve CoinGecko bilgileri

## Python Bağımlılıkları

`requirements.txt` içinde görünen paketler:

- `ccxt`
- `pandas`
- `pandas-ta-classic`
- `python-dotenv`
- `requests`

Masaüstü uygulama için ayrı paket görünmüyor; bu tutarlı çünkü `desktop_app.py` Tkinter kullanıyor ve Tkinter standart Python dağıtımının parçası.

## Kurulum

Python sürümü repo içinde açıkça pinlenmemiş. Kod tabanı `from __future__ import annotations`, `dataclass`, `zoneinfo` ve modern type hint kullanıyor; pratikte güncel bir Python 3.11+ ortamı tercih etmek daha güvenli olur.

Örnek kurulum:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Ortam Değişkenleri

Bot, `config/settings.py` içindeki fallback zinciri nedeniyle hem `.env` hem de `_.env` dosyasını okuyabiliyor. Ayrıca veritabanı yolu için hem `DB_PATH` hem `SQLITE_PATH` destekleniyor.

Başlangıç için `.env_example` dosyasını kopyalayıp değerleri doldur:

```powershell
Copy-Item .env_example .env
```

Önemli: `.env_example` dosyası olduğu gibi güvenli ilk deneme profili değildir. Kopyaladıktan sonra gerçek teste geçmeden önce `DRY_RUN=1` yap ve temel execution akışı doğrulanana kadar `LLM_ENABLED=0` düşün.

Minimum kritik alanlar:

```env
OKX_API_KEY=your_okx_api_key
OKX_SECRET=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase

OKX_SANDBOX=1
DRY_RUN=1

SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
TIMEFRAME=15m
LOOP_SECONDS=300
DB_PATH=trading_bot.db
```

Önemli notlar:

- `config/settings.py` içinde `OKX_API_KEY`, `OKX_SECRET` ve `OKX_PASSPHRASE` zorunlu. Eksikse uygulama `RuntimeError` ile açılmaz.
- `.env_example` dosyasında veritabanı anahtarı `SQLITE_PATH` olarak geçiyor. Kod geriye uyumluluk için bunu destekliyor.
- AI research tarafı isteğe bağlı. `LLM_ENABLED=0` ile tamamen kapatılabilir.
- Telegram alanları boş bırakılırsa bot çalışabilir; sadece notifier devre dışı kalır.

## Telegram Kontrol Yüzeyi

Bu projede Telegram yalnızca bildirim kanalı değildir. `TELEGRAM_TOKEN` ve `TELEGRAM_CHAT_ID` tanımlıysa `main.py` Telegram update polling yapar ve operasyonel komutları işler.

Mevcut kodda görünen örnek komutlar:

- Durum ve inceleme: `/status`, `/health`, `/positions`, `/dust [max_usdt]`, `/params`, `/streaks`, `/pnl`, `/daily_pnl`, `/exit_stats`, `/price_debug`
- Manuel kontrol: `/trigger`, `/force_refresh`, `/reconcile`
- Yüksek etkili kontroller: `/dust_clean [max_usdt]`, `/close_all`, `/panic`, `/pause_trading`, `/resume_trading`

Bu nedenle Telegram erişimini yalnızca alert kanalı gibi değil, operasyonel kontrol yüzeyi gibi değerlendirmek gerekir.

## Çalıştırma

### Ana bot

```powershell
python main.py
```

Windows'ta aynı ana bot repo kökünden şu dosyayla başlatılabilir:

```powershell
.\start_bot.bat
```

Ana akış kabaca şöyledir:

1. Ayarları yükler.
2. Exchange, DB ve yardımcı motorları hazırlar.
3. Her cycle'da market verisi çeker.
4. Teknik skor, AI skor, rejim ve risk kontrollerini birlikte değerlendirir.
5. Gerekirse emir gönderir.
6. Sonucu log ve SQLite state içine yazar.

İlk güvenli deneme için önerilen ayarlar:

```env
OKX_SANDBOX=1
DRY_RUN=1
LLM_ENABLED=0
```

### Masaüstü kontrol paneli

```powershell
python desktop_app.py
```

Windows'ta doğrudan:

```powershell
.\run_desktop_app.bat
```

Yalnızca veritabanı bağlantısını doğrulamak için:

```powershell
python desktop_app.py --check
```

Farklı bir SQLite dosyasıyla çalıştırmak için:

```powershell
python desktop_app.py --db trading_bot.db
```

Masaüstü uygulama doğrudan emir göndermez; `bot_state` içine kontrol bayrakları yazar. `docs/desktop_app.md` dosyasına göre force refresh, pause trading ve panic mode gibi operasyonel kontroller bu kanal üzerinden yürütülür.

### Windows otomatik başlatma

Windows başlangıç otomasyonu için `start_bot.bat` kullanılır. Önce elle test et:

```powershell
.\start_bot.bat
```

Geçerli kullanıcı oturum açtığında botu başlatacak bir Task Scheduler görevi oluşturmak için PowerShell'i repo kökünde çalıştır:

```powershell
schtasks /Create /TN "OKX Spot Bot" /TR "`"$PWD\start_bot.bat`"" /SC ONLOGON /RL LIMITED /F
```

Operasyon notları:

- Görev geçerli Windows kullanıcısı altında çalışır.
- Görevi etkinleştirmeden önce `.env` hazır olmalıdır.
- Başlangıç yolu doğrulanana kadar `DRY_RUN=1` ve `OKX_SANDBOX=1` kullan.
- Oturum açtıktan sonra botun gerçekten başladığını `logs\bot.log` üzerinden kontrol et.
- Görevi kaldırmak için:

```powershell
schtasks /Delete /TN "OKX Spot Bot" /F
```

## Konfigürasyon Başlıkları

Repo içinde görünen önemli ayar grupları:

- Exchange ve çalışma modu: `OKX_SANDBOX`, `DRY_RUN`, `OKX_TD_MODE`
- Veri toplama: `SYMBOLS`, `TIMEFRAME`, `OHLCV_LIMIT`, `LOOP_SECONDS`
- Emir ve exposure limitleri: `MIN_ORDER_QUOTE_USDT`, `MIN_TRADE_PCT`, `MIN_TRADE_QUOTE_BUFFER_PCT`, `MAX_OPEN_POSITIONS`, `MAX_SYMBOL_EXPOSURE_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_SINGLE_TRADE_PCT`
- Günlük risk guard'ları: `MAX_DAILY_REALIZED_LOSS_USDT`, `MAX_DAILY_DRAWDOWN_PCT`
- Scale-in davranışı: `SCALE_IN_ENABLED`, `SCALE_IN_TRIGGER_STREAK`, `MAX_SCALE_IN_COUNT`
- TP/SL: `STOP_LOSS_PCT`, `PARTIAL_TAKE_PROFIT_PCT`, `FULL_TAKE_PROFIT_PCT`, `BREAK_EVEN_*`, `TRAILING_TAKE_PROFIT_*`
- Rejim motoru: `REGIME_*`
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTIFY_EVERY_CYCLE`
- AI / research: `LLM_ENABLED`, `LLM_PROVIDER_ORDER`, `AWS_BEARER_TOKEN_BEDROCK`, `BEDROCK_*`, `GROQ_*`, `EXA_API_KEY`, `COINGECKO_*`

## Veritabanı ve Durum Yönetimi

`db/database.py` dosyasına göre ana tablolar:

- `orders`: gönderilen emir kayıtları
- `fills`: gerçekleşen trade kayıtları ve fill-level realized PnL
- `positions`: sembol bazlı güncel özet pozisyon state'i
- `symbol_locks`: cooldown ve kilit durumu
- `bot_state`: küçük ama kritik runtime/state anahtarları
- `cycle_reports`: bot cycle özetleri

SQLite tarafı WAL modunda açılıyor. Bu, bot ve masaüstü uygulamanın aynı DB üzerinde okuma/yazma yapması açısından anlamlı bir tercih.

## Loglar ve Operasyonel İzleme

Kod şu anda iki log dosyasına yazar:

- `logs/bot.log`
- `logs/okx_debug.log`

Ana runtime logunu `main.py` yönetir. `core/exchange.py` ise düşük seviye exchange adapter debug çıktısını ayrıca `logs/okx_debug.log` dosyasına yazar.

Takip etmeye değer log etiketleri:

- `[RECONCILE]`
- `[BUY SENT]`
- `[SELL SENT]`
- `[TPSL CHECK]`
- `[TPSL TRIGGER]`
- `[LLM REFRESH]`
- `[AI THRESHOLD]`

## Geliştirme Notları

- `scratch/` klasörü, klasik test paketi yerine hedefli coverage/test script'leri içeriyor. Ayrı bir `tests/` klasörü veya `pytest.ini` görünmüyor.
- `docs/ui_design.md`, gelecekteki kontrol yüzeyi için implementasyon notları içeriyor.
- `docs/ui_mockup.*` dosyaları UI tasarım çıktıları gibi görünüyor; botun çekirdek çalışma akışının parçası değiller.
- Çalışma ağacında `trading_bot.db`, `test.db`, `.coverage` ve `logs/` gibi runtime çıktıları bulunuyor. Bunlar repo davranışını anlamak için faydalı ama kaynak kodun zorunlu parçası değiller.

## Bilinen Sınırlar

Kaynak dosyalara göre görünen sınırlar:

- Proje production-grade dağıtım/paketleme akışını tamamlamış görünmüyor.
- Masaüstü uygulama ilk sürüm kontrol paneli; paketlenmiş `.exe` değil.
- AI research katmanı dış servis ve API anahtarlarına bağlı; başarısızlıklar için fallback mantığı var ama bu katman deterministik değil.
- Gerçek pozisyon doğruluğu açısından kritik katman `core/reconciler.py`; bu nedenle canlı kullanımdan önce reconcile davranışı özellikle izlenmeli.
- `scratch/` script'leri geliştirme kontrolleri için faydalıdır; ancak formal bir CI test paketi değildir.

## Güvenlik

Bu proje kapsamlı güvenlik protokollerini takip eder:
- **Dependabot:** Otomatik bağımlılık ve GitHub Actions güncellemeleri.
- **CodeQL:** Güvenlik açıklarını tespit etmek için Statik Uygulama Güvenlik Testi (SAST).
- **Güvenlik Politikası:** [SECURITY.md](./SECURITY.md) dosyasında tanımlanmıştır.
- **Proaktif Tarama:** CI/CD süreçlerine entegre Bandit ve pip-audit araçları.
- **Pre-commit Kancaları:** Şifre sızıntısı ve kod kalitesi için yerel kontroller.

## Güvenli Doğrulama Akışı


Önerilen doğrulama sırası:

1. `.env` dosyasını doldur.
2. `OKX_SANDBOX=1` ve `DRY_RUN=1` ile başlat.
3. `python main.py` çalıştır.
4. `logs/bot.log` içinde health, reconcile ve signal akışını izle.
5. `python desktop_app.py --check` ile DB erişimini doğrula.
6. `python desktop_app.py` ile üretilen state'in UI tarafında okunabildiğini kontrol et.
7. Ancak bu adımlardan sonra gerçek emir akışına yaklaş.

## Hızlı Okuma Sırası

Repo içine yeni giren biri için önerilen sıra:

1. `README_TR.md`
2. `README.md`
3. `main.py`
4. `config/settings.py`
5. `core/reconciler.py`
6. `core/tpsl_engine.py`
7. `strategy/scoring_engine.py`
8. `analysis/rumor_analyzer.py`
9. `db/database.py`
10. `docs/desktop_app.md`
