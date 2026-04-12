from __future__ import annotations

"""
RECONCILER
==========

Bu dosya botun en kritik muhasebe katmanlarından biridir. Kısaca görevi:
- exchange order status sync
- exchange fills / trades çekme
- DB fills tablosuna yazma
- fills + balance verisinden position rebuild etme

Neden kritik?
-------------
Çünkü trading botların en tehlikeli bug'ları çoğu zaman sinyal motorunda değil,
POZİSYON GERÇEĞİ ile DB GERÇEĞİ birbirinden koptuğunda ortaya çıkar.

Bu dosya özellikle şu problemleri önlemek için vardır:
- sandbox false zero balance
- duplicate fill
- avg_entry drift
- force close yanlış tetiklenmesi
- dust nedeniyle phantom position
- fee kaynaklı cost basis hatası

Yeni chat'te buraya özellikle bakılması gereken yerler:
- _sync_trades
- _build_from_fills
- _rebuild_position
- meaningful mismatch / dust / preserve_on_balance_error mantıkları
"""


import logging
import time


class Reconciler:
    """
    Reconciler.

    Bu sınıfın görevi üç parçalıdır:
    1) Order status sync
    2) Exchange trade history -> fills tablosu
    3) Position rebuild

    EN BÜYÜK TASARIM DERSİ:
    Eski versiyon fills_qty ile base balance'ı sert şekilde birbirine override ediyordu.
    Bunun sonucu:
    - sandbox'ta false zero balance yüzünden fake force close
    - live'da qty clamp yüzünden avg bozulması
    - TP/SL state reset
    - dust miktarlarda gereksiz close/reopen churn

    Bu yeni versiyonda yaklaşım daha nettir:
    - fills = muhasebe ve realized pnl kaynağı
    - balance = elde gerçekten ne kadar coin var sorusunun cevabı
    - qty için balance önceliklidir
    - avg_entry_price için mümkünse fills muhasebesi kullanılır
    - ama "mikro toz" miktarları gerçek pozisyon sayılmaz
    - balance fetch patladı diye açık pozisyonu kör şekilde sıfırlamayız
    """

    def __init__(self, exchange, orders_repo, fills_repo, positions_repo, bot_state_repo=None):
        # Reconciler tek başına çalışmaz.
        # Borsaya erişim için exchange, DB erişimi için repo nesneleri gerekir.
        self.exchange = exchange
        self.orders_repo = orders_repo
        self.fills_repo = fills_repo
        self.positions_repo = positions_repo
        self.bot_state_repo = bot_state_repo

    def now_ms(self) -> int:
        # Her yerde aynı zaman formatını kullanalım diye helper.
        return int(time.time() * 1000)

    def sync_symbol(self, symbol: str) -> None:
        # Bir symbol'ü uzlaştırmanın ana sırası:
        # 1) order durumlarını güncelle
        # 2) trade/fill geçmişini çek
        # 3) eldeki verilerle pozisyonu yeniden kur
        self._sync_orders(symbol)
        self._sync_trades(symbol)
        self._rebuild_position(symbol)

    def _settings(self):
        # Ayarları exchange üstünden alıyoruz çünkü projede öyle bağlanmış.
        return getattr(self.exchange, "settings", None)

    def _reconcile_anchor_key(self, symbol: str) -> str:
        # Bot state içinde symbol'e özel anchor anahtarı üret.
        return f"reconcile_anchor:{symbol}"

    def _reconcile_mismatch_key(self, symbol: str) -> str:
        # Aynı symbol için mismatch sayacını tutan anahtar.
        return f"reconcile_mismatch_count:{symbol}"

    def _load_reconcile_anchor(self, symbol: str) -> dict | None:
        # Daha önce rebase yapıldıysa o başlangıç noktasını buradan okuruz.
        if not self.bot_state_repo:
            return None
        raw = self.bot_state_repo.get(self._reconcile_anchor_key(symbol))
        if not isinstance(raw, dict):
            return None
        return raw

    def _clear_reconcile_anchor(self, symbol: str) -> None:
        # Anchor artık gerekmiyorsa state'ten sil.
        if self.bot_state_repo:
            self.bot_state_repo.delete(self._reconcile_anchor_key(symbol))

    def _set_reconcile_anchor(
        self,
        *,
        symbol: str,
        qty: float,
        avg_entry_price: float,
        realized_pnl_quote: float,
        fees_quote: float,
        reason: str,
    ) -> None:
        # Anchor demek şudur:
        # "Geçmiş fills zincirini bu noktadan sonra güven."
        # Böylece çok eski kirli veri bugünkü pozisyonu sonsuza kadar bozmaz.
        if not self.bot_state_repo:
            return
        last_fill = self.fills_repo.latest_for_symbol(symbol)
        payload = {
            "qty": float(qty or 0.0),
            "avg_entry_price": float(avg_entry_price or 0.0),
            "realized_pnl_quote": float(realized_pnl_quote or 0.0),
            "fees_quote": float(fees_quote or 0.0),
            "last_fill_ts_ms": int((last_fill or {}).get("timestamp_ms") or 0),
            "last_trade_id": str((last_fill or {}).get("trade_id") or ""),
            "reason": reason,
            "updated_at_ms": self.now_ms(),
        }
        self.bot_state_repo.set(self._reconcile_anchor_key(symbol), payload, self.now_ms())
        self.bot_state_repo.delete(self._reconcile_mismatch_key(symbol))
        logging.warning(
            "[RECONCILE REBASE] %s qty=%s avg=%s reason=%s cursor_ts=%s trade_id=%s",
            symbol,
            payload["qty"],
            payload["avg_entry_price"],
            reason,
            payload["last_fill_ts_ms"],
            payload["last_trade_id"],
        )

    def _register_mismatch(self, symbol: str) -> int:
        # Mismatch sayısını arttırır ve yeni değeri döndürür.
        if not self.bot_state_repo:
            return 0
        key = self._reconcile_mismatch_key(symbol)
        now_ms = self.now_ms()
        current = int(self.bot_state_repo.get(key) or 0) + 1
        self.bot_state_repo.set(key, current, now_ms)
        return current

    def _clear_mismatch(self, symbol: str) -> None:
        # Artık uyuşmazlık yoksa sayacı temizle.
        if self.bot_state_repo:
            self.bot_state_repo.delete(self._reconcile_mismatch_key(symbol))

    # ----------------------------
    # ORDER SYNC
    # ----------------------------
    def _sync_orders(self, symbol: str) -> None:

        # Burada sadece hala "açık olabilir" diye düşündüğümüz order'ları geziyoruz.
        # Daha önce ghost diye temizlenen order'ları tekrar tekrar kontrol etmiyoruz.
        rows = self.orders_repo.get_sync_candidates(symbol)

        for row in rows:

            client_order_id = row["client_order_id"]
            exchange_order_id = row["exchange_order_id"]

            if self.orders_repo.is_ghost_cleaned(client_order_id):
                continue

            try:

                if exchange_order_id:

                    order = self.exchange.fetch_order(exchange_order_id, symbol)

                else:

                    order = self.exchange.fetch_order_by_client_id(
                        client_order_id,
                        symbol
                    )

            except Exception as exc:

                err_text = str(exc)

                # -------------------------
                # GHOST ORDER CLEANUP
                # -------------------------

                if (
                    "51603" in err_text
                    or "Order does not exist" in err_text
                    or "order not found" in err_text.lower()
                ):
                    # Borsa "bu order yok" diyorsa bunu ghost order kabul ediyoruz.
                    # DB'ye işaret koyduğumuz için aynı order tekrar log spam üretmiyor.
                    try:
                        self.orders_repo.mark_ghost_cleaned(
                            client_order_id,
                            exchange_order_id,
                            self.now_ms(),
                            "ghost_order_not_found",
                        )
                    except Exception as loop_e:
                        logging.error(f"[GHOST MARK ERROR] {symbol} {loop_e}")
                    
                    logging.warning(
                        f"[GHOST ORDER CLEANED] {symbol} client_order_id={client_order_id}"
                    )
                    continue

                logging.warning(
                    f"[ORDER SYNC ERROR] {symbol} client_order_id={client_order_id} err={exc}"
                )
                continue


            if not order:

                continue


            status = str(order.get("status") or "open").upper()

            ex_id = order.get("id") or exchange_order_id


            self.orders_repo.update_status(

                client_order_id,

                ex_id,

                status,

                self.now_ms(),

            )
    # ----------------------------
    # TRADES FETCH
    # ----------------------------
    def _fetch_exchange_trades(self, symbol: str) -> list[dict]:
        # Borsadan trade geçmişini parça parça çekiyoruz.
        # seen_ids ile aynı trade iki kez eklenmesin diye koruma yapıyoruz.
        all_trades = []
        seen_ids = set()
        since = None

        settings = self._settings()
        reconcile_since_ms = int(getattr(settings, "reconcile_since_ms", 0) or 0) if settings else 0
        if reconcile_since_ms > 0:
            since = reconcile_since_ms

        for _ in range(10):
            # Sonsuz döngü riskine girmemek için maksimum 10 tur deniyoruz.
            try:
                trades = self.exchange.fetch_my_trades(symbol, since=since, limit=100)

                if not trades:
                    break

                trades = sorted(trades, key=lambda x: x["timestamp"])
                new_count = 0

                for t in trades:
                    tid = str(t.get("id"))
                    if not tid or tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    all_trades.append(t)
                    new_count += 1

                if new_count == 0:
                    break

                since = trades[-1]["timestamp"] + 1
                # Son gördüğümüz trade'den bir sonraki milisaniyeden devam ediyoruz.
                # Böylece aynı trade'i tekrar çekme ihtimali azalıyor.

                if len(trades) < 100:
                    break

            except Exception as e:
                # Trade çekme patlarsa tüm botu değil sadece bu parçayı uyarı ile geçiyoruz.
                logging.warning(f"[TRADES FETCH ERROR] {symbol} err={e}")
                break

        logging.info(f"[TRADES FETCH FULL] {symbol} count={len(all_trades)}")
        return all_trades

    def _apply_fill_accounting(
        self,
        *,
        side: str,
        fill_qty: float,
        fill_price: float,
        fill_cost: float,
        fee_cost: float,
        fee_ccy: str | None,
        base_asset: str,
        quote_asset: str,
        current_qty: float,
        current_avg: float,
    ) -> tuple[float, float, float, float]:
        """
        Tek bir fill'i muhasebe state'ine uygular.

        Dönüş:
        - next_qty
        - next_avg
        - realized_pnl_quote_for_this_fill
        - fee_quote_equivalent

        Neden ayrı helper?
        - Aynı fill muhasebesi hem /daily_pnl backfill'i için hem de position rebuild için kullanılmalı.
        - İki farklı yerde iki farklı formül olursa realized toplamı ile positions total birbirinden kopar.
        - Bu helper tek truth source gibi davranır.
        """
        # current_qty/current_avg = bu fill gelmeden önce elimizde ne vardı?
        # fill geldikten sonra yeni qty, yeni avg ve varsa realized pnl hesaplıyoruz.
        qty = float(current_qty or 0.0)
        avg_entry = float(current_avg or 0.0)
        realized_pnl_quote = 0.0
        fee_quote_equivalent = 0.0

        if fee_ccy == quote_asset:
            fee_quote_equivalent = float(fee_cost or 0.0)
        elif fee_ccy == base_asset:
            fee_quote_equivalent = float(fee_cost or 0.0) * float(fill_price or 0.0)

        if side == "buy":
            # Buy gelince eldeki coin artar.
            # Ortalama maliyet de eski maliyet + yeni alım maliyeti ile güncellenir.
            effective_qty = float(fill_qty or 0.0)
            if fee_ccy == base_asset:
                effective_qty = max(0.0, effective_qty - float(fee_cost or 0.0))

            total_cost = qty * avg_entry + float(fill_cost or 0.0)
            qty += effective_qty
            avg_entry = total_cost / qty if qty > 1e-12 else 0.0
            return qty, avg_entry, realized_pnl_quote, fee_quote_equivalent

        if side == "sell":
            # Sell gelince qty azalır.
            # Bu sırada kar/zarar da burada çıkar.
            if qty <= 1e-12:
                return 0.0, 0.0, 0.0, fee_quote_equivalent

            close_qty = min(qty, float(fill_qty or 0.0))
            realized_pnl_quote = close_qty * (float(fill_price or 0.0) - avg_entry)

            # P0.5 notu:
            # realized pnl'yi fill-level tuttuğumuz için fee'yi de burada düşüyoruz.
            # Eski kod sadece quote fee'yi düşüyordu. Bu, base fee ile satılan fill'lerde
            # ekonomik pnl ile raporlanan pnl arasında sapma yaratıyordu.
            # Burada fee'nin quote eşdeğerini realized tarafına da yansıtıyoruz.
            realized_pnl_quote -= fee_quote_equivalent

            qty -= close_qty
            if qty <= 1e-12:
                qty = 0.0
                avg_entry = 0.0

            return qty, avg_entry, realized_pnl_quote, fee_quote_equivalent

        return qty, avg_entry, 0.0, fee_quote_equivalent

    def _refresh_fill_realized_pnl(self, symbol: str) -> None:
        """
        Bir symbol için fills zincirini baştan yürütür ve realized_pnl_quote kolonunu backfill eder.

        Neden gerekli?
        - Eski DB'lerde bu kolon sonradan eklendi; eski fill satırları 0 ile gelir.
        - Sadece yeni insert'lere realized yazmak geçmiş veriyi düzeltmez.
        - /daily_pnl doğru olsun istiyorsak geçmiş tüm fills için bu kolonun tekrar hesaplanması gerekir.
        """
        # Burada geçmiş fills zincirini baştan yürütüyoruz.
        # Amaç: DB'deki her sell fill için realized pnl kolonunu doğru yapmak.
        fills = self.fills_repo.by_symbol(symbol)
        base_asset, quote_asset = symbol.split("/")
        fills = sorted(fills, key=lambda x: (x["timestamp_ms"], x["trade_id"]))

        qty = 0.0
        avg_entry = 0.0

        for f in fills:
            side = str(f["side"] or "").lower()
            fill_qty = float(f["qty"] or 0.0)
            fill_price = float(f["price"] or 0.0)
            fill_cost = float(f["cost"] or 0.0)
            fee_cost = float(f["fee_cost"] or 0.0)
            fee_ccy = f.get("fee_currency")

            qty, avg_entry, realized_pnl_quote, _ = self._apply_fill_accounting(
                side=side,
                fill_qty=fill_qty,
                fill_price=fill_price,
                fill_cost=fill_cost,
                fee_cost=fee_cost,
                fee_ccy=fee_ccy,
                base_asset=base_asset,
                quote_asset=quote_asset,
                current_qty=qty,
                current_avg=avg_entry,
            )
            self.fills_repo.update_realized_pnl_quote(str(f["trade_id"]), float(realized_pnl_quote))

    # ----------------------------
    # INSERT TRADES
    # ----------------------------
    def _sync_trades(self, symbol: str) -> None:
        # Borsadan trade'leri çek, DB'ye eksik olanları yaz.
        trades = self._fetch_exchange_trades(symbol)
        inserted = 0

        for t in trades:
            trade_id = str(t.get("id") or "").strip()
            if not trade_id:
                continue

            if self.fills_repo.exists(trade_id):
                # Aynı trade daha önce yazıldıysa tekrar ekleme.
                continue

            fee = t.get("fee") or {}
            order_info = t.get("info") or {}
            client_order_id = order_info.get("clOrdId") or order_info.get("clientOrderId") or None

            # Exit reason: sell fill ise ve bot_state'te pending_exit_reason varsa oku ve temizle.
            # Bu sayede stop_loss / partial_tp / full_tp / indicator_sell ayrımı fills tablosuna yazılır.
            _exit_reason_val = None
            if str(t.get("side") or "").lower() == "sell" and self.bot_state_repo:
                # Sell fill bir çıkıştan geldiyse nedenini bot_state'ten okuyup fill'e iliştiriyoruz.
                if client_order_id:
                    _pending = self.bot_state_repo.get(f"pending_exit_reason:{symbol}")
                    if isinstance(_pending, dict) and _pending.get("client_order_id") == client_order_id:
                        _exit_reason_val = str(_pending.get("reason") or "")
                        self.bot_state_repo.delete(f"pending_exit_reason:{symbol}")

            # İlk insert anında realized_pnl_quote=0 yazıyoruz.
            # Doğru realized değer aynı fonksiyonun sonunda _refresh_fill_realized_pnl ile
            # tüm zincir tekrar yürütülerek backfill edilir.
            # Böylece mevcut fill sıralaması, geçmiş DB kayıtları ve duplicate senaryolarında
            # tek tek insert sırasında yanlış incremental state taşımıyoruz.
            self.fills_repo.insert(
                trade_id=trade_id,
                order_id=str(t.get("order") or "") or None,
                client_order_id=client_order_id,
                symbol=symbol,
                side=str(t.get("side") or "").lower(),
                qty=float(t.get("amount") or 0.0),
                price=float(t.get("price") or 0.0),
                cost=float(t.get("cost") or 0.0),
                fee_cost=float(fee.get("cost") or 0.0),
                fee_currency=fee.get("currency"),
                timestamp_ms=int(t.get("timestamp") or self.now_ms()),
                realized_pnl_quote=0.0,
                exit_reason=_exit_reason_val,
            )
            inserted += 1

        # P0.5: daily pnl güvenilir olsun diye symbol fills zincirini her sync sonrası
        # realized_pnl_quote açısından tekrar yürütüp backfill ediyoruz.
        self._refresh_fill_realized_pnl(symbol)

        logging.info(f"[RECON] {symbol} fetched_trade_count={len(trades)} inserted_real={inserted}")

    # ----------------------------
    # FILLS ACCOUNTING
    # ----------------------------
    def _build_from_fills(self, symbol: str) -> tuple[float, float, float, float]:
        # Bu fonksiyon sadece fills zincirine bakarak teorik muhasebe çıkarır:
        # elimde kaç coin olmalı, ortalama maliyet ne, realize pnl ne kadar?
        fills = self.fills_repo.by_symbol(symbol)
        base_asset, quote_asset = symbol.split("/")
        fills = sorted(fills, key=lambda x: (x["timestamp_ms"], x["trade_id"]))

        anchor = self._load_reconcile_anchor(symbol)
        qty = float((anchor or {}).get("qty") or 0.0)
        avg_entry = float((anchor or {}).get("avg_entry_price") or 0.0)
        realized = float((anchor or {}).get("realized_pnl_quote") or 0.0)
        fees_quote = float((anchor or {}).get("fees_quote") or 0.0)

        anchor_ts = int((anchor or {}).get("last_fill_ts_ms") or 0)
        anchor_trade_id = str((anchor or {}).get("last_trade_id") or "")
        anchor_consumed = anchor is None
        if anchor:
            # Anchor varsa muhasebeyi tarihin en başından değil,
            # o güvenli başlangıç noktasından itibaren yürütürüz.
            logging.info(
                "[RECONCILE ANCHOR] %s qty=%s avg=%s cursor_ts=%s trade_id=%s",
                symbol,
                qty,
                avg_entry,
                anchor_ts,
                anchor_trade_id,
            )

        for f in fills:
            if not anchor_consumed:
                fill_ts = int(f["timestamp_ms"] or 0)
                fill_trade_id = str(f["trade_id"] or "")
                if fill_ts < anchor_ts:
                    continue
                if fill_ts == anchor_ts:
                    if anchor_trade_id and fill_trade_id == anchor_trade_id:
                        anchor_consumed = True
                    continue
                anchor_consumed = True

            side = str(f["side"]).lower()
            fill_qty = float(f["qty"] or 0.0)
            fill_price = float(f["price"] or 0.0)
            fill_cost = float(f["cost"] or 0.0)
            fee_cost = float(f["fee_cost"] or 0.0)
            fee_ccy = f["fee_currency"]

            if fill_qty <= 1e-12:
                continue

            qty, avg_entry, realized_pnl_quote, fee_quote_equivalent = self._apply_fill_accounting(
                side=side,
                fill_qty=fill_qty,
                fill_price=fill_price,
                fill_cost=fill_cost,
                fee_cost=fee_cost,
                fee_ccy=fee_ccy,
                base_asset=base_asset,
                quote_asset=quote_asset,
                current_qty=qty,
                current_avg=avg_entry,
            )

            realized += realized_pnl_quote
            fees_quote += fee_quote_equivalent

        return qty, avg_entry, realized, fees_quote

    def _get_exchange_cost_price(self, symbol: str) -> tuple[float, float]:
        # Şimdilik exchange tarafında cost price okumuyoruz.
        # İleride borsa API'sinden gerçek maliyet okunursa burası doldurulabilir.
        return 0.0, 0.0

    def _safe_last_price(self, symbol: str, fills_avg: float, existing_avg: float) -> float:
        """
        Qty'yi quote değerine çevirmek için yaklaşık bir referans fiyat al.

        Öncelik:
        1) live ticker last
        2) fills avg
        3) existing avg
        4) 0

        Amaç burada mükemmel fiyat bulmak değil.
        Amaç, 0.0000007 BNB gibi mikroskobik kalıntıları gerçek pozisyon sanmamaktır.
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            last = float(ticker.get("last") or 0.0)
            if last > 0:
                return last
        except Exception:
            pass

        if fills_avg > 0:
            return fills_avg
        if existing_avg > 0:
            return existing_avg
        return 0.0

    def _value(self, qty: float, ref_price: float) -> float:
        # Basit yardımcı: qty * fiyat = yaklaşık USDT değeri.
        if qty <= 0 or ref_price <= 0:
            return 0.0
        return qty * ref_price

    def _meaningful_mismatch(self, fills_qty: float, base_total: float, ref_price: float, warn_abs_quote: float, warn_ratio: float) -> bool:
        """
        Her fills/balance farkını warning'e çevirmek log kirliliği üretir.

        Mismatch warning sadece iki şart birlikte anlamlıysa basılmalı:
        - farkın USDT karşılığı anlamlı büyüklükte olmalı
        - fark oranı da dikkate değer olmalı
        """
        diff_qty = abs(fills_qty - base_total)
        if diff_qty <= 0:
            return False

        diff_value = self._value(diff_qty, ref_price)
        if diff_value < warn_abs_quote:
            return False

        denominator = max(abs(fills_qty), abs(base_total), 1e-12)
        ratio = diff_qty / denominator
        return ratio >= warn_ratio

    # ----------------------------
    # POSITION BUILD
    # ----------------------------
    def _rebuild_position(self, symbol: str) -> None:
        # Bütün uzlaşmanın kalbi burada.
        # Fills, balance ve eski position state'i birlikte okunur.
        # Sonunda "OPEN mi CLOSED mu, qty kaç, avg kaç?" kararı verilir.
        settings = self._settings()
        epsilon = float(getattr(settings, "reconcile_epsilon", 1e-12) or 1e-12) if settings else 1e-12
        position_source_mode = str(getattr(settings, "position_source_mode", "hybrid") or "hybrid").lower() if settings else "hybrid"
        allow_balance_only_positions = bool(getattr(settings, "allow_balance_only_positions", True)) if settings else True
        sandbox_ignore_balance_zero = bool(getattr(settings, "sandbox_ignore_balance_zero", True)) if settings else True
        live_force_close_on_zero_balance = bool(getattr(settings, "live_force_close_on_zero_balance", True)) if settings else True
        preserve_on_balance_error = bool(getattr(settings, "preserve_position_on_balance_fetch_error", True)) if settings else True
        rebase_after_mismatch_count = int(getattr(settings, "reconcile_rebase_after_mismatch_count", 3) or 3) if settings else 3
        min_position_value_usdt = float(getattr(settings, "min_position_value_usdt", 5.0) or 5.0) if settings else 5.0
        warn_abs_quote = float(getattr(settings, "reconcile_warn_abs_quote_usdt", 5.0) or 5.0) if settings else 5.0
        warn_ratio = float(getattr(settings, "reconcile_warn_ratio", 0.10) or 0.10) if settings else 0.10
        soft_zero_multiplier = float(getattr(settings, "reconcile_soft_zero_multiplier", 3.0) or 3.0) if settings else 3.0

        base_asset, quote_asset = symbol.split("/")
        fills_qty, fills_avg, realized, fees_quote = self._build_from_fills(symbol)
        existing = self.positions_repo.get(symbol)
        existing_avg = float((existing or {}).get("avg_entry_price") or 0.0)
        existing_qty = float((existing or {}).get("qty") or 0.0)

        balance = None
        balance_error = None
        try:
            balance = self.exchange.fetch_balance()
            base_total = float(self.exchange.get_asset_total(base_asset, balance=balance))
            base_free = float(self.exchange.get_asset_free(base_asset, balance=balance))
        except Exception as exc:
            # Balance çekilemezse bu önemli bir olaydır.
            # Ama tek başına "pozisyon sıfır" demek değildir.
            balance_error = exc
            logging.warning(f"[BALANCE FETCH ERROR] {symbol} err={exc}")
            base_total = None
            base_free = None

        min_amount = max(float(self.exchange.min_amount(symbol) or 0.0), epsilon)
        soft_zero_qty = max(min_amount * soft_zero_multiplier, epsilon)
        sandbox_mode = bool(getattr(getattr(self.exchange, "ex", None), "sandbox", False))

        ref_price = self._safe_last_price(symbol, fills_avg, existing_avg)
        fills_value = self._value(fills_qty, ref_price)
        existing_value = self._value(existing_qty, ref_price)
        base_value = self._value(base_total or 0.0, ref_price)

        final_qty = 0.0
        final_avg = 0.0

        if position_source_mode == "fills":
            # Eski/özel mod: pozisyon qty'yi sadece fills'ten türet.
            final_qty = fills_qty if fills_value >= min_position_value_usdt else 0.0
            final_avg = fills_avg if final_qty > 0 else 0.0

        elif position_source_mode == "balance":
            # En katı mod: qty sadece cüzdan balance'ından gelsin.
            if base_total is not None and base_total > soft_zero_qty and base_value >= min_position_value_usdt:
                final_qty = base_total
                final_avg = fills_avg if fills_avg > 0 else existing_avg
            else:
                final_qty = 0.0
                final_avg = 0.0

        else:
            # HYBRID MODE
            # En güvenilir qty kaynağı balance'tır ama şu kuralla:
            # "gerçek pozisyon" sayılacak kadar anlamlı olmalı.
            if base_total is None:
                # Balance fetch patladıysa position'ı zorla sıfırlamak kötü tasarımdır.
                # Çünkü network hatası yüzünden bot kendi state'ini çöpe atmış olur.
                if preserve_on_balance_error:
                    if fills_value >= min_position_value_usdt:
                        final_qty = fills_qty
                        final_avg = fills_avg
                        logging.warning(
                            f"[BALANCE FETCH PRESERVE FILLS] {symbol} fills_qty={fills_qty} fills_value={fills_value:.4f}"
                        )
                    elif existing_value >= min_position_value_usdt and existing_avg > 0:
                        final_qty = existing_qty
                        final_avg = existing_avg
                        logging.warning(
                            f"[BALANCE FETCH PRESERVE EXISTING] {symbol} existing_qty={existing_qty} existing_value={existing_value:.4f}"
                        )
                    else:
                        final_qty = 0.0
                        final_avg = 0.0
                else:
                    final_qty = 0.0
                    final_avg = 0.0

            elif base_total > soft_zero_qty and base_value >= min_position_value_usdt:
                # Açık pozisyon miktarı için ana doğru kaynak balance.
                # Yani "elimde kaç coin var?" sorusunun cevabı fills değil balance.
                final_qty = base_total

                # Avg önceliği:
                # 1) fills'tan gelen gerçek avg
                # 2) exchange trades'tan hesaplanan avg (DB reset sonrası fills boşsa)
                # 3) eski position avg
                if fills_avg > 0:
                    final_avg = fills_avg
                    if self._meaningful_mismatch(fills_qty, base_total, ref_price, warn_abs_quote, warn_ratio):
                        mismatch_count = self._register_mismatch(symbol)
                        if mismatch_count <= 3 or mismatch_count % 10 == 0:
                            logging.warning(
                                f"[POSITION MISMATCH] {symbol} fills_qty={fills_qty} base_total={base_total} base_free={base_free} fills_value={fills_value:.4f} base_value={base_value:.4f} count={mismatch_count}"
                            )
                        # Mismatch olsa bile qty'yi fills'ten üretmiyoruz.
                        # Pozisyon miktarı balance'tan geliyor; fills sadece maliyet/PnL için kalıyor.
                        # We specifically DO NOT set reconcile anchor here anymore.
                        # Position qty follows balance, fills continues strictly as realized PnL / entry basis source.
                    else:
                        self._clear_mismatch(symbol)
                else:
                    # Fills yok ama balance var senaryosu.
                    # DB reset olmuş olabilir; yine de açık pozisyonu kaybetmek istemeyiz.
                    _, final_avg = self._get_exchange_cost_price(symbol)
                    if final_avg <= 0:
                        final_avg = existing_avg
                    if allow_balance_only_positions and final_avg > 0:
                        logging.warning(
                            f"[BALANCE ONLY POSITION] {symbol} fills_qty={fills_qty} base_total={base_total} base_free={base_free} base_value={base_value:.4f} exchange_avg={final_avg:.6f}"
                        )
                    self._clear_mismatch(symbol)

            else:
                # Wallet'ta ya hiç coin yok ya da sadece dust var.
                if base_total is not None and base_total > epsilon and base_total <= soft_zero_qty:
                    logging.info(
                        f"[POSITION DUST CLOSED] {symbol} base_total={base_total} base_free={base_free} base_value={base_value:.8f}"
                    )

                if fills_value >= min_position_value_usdt:
                    if sandbox_mode and sandbox_ignore_balance_zero:
                        # Sandbox bazen saçma şekilde zero balance döndürebilir.
                        # O yüzden test ortamında daha yumuşak davranıyoruz.
                        final_qty = fills_qty
                        _, final_avg = self._get_exchange_cost_price(symbol)
                        if final_avg <= 0:
                            final_avg = fills_avg
                        logging.warning(
                            f"[SANDBOX BALANCE ZERO IGNORED] {symbol} fills_qty={fills_qty} fills_value={fills_value:.4f} base_total={base_total} base_free={base_free}"
                        )
                    elif (not sandbox_mode) and live_force_close_on_zero_balance:
                        # Canlıda balance yoksa pozisyon da yok kabul ediyoruz.
                        # Fills tarafı geriden gelse bile burada kirli açık pozisyon üretmiyoruz.
                        final_qty = 0.0
                        final_avg = 0.0
                        mismatch_count = self._register_mismatch(symbol)
                        logging.warning(
                            f"[POSITION FORCE CLOSE] {symbol} fills_qty={fills_qty} fills_value={fills_value:.4f} base_total={base_total} base_free={base_free} count={mismatch_count}"
                        )
                        # We specifically DO NOT set reconcile anchor here anymore.
                        # Setting the anchor causes fills history drift and duplicate entries.
                        self._clear_mismatch(symbol)
                    else:
                        # Canlıda sıfıra zorlamıyorsak geçici olarak fills muhasebesini koruyoruz.
                        final_qty = fills_qty
                        _, final_avg = self._get_exchange_cost_price(symbol)
                        if final_avg <= 0:
                            final_avg = fills_avg
                else:
                    # Hem balance hem fills tarafı sadece kırıntıysa CLOSED kabul ediyoruz.
                    final_qty = 0.0
                    final_avg = 0.0
                    self._clear_mismatch(symbol)

        if ref_price > 0 and self._value(final_qty, ref_price) < min_position_value_usdt:
            # Çok küçük pozisyonları gerçek açık pozisyon saymıyoruz.
            if final_qty > 0:
                logging.info(
                    f"[POSITION VALUE FILTERED] {symbol} qty={final_qty} avg={final_avg} ref_price={ref_price} value={self._value(final_qty, ref_price):.8f} threshold={min_position_value_usdt}"
                )
            final_qty = 0.0
            final_avg = 0.0

        if final_qty <= soft_zero_qty:
            # Mikro toz miktarları 0 kabul edilir.
            final_qty = 0.0
            final_avg = 0.0

        status = "OPEN" if final_qty > 0 else "CLOSED"
        # Son karar burada loglanır ve positions tablosuna yazılır.
        logging.info(f"[POSITION] {symbol} qty={final_qty} avg={final_avg} status={status}")

        self.positions_repo.upsert(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            qty=final_qty,
            avg_entry_price=final_avg,
            realized_pnl_quote=realized,
            fees_quote=fees_quote,
            status=status,
            now_ms=self.now_ms(),
        )
