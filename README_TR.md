# OKX Spot Trading Bot

OKX borsası için geliştirilmiş, teknik sinyal işleme ve risk yönetimi özelliklerine sahip modüler trading botu.

## Proje Yapısı

- `main.py`: Ana yürütme döngüsü ve koordinasyon.
- `core/`: Temel ticaret mantığı.
    - `exchange.py`: CCXT üzerinden OKX API entegrasyonu.
    - `execution_engine.py`: Emir iletimi ve soğuma (cooldown) yönetimi.
    - `reconciler.py`: Muhasebe, gerçekleşen emirlerin kaydı ve pozisyon senkronizasyonu.
    - `tpsl_engine.py`: Kar al (TP) ve zarar durdur (SL) yönetimi.
    - `risk_manager.py`: Pozisyon limitleri ve düşüş (drawdown) koruması.
- `strategy/`: Strateji ve skorlama bileşenleri.
    - `scoring_engine.py`: Sinyal-aksiyon eşleşmesi.
    - `regime_engine.py`: Market rejimi tespiti (Trend, Yatay, Volatil).
- `indicators/`: Teknik gösterge hesaplamaları.
- `db/`: Veritabanı katmanı ve repolar.
    - `database.py`: SQLite bağlantı ve şema yönetimi.
    - `repositories.py`: Emir, fill ve pozisyon verilerine erişim.
- `analysis/`: AI araştırma ve market analizi (isteğe bağlı LLM entegrasyonu).
- `reporting/`: Telegram ve konsol raporlama kayıtları.
- `scratch/`: Mantıksal doğrulama için kullanılan test paketleri.

## Temel Özellikler

- **Yenilenmiş Reconciler:** Pozisyon takibi için sadeleştirilmiş muhasebe mantığı.
- **Deterministik İşlem:** Sıkı emir yönetimi ve hata yakalama.
- **Dinamik Rejim Tespiti:** Market koşullarına göre strateji adaptasyonu.
- **Çok Katmanlı TP/SL:** Zarar durdur, kısmi kar al, başabaş ve trailing koruması.

## Yapılandırma (.env)

| Değişken | Açıklama |
| :--- | :--- |
| `OKX_API_KEY` | Borsa API Anahtarı |
| `SYMBOLS` | İşlem görecek pariteler (Örn: BTC/USDT) |
| `DRY_RUN` | True ise emirler simüle edilir |
| `TPSL_ENABLED` | TP/SL yönetimini etkinleştirir |
| `STOP_LOSS_PCT` | Zarar durdurma yüzdesi |
| `MAX_OPEN_POSITIONS` | Maksimum eşzamanlı işlem sayısı |

## Test ve Doğrulama

Proje, branch coverage analizi içeren birim ve entegrasyon testlerine sahiptir.

### Testlerin Çalıştırılması
```bash
$env:PYTHONPATH="."
pytest scratch/ --cov-branch --cov-report=term-missing
```

### Kapsama Durumu
- `strategy/regime_engine.py`: %100 Branch Coverage
- `core/execution_engine.py`: %100 Branch Coverage
- `core/tpsl_engine.py`: %96 Branch Coverage
- `db/repositories.py`: %90 Branch Coverage
- `core/reconciler.py`: %90 Branch Coverage

## Güvenlik Notu
Konfigürasyonları Sandbox modunda test edin ve canlı sermaye kullanmadan önce sistemi `DRY_RUN=true` modunda gözlemleyin.
