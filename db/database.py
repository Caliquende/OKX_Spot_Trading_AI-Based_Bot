from __future__ import annotations

"""
SQLITE ALTYAPISI / SQLITE INFRASTRUCTURE
========================================

TR:
Bu dosya SQLite baglantisini, execute/fetch helper'larini ve schema init mantigini tasir.
En kritik gorevlerinden biri bot ilk ayaga kalktiginda gerekli tablolarin varligini garanti etmektir.

Bu dosya su sorular icin ozellikle onemlidir:
- Hangi tablolar var?
- Primary key ve conflict davranisi nasil?
- Yeni state eklenince migration benzeri schema eklemesi nereye yapilir?
- `fills.realized_pnl_quote` gibi sonradan eklenen muhasebe kolonlari nasil eklenir?

P0.5 notu:
`/daily_pnl` komutunun guvenilir calisabilmesi icin fill-level realized pnl saklanmalidir.
Sadece `positions.realized_pnl_quote` toplamı gunluk kirilim icin yetmez.
Bu yuzden `fills` tablosuna `realized_pnl_quote` kolonu eklenir ve eski DB'lere ALTER TABLE ile migration uygulanir.

EN:
This file contains SQLite connection handling, execute/fetch helpers, and schema initialization logic.
One of its most important jobs is to guarantee that required tables exist when the bot starts.

This file matters especially for questions like:
- Which tables exist?
- How do primary keys and conflicts behave?
- Where should schema-extension logic be added when new state is introduced?
- How are later accounting columns such as `fills.realized_pnl_quote` migrated?

P0.5 note:
For `/daily_pnl` to work reliably, realized PnL must be stored at the fill level.
Keeping only cumulative `positions.realized_pnl_quote` is not enough for daily breakdowns.
That is why the `fills` table contains `realized_pnl_quote` and old DBs are upgraded with `ALTER TABLE`.
"""


import sqlite3
from contextlib import contextmanager
from typing import Iterator


