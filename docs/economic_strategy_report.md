# Acil Ekonomik Strateji Raporu

Tarih: 2026-04-27

## Root cause

Son 10 günde negatif yazmanın ana sebebi tek bir hatalı sinyal değil; risk kapılarının kapalı veya gevşek kalması, zayıf piyasa rejimlerinde long maruziyetin korunması ve çıkışların zararı büyüdükten sonra kademeli çalışmasıdır.

Kanıtlar:

- `logs/bot.log` ve `trading_bot.db` verisine göre 2026-04-18 ile 2026-04-27 arasında realized PnL yaklaşık `-1438.98 USDT`.
- En büyük zarar kaynakları: `ADA/USDT -654.66`, `ETH/USDT -496.61`, `SOL/USDT -389.75`.
- Pozitif kalan semboller: `BTC/USDT +90.59`, `BNB/USDT +228.03`.
- Exit reason bazında en büyük zarar `indicator_partial_close`: yaklaşık `-1018.64 USDT`.
- `stop_loss_hit` sadece 4 fill üretmiş ve yaklaşık `-319.29 USDT` zarar yazmış.
- `auto_risk_guard_state.enabled=false`; çünkü `MAX_DAILY_REALIZED_LOSS_USDT` ve `MAX_DAILY_DRAWDOWN_PCT` pratikte 0/default durumda.
- Bot process aktif: `python main.py`.
- Acil müdahale olarak `bot_state.trading_paused=true` ayarlandı. Bu yeni entry ve scale-in kapısını kapatır; exit işlemlerini kapatmaz.

## Ekonomik yorum

Bot şu anda "dipten alma + zarar büyüyünce azaltma" davranışına kaymış. CHOP/RANGE/VOLATILE rejimlerinde bu davranış ekonomik olarak kötü asimetri üretir: küçük kârlar erken korunurken, negatif sinyaller gelene kadar açık longlar taşınır.

Mevcut PnL dağılımı sembol seçimi problemini de gösteriyor. Aynı sistem BTC ve BNB tarafında pozitif kalırken ADA, ETH ve SOL tarafında zarar yoğunlaşıyor. Bu, tüm botu tek eşikle yönetmenin yanlış olduğunu gösterir; symbol-level kill switch ve symbol-level risk budget gerekiyor.

## Acil strateji

1. Yeni risk alımı yeniden açıldı.
   - İlk acil fren olarak `trading_paused=true` uygulanmıştı.
   - Kullanıcının risk-on talebi sonrası yeni rejim policy devreye alındı ve `trading_paused=false` yapıldı.
   - Doğrulanan log: `entries_blocked=False entry_block_reason=none`.

2. Açık pozisyonlar manuel yatırım kararı olmadan zorla kapatılmadı.
   - Açık pozisyonlar: `ADA/USDT`, `BNB/USDT`, `SOL/USDT`.
   - Mevcut sistem exitleri çalıştırmaya devam edebilir.

3. Günlük devre kesici zorunlu hale getirilmeli.
   - `MAX_DAILY_REALIZED_LOSS_USDT` pozitif bir değere çekilmeli.
   - `MAX_DAILY_DRAWDOWN_PCT` pozitif bir değere çekilmeli.
   - Bu iki eşik 0 kaldığı sürece bot günlük kötü performansta kendini durdurmaz.

4. Symbol-level kill switch eklenmeli.
   - Son 10 günde `ADA`, `ETH`, `SOL` yeni entry için kapatılmalı.
   - BTC ve BNB izleme modunda kalabilir; yeni entry yine global pause kalkmadan açılmamalı.

5. Rejim bazlı long izni daraltılmalı.
   - CHOP rejiminde yeni BUY kapalı olmalı veya sadece çok güçlü, kapanmış mum teyitli sinyalle açılmalı.
   - RANGE rejiminde sadece destekten dönüş teyidi varsa entry açılmalı.
   - VOLATILE rejiminde pozisyon boyutu düşürülmeli, stop daha erken devreye girmeli.