class Database:
    """
    SQLite bağlantısını ve schema init'i burada tutuyoruz.
    Amaç: DB'nin oyuncak olmaktan çıkması.

    Çok basit anlatım:
    - Botun hafızası burada durur.
    - Order'lar, fill'ler, pozisyonlar ve küçük state bilgileri burada saklanır.
    - Bot kapanıp açılsa bile geçmişi unutmasın diye bu sınıf vardır.
    """

    def __init__(self, path: str):
        # TR: path = SQLite dosyasinin diskteki adresi. Once schema, sonra pragma ayarlari kurulur.
        # EN: path = on-disk location of the SQLite file. We initialize schema first, then runtime pragmas.
        self.path = path
        self._init_schema()
        self._init_pragmas()

    def _connect(self) -> sqlite3.Connection:
        # TR: Burada gercek SQLite baglantisi acilir.
        # EN: This is where the real SQLite connection is created.
        # TR: timeout=30 ise DB kisa sure kilitliyse hemen patlamak yerine biraz bekler.
        # EN: timeout=30 means we wait a bit instead of failing immediately when the DB is briefly locked.
        # TR: check_same_thread=False farkli thread'lerin ayni DB'yi kullanabilmesini saglar.
        # EN: check_same_thread=False allows different threads to use the same DB.
        con = sqlite3.connect(
            self.path,
            timeout=30,                 # 🔥 lock olursa bekler
            check_same_thread=False     # 🔥 multi-access izin
        )
        # TR: row_factory sayesinde satirlar tuple yerine isimli kolon gibi okunur.
        # EN: row_factory makes rows behave like named-column records instead of plain tuples.
        con.row_factory = sqlite3.Row
        return con

    def _init_pragmas(self) -> None:
        # TR: PRAGMA ayarlari SQLite'in calisma karakterini belirler.
        # EN: PRAGMA settings define the runtime behavior of SQLite.
        # TR: WAL modu eszamanli okuma/yazma durumlarinda cok faydalidir.
        # EN: WAL mode is very helpful for concurrent reads and writes.
        # TR: synchronous=NORMAL performans ve guvenlik arasinda denge kurar.
        # EN: synchronous=NORMAL balances performance and safety.
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL;")      # 🔥 en kritik
            con.execute("PRAGMA synchronous=NORMAL;")    # 🔥 performans + stability
            con.commit()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        # Bu helper "bağlantı aç, iş bitince commit et, sonra kapat" işini tek yere toplar.
        # Böylece her yerde aynı boilerplate kodu yazmıyoruz.
        con = self._connect()
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_schema(self) -> None:
        # Bot ilk açıldığında gerekli tablolar yoksa burada oluşturulur.
        # "IF NOT EXISTS" kullandığımız için tablo varsa tekrar yaratmaya çalışmaz.
        with self.conn() as con:
            cur = con.cursor()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    requested_qty REAL,
                    requested_quote REAL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            # orders tablosu:
            # - Botun gönderdiği order'ları takip eder.
            # - client_order_id bizim tarafın id'sidir.
            # - exchange_order_id borsanın verdiği id'dir.
            # - status order'ın hala açık mı, iptal mi, doldu mu bilgisidir.

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fills (
                    trade_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    client_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    cost REAL,
                    fee_cost REAL NOT NULL DEFAULT 0,
                    fee_currency TEXT,
                    timestamp_ms INTEGER NOT NULL,
                    realized_pnl_quote REAL NOT NULL DEFAULT 0,
                    exit_reason TEXT
                )
                """
            )
            # fills tablosu:
            # - Borsada gerçekten olmuş trade kayıtları buraya düşer.
            # - Pozisyon muhasebesi için en önemli geçmiş veri kaynağı burasıdır.
            # - realized_pnl_quote satılan her trade'in kar/zararını tutar.

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    base_asset TEXT NOT NULL,
                    quote_asset TEXT NOT NULL,
                    qty REAL NOT NULL DEFAULT 0,
                    avg_entry_price REAL NOT NULL DEFAULT 0,
                    realized_pnl_quote REAL NOT NULL DEFAULT 0,
                    fees_quote REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'CLOSED',
                    updated_at_ms INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # positions tablosu:
            # - Her symbol için botun "şu an açık mı kapalı mı" görüşünü tutar.
            # - qty ve avg_entry_price burada özet halde saklanır.
            # - Bu tablo geçmişin tamamı değil, o anki özet state'tir.

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_locks (
                    symbol TEXT PRIMARY KEY,
                    locked_until_ms INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    updated_at_ms INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # symbol_locks tablosu:
            # - Aynı sembolde bot peş peşe order göndermesin diye geçici kilit tutar.
            # - Özellikle exit sonrası kısa süre sakin kalmak için kullanılır.

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            # bot_state tablosu:
            # - Küçük ama önemli anahtar/değer state'leri burada durur.
            # - Örn: streak, regime, reconcile anchor, pending exit reason gibi şeyler.

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_started_ms INTEGER NOT NULL,
                    cycle_finished_ms INTEGER,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            # cycle_reports tablosu:
            # - Her trading cycle sonunda kısa özet saklamak için kullanılır.
            # - Sonradan "bot o cycle ne yaptı?" diye bakmak kolay olur.


            # P0.5 /daily_pnl düzeltmesi:
            #
            # fills tablosunda realized_pnl_quote kolonu yoksa günlük realized pnl
            # güvenilir biçimde hesaplanamaz. positions tablosundaki realized_pnl_quote
            # kümülatif toplamdır; günlük kırılım sağlamaz.
            #
            # Bu yüzden CREATE TABLE sonrası ALTER TABLE tabanlı hafif migration uyguluyoruz.
            # Mevcut DB bozulmadan yeni kolon eklenir.
            self._ensure_column(
                con,
                table_name="fills",
                column_name="realized_pnl_quote",
                column_sql="REAL NOT NULL DEFAULT 0",
            )
            # Eski DB'de bu kolon yoksa eklenir.
            # Varsa hiçbir şey yapmadan geçer.
            self._ensure_column(
                con,
                table_name="fills",
                column_name="exit_reason",
                column_sql="TEXT",
            )
            self._ensure_column(
                con,
                table_name="orders",
                column_name="ghost_cleaned_at_ms",
                column_sql="INTEGER",
            )
            self._ensure_column(
                con,
                table_name="orders",
                column_name="ghost_cleanup_reason",
                column_sql="TEXT",
            )
            # Bu kolonlar ghost cleanup tekrarlarını engellemek için var.

    def _table_columns(self, con: sqlite3.Connection, table_name: str) -> set[str]:
        # PRAGMA table_info ile tablodaki kolonları okuyoruz.
        # Böylece "kolon var mı yok mu" güvenli biçimde anlayabiliyoruz.
        rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def _ensure_column(
        self,
        con: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        """
        Minimal migration helper.

        Neden var?
        - Bot artık oyuncak değil; schema evrimi kaçınılmaz.
        - Ayrı bir migration framework kurmadan küçük kolon eklemeleri yapmak
          için güvenli bir ara çözüme ihtiyacımız var.
        - /daily_pnl bug fix bu helper olmadan eski DB'lerde hemen patlar.
        """
        columns = self._table_columns(con, table_name)
        if column_name in columns:
            # Kolon zaten varsa sessizce çık.
            return
        # Kolon yoksa canlı DB'yi bozmadan ALTER TABLE ile ekle.
        con.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def fetchone(self, sql: str, params: tuple = ()):
        # Tek satır beklediğimiz sorgular için helper.
        # Sonuç yoksa None döner.
        with self.conn() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()):
        # Çok satırlı sorgular için helper.
        # Sonucu dict listesi olarak döndürür.
        with self.conn() as con:
            rows = con.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def execute(self, sql: str, params: tuple = ()) -> None:
        # INSERT / UPDATE / DELETE gibi yazan sorgular için helper.
        with self.conn() as con:
            con.execute(sql, params)