## Uygulanan risk-on rejim stratejisi

Kodda `strategy.scoring_engine.apply_regime_execution_policy` eklendi ve `main.py` içinde `evaluate_signal` sonrasında, TPSL öncesinde çalıştırıldı.

Rejim kuralları:

- `TREND`:
  - `trend_bias=UP` ise BUY/STRONG_BUY fırsatları açık kalır.
  - `trend_bias=DOWN` ise yalnızca güçlü ters dönüşte ve pozitif effective AI ile alım açık kalır.
- `RANGE`:
  - Mean-reversion kanıtı gerekir: RSI, StochRSI veya Bollinger tarafında dip/dönüş sinyali yoksa alım yapılmaz.
  - Zayıf katalist ve negatif AI varsa düşük conviction range alımı bloklanır.
- `CHOP`:
  - Sadece strong-score alımlar geçer.
  - Pozisyon boyutu `buy_pct` seviyesine düşürülür.
- `VOLATILE`:
  - Breakout/continuation teyidi gerekir: trend bias yukarı ve EMA/price teyidi.
  - Teyit yoksa volatile dip yakalama kapalıdır.
- Açık pozisyon çıkışları:
  - CHOP/VOLATILE rejiminde `-2.5%` veya daha kötü PnL ile gelen `PARTIAL_CLOSE`, `FULL_CLOSE` olur.
  - Down-bias ortamda `-1.8%` veya daha kötü PnL ile gelen `PARTIAL_CLOSE`, `FULL_CLOSE` olur.

İlk canlı doğrulama:

- 2026-04-27 23:37 cycle içinde `SOL/USDT` için policy çalıştı.
- Ham sinyal: `PARTIAL_CLOSE`.
- Policy sonucu: `FULL_CLOSE`.
- Neden: `policy_full_close_loser_down_bias`.
- PnL yaklaşık `-3.0%`.

## Geliştirici yapılacakları

P0:

- `.env` risk guard değerlerini pozitif yap.
- `trading_paused` açıkken signal streak birikmediğini logdan doğrula.
- Son 10 gün için symbol-level PnL rapor komutu ekle veya mevcut `/daily_pnl` çıktısını symbol kırılımıyla genişlet.

P1:

- `MAX_DAILY_REALIZED_LOSS_USDT` tetiklenince sadece entry değil, scale-in state ve BUY streaklerini de temizlediğini test et.
- `indicator_partial_close` zarar ürettiğinde kalan pozisyonun tekrar tekrar aynı cyclelarda kademeli satılıp satılmadığını backtest et.
- `SELL` sinyali pozisyon zarardayken `PARTIAL_CLOSE` yerine belirli koşullarda `FULL_CLOSE` üretmeli mi, simülasyonla ölç.

P2:

- Symbol-level cooldown/ban tablosu ekle.
- Son N fill negatifse sembolü otomatik karantinaya al.
- Rejim + sembol + exit_reason bazında performans raporu üret.

## Doğrulama

- `bot_state` içinde `trading_paused` değeri `true` olmalı.
- Bir sonraki cycle logunda yeni BUY/scale-in denemeleri `blocked:trading_paused` ile durmalı.
- Exit branch etkilenmemeli; açık pozisyonlar için TPSL ve SELL kaynaklı exit logları çalışmaya devam etmeli.
- `auto_risk_guard_state.enabled` ancak `.env` risk guard limitleri pozitif yapıldıktan sonra `true` olmalı.

## Regression risks

- `trading_paused=true` yeni kâr fırsatlarını da kapatır; bu bilinçli acil fren kararıdır.
- Exitler açık kaldığı için zarar realize olmaya devam edebilir; bu yeni risk almak değil, mevcut riski azaltmaktır.
- Risk guard limitleri çok dar seçilirse bot sürekli entry kapatır.
- Symbol-level ban eklenirken açık pozisyon exitleri yanlışlıkla engellenmemeli.
