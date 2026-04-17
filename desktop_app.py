from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any


APP_TITLE = "OKX Bot Control Tower"
APP_MIN_SIZE = (1180, 760)

COLORS = {
    "bg": "#081017",
    "panel": "#111d25",
    "panel_soft": "#172832",
    "line": "#29404c",
    "text": "#eef7f3",
    "muted": "#93a8a9",
    "faint": "#62787b",
    "cyan": "#69d7ff",
    "amber": "#f6b84a",
    "green": "#7de08d",
    "red": "#ff6868",
    "blue": "#8ba7ff",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def format_ms(value: Any) -> str:
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")


def format_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def format_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f} USDT"
    except (TypeError, ValueError):
        return "-"


def short_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def bool_text(value: Any) -> str:
    return "ON" if bool(value) else "OFF"


def bool_color(value: Any) -> str:
    return COLORS["red"] if bool(value) else COLORS["green"]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_db_path(cli_path: str | None = None) -> Path:
    root = Path(__file__).resolve().parent
    load_env_file(root / ".env")
    raw = cli_path or os.getenv("DB_PATH") or os.getenv("SQLITE_PATH") or "trading_bot.db"
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def configured_symbols_from_env() -> list[str]:
    raw = os.getenv("SYMBOLS", "")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


class BotDataStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.configured_symbols = configured_symbols_from_env()
        self.configured_symbol_set = set(self.configured_symbols)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def is_available(self) -> tuple[bool, str]:
        if not self.db_path.exists():
            return False, f"DB not found: {self.db_path}"
        try:
            with self._connect() as con:
                con.execute("SELECT 1").fetchone()
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._connect() as con:
            con.execute(sql, params)
            con.commit()

    def get_bot_state(self, key: str) -> Any:
        row = self.fetch_one("SELECT value FROM bot_state WHERE key = ?", (key,))
        if not row:
            return None
        raw = row.get("value")
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def set_bot_state(self, key: str, value: Any) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.execute(
            """
            INSERT INTO bot_state(key, value, updated_at_ms)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at_ms=excluded.updated_at_ms
            """,
            (key, raw, now_ms()),
        )

    def latest_cycle(self) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT *
            FROM cycle_reports
            ORDER BY cycle_started_ms DESC, id DESC
            LIMIT 1
            """
        )

    def recent_cycles(self, limit: int = 60) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, cycle_started_ms, cycle_finished_ms, status, summary
            FROM cycle_reports
            ORDER BY cycle_started_ms DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )

    def open_positions(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT *
            FROM positions
            WHERE status = 'OPEN' AND qty > 0
            ORDER BY symbol ASC
            """
        )

    def all_positions(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT *
            FROM positions
            ORDER BY status DESC, symbol ASC
            """
        )

    def positions(self) -> list[dict[str, Any]]:
        rows = self.all_positions()
        if not self.configured_symbol_set:
            return rows
        visible: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            is_open = str(row.get("status") or "").upper() == "OPEN" and float(row.get("qty") or 0.0) > 0
            if symbol in self.configured_symbol_set or is_open:
                visible.append(row)
        return visible

    def stale_unconfigured_positions(self) -> list[dict[str, Any]]:
        if not self.configured_symbol_set:
            return []
        stale: list[dict[str, Any]] = []
        for row in self.all_positions():
            symbol = str(row.get("symbol") or "").upper()
            is_open = str(row.get("status") or "").upper() == "OPEN" and float(row.get("qty") or 0.0) > 0
            if symbol not in self.configured_symbol_set and not is_open:
                stale.append(row)
        return stale

    def recent_orders(self, limit: int = 80) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT *
            FROM orders
            ORDER BY created_at_ms DESC
            LIMIT ?
            """,
            (limit,),
        )

    def recent_fills(self, limit: int = 80) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT *
            FROM fills
            ORDER BY timestamp_ms DESC, trade_id DESC
            LIMIT ?
            """,
            (limit,),
        )

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in ("positions", "orders", "fills", "cycle_reports", "bot_state", "symbol_locks"):
            row = self.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
            result[table] = int((row or {}).get("count") or 0)
        return result

    def control_state(self) -> dict[str, Any]:
        auto = self.get_bot_state("auto_risk_guard") or {}
        return {
            "panic_mode": bool(self.get_bot_state("panic_mode")),
            "trading_paused": bool(self.get_bot_state("trading_paused")),
            "manual_cycle_requested": bool(self.get_bot_state("manual_cycle_requested")),
            "force_refresh_groq_requested": bool(self.get_bot_state("force_refresh_groq_requested")),
            "force_refresh_thresholds_requested": bool(self.get_bot_state("force_refresh_thresholds_requested")),
            "auto_risk_guard_active": bool(auto.get("active")) if isinstance(auto, dict) else False,
            "auto_risk_guard_reason": auto.get("reason", "") if isinstance(auto, dict) else "",
        }

    def safe_state_rows(self) -> list[tuple[str, str]]:
        keys = [
            "panic_mode",
            "trading_paused",
            "manual_cycle_requested",
            "force_refresh_groq_requested",
            "force_refresh_thresholds_requested",
            "ai_thresholds",
            "auto_risk_guard",
        ]
        rows: list[tuple[str, str]] = []
        for key in keys:
            value = self.get_bot_state(key)
            if isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False, indent=2)
            else:
                text = str(value)
            rows.append((key, short_text(text, 360)))
        return rows


def parse_latest_cycle(summary: str) -> dict[str, Any]:
    return {
        "severity": parse_field(summary, r"Severity:\s*([^\n]+)") or parse_field(summary, r"severity=([^\s|]+)") or "UNKNOWN",
        "total_balance": parse_field(summary, r"Total:\s*([0-9.,]+)\s*USDT") or parse_field(summary, r"total_balance=([0-9.,]+)"),
        "cash_balance": parse_field(summary, r"Cash:\s*([0-9.,]+)\s*USDT") or parse_field(summary, r"cash=([0-9.,]+)"),
        "cash_ratio": parse_field(summary, r"Cash ratio:\s*([0-9.]+)") or parse_field(summary, r"cash_ratio=([0-9.]+)"),
        "symbols": parse_symbol_decisions(summary),
    }


def parse_field(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def parse_symbol_decisions(summary: str) -> list[dict[str, str]]:
    if not summary:
        return []
    parsed_new = parse_new_cycle_symbols(summary)
    if parsed_new:
        return parsed_new
    return parse_legacy_cycle_symbols(summary)


def parse_new_cycle_symbols(summary: str) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    symbol_re = re.compile(r"^([A-Z0-9]+/[A-Z0-9]+)(?:\s+\[AI\])?$")

    for raw_line in summary.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        match = symbol_re.match(stripped)
        if match:
            if current:
                symbols.append(current)
            current = {"symbol": match.group(1)}
            continue
        if not current:
            continue
        if stripped.startswith("Signal:"):
            current["action"] = parse_field(stripped, r"Signal:\s*([^|]+)") or ""
            current["stance"] = parse_field(stripped, r"stance\s+([^|]+)") or ""
            current["total"] = parse_field(stripped, r"total\s+([^|]+)") or ""
            current["streak"] = parse_field(stripped, r"streak\s+([^|]+)") or ""
        elif stripped.startswith("Regime:"):
            current["regime"] = stripped.replace("Regime:", "", 1).strip()
        elif stripped.startswith("Position:"):
            current["position"] = stripped.replace("Position:", "", 1).strip()
        elif stripped.startswith("Risk/Exec:"):
            current["exec"] = stripped.replace("Risk/Exec:", "", 1).strip()
        elif stripped.startswith("Groq:"):
            current["groq"] = stripped.replace("Groq:", "", 1).strip()
    if current:
        symbols.append(current)
    return symbols


def parse_legacy_cycle_symbols(summary: str) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    for raw_line in summary.splitlines():
        if "/" not in raw_line or "|" not in raw_line:
            continue
        parts = [part.strip() for part in raw_line.split("|")]
        head = parts[0].replace("[AI_THRESHOLDS]", "").strip()
        if not re.match(r"^[A-Z0-9]+/[A-Z0-9]+", head):
            continue
        item: dict[str, str] = {"symbol": head.split()[0]}
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            item[key.strip()] = value.strip()
        symbols.append(item)
    return symbols


class ScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)


class BotDesktopApp(tk.Tk):
    def __init__(self, store: BotDataStore):
        super().__init__()
        self.store = store
        self.active_screen = "overview"
        self.auto_refresh = tk.BooleanVar(value=True)
        self.status_text = tk.StringVar(value="Starting...")
        self.last_refresh_text = tk.StringVar(value="-")
        self.banner_text = tk.StringVar(value="Loading bot state")
        self.banner_detail = tk.StringVar(value="SQLite snapshot okunuyor.")

        self.title(APP_TITLE)
        self.minsize(*APP_MIN_SIZE)
        self.configure(bg=COLORS["bg"])

        self._configure_styles()
        self._build_shell()
        self.show_screen("overview")
        self.refresh()
        self.after(15000, self._auto_refresh_tick)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Soft.TFrame", background=COLORS["panel_soft"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI Semibold", 24))
        style.configure("Section.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI Semibold", 14))
        style.configure("Metric.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Cascadia Code", 17, "bold"))
        style.configure("Rail.TFrame", background="#060b10")
        style.configure("Rail.TButton", anchor="w", padding=(14, 12), background="#060b10", foreground=COLORS["muted"], borderwidth=0)
        style.map("Rail.TButton", background=[("active", COLORS["panel_soft"])], foreground=[("active", COLORS["text"])])
        style.configure("Accent.TButton", padding=(14, 10), background=COLORS["cyan"], foreground="#061015", borderwidth=0)
        style.configure("Warn.TButton", padding=(14, 10), background=COLORS["amber"], foreground="#061015", borderwidth=0)
        style.configure("Danger.TButton", padding=(14, 10), background=COLORS["red"], foreground="#061015", borderwidth=0)
        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"], foreground=COLORS["text"], rowheight=32, borderwidth=0)
        style.configure("Treeview.Heading", background=COLORS["panel_soft"], foreground=COLORS["text"], font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#244a57")])

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        rail = ttk.Frame(self, width=238, style="Rail.TFrame")
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)

        brand = tk.Label(
            rail,
            text="OKX\nCONTROL TOWER",
            bg="#060b10",
            fg=COLORS["cyan"],
            justify="left",
            font=("Segoe UI Semibold", 18),
        )
        brand.pack(anchor="w", padx=22, pady=(24, 6))

        subtitle = tk.Label(
            rail,
            text="Desktop bot cockpit",
            bg="#060b10",
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        )
        subtitle.pack(anchor="w", padx=22, pady=(0, 24))

        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in (
            ("overview", "Overview"),
            ("signals", "Signals"),
            ("positions", "Positions"),
            ("orders", "Orders & Fills"),
            ("cycles", "Cycle Reports"),
            ("controls", "Risk Controls"),
            ("settings", "Settings Snapshot"),
        ):
            button = ttk.Button(rail, text=label, style="Rail.TButton", command=lambda k=key: self.show_screen(k))
            button.pack(fill="x", padx=14, pady=4)
            self.nav_buttons[key] = button

        footer = tk.Label(
            rail,
            textvariable=self.status_text,
            bg="#060b10",
            fg=COLORS["muted"],
            justify="left",
            wraplength=190,
            font=("Segoe UI", 9),
        )
        footer.pack(side="bottom", anchor="w", padx=22, pady=24)

        main = ttk.Frame(self)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.main = main

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Bot şu an ne halt ediyor?", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.last_refresh_text, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Refresh", style="Accent.TButton", command=self.refresh).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        ttk.Checkbutton(header, text="Auto refresh", variable=self.auto_refresh).grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))

        self.content = ttk.Frame(main)
        self.content.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 24))
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

    def show_screen(self, key: str) -> None:
        self.active_screen = key
        for child in self.content.winfo_children():
            child.destroy()
        builders = {
            "overview": self._build_overview,
            "signals": self._build_signals,
            "positions": self._build_positions,
            "orders": self._build_orders,
            "cycles": self._build_cycles,
            "controls": self._build_controls,
            "settings": self._build_settings,
        }
        builders.get(key, self._build_overview)()
        self.refresh()

    def refresh(self) -> None:
        ok, error = self.store.is_available()
        if not ok:
            self.status_text.set(error)
            self.banner_text.set("DB unavailable")
            self.banner_detail.set(error)
            return
        self.last_refresh_text.set(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
        self.status_text.set(f"DB: {self.store.db_path}")
        if self.active_screen == "overview":
            self._render_overview()
        elif self.active_screen == "signals":
            self._render_signals()
        elif self.active_screen == "positions":
            self._render_positions()
        elif self.active_screen == "orders":
            self._render_orders()
        elif self.active_screen == "cycles":
            self._render_cycles()
        elif self.active_screen == "controls":
            self._render_controls()
        elif self.active_screen == "settings":
            self._render_settings()

    def _auto_refresh_tick(self) -> None:
        if self.auto_refresh.get():
            self.refresh()
        self.after(15000, self._auto_refresh_tick)

    def _panel(self, parent: tk.Widget, row: int, column: int, title: str, subtitle: str = "", columnspan: int = 1) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=18)
        frame.grid(row=row, column=column, sticky="nsew", padx=8, pady=8, columnspan=columnspan)
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(frame, text=subtitle, style="PanelMuted.TLabel", wraplength=520).pack(anchor="w", pady=(4, 12))
        return frame

    def _metric_card(self, parent: tk.Widget, title: str, value: str, hint: str = "", color: str | None = None) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        ttk.Label(frame, text=title, style="PanelMuted.TLabel").pack(anchor="w")
        label = tk.Label(
            frame,
            text=value,
            bg=COLORS["panel"],
            fg=color or COLORS["text"],
            font=("Cascadia Code", 17, "bold"),
        )
        label.pack(anchor="w", pady=(8, 2))
        if hint:
            tk.Label(frame, text=hint, bg=COLORS["panel"], fg=COLORS["faint"], font=("Segoe UI", 9)).pack(anchor="w")
        return frame

    def _make_tree(self, parent: tk.Widget, columns: tuple[str, ...], headings: tuple[str, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col, heading in zip(columns, headings):
            tree.heading(col, text=heading)
            tree.column(col, minwidth=80, width=130, stretch=True)
        tree.pack(fill="both", expand=True)
        return tree

    def _build_overview(self) -> None:
        frame = ttk.Frame(self.content)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        self.overview_banner = ttk.Frame(frame, style="Panel.TFrame", padding=18)
        self.overview_banner.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        tk.Label(
            self.overview_banner,
            textvariable=self.banner_text,
            bg=COLORS["panel"],
            fg=COLORS["amber"],
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w")
        tk.Label(
            self.overview_banner,
            textvariable=self.banner_detail,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self.metric_grid = ttk.Frame(frame)
        self.metric_grid.grid(row=1, column=0, columnspan=2, sticky="ew")
        for col in range(4):
            self.metric_grid.columnconfigure(col, weight=1)

        self.overview_left = self._panel(frame, 2, 0, "Son cycle kararları", "Aksiyon, skor ve sebep tek yerde.")
        self.overview_right = self._panel(frame, 2, 1, "Kontrol özeti", "Bot state ve güvenlik bayrakları.")

    def _render_overview(self) -> None:
        latest = self.store.latest_cycle()
        summary = str((latest or {}).get("summary") or "")
        cycle = parse_latest_cycle(summary)
        control = self.store.control_state()
        positions = self.store.open_positions()

        severity = str(cycle.get("severity") or "UNKNOWN").upper()
        if control["panic_mode"]:
            self.banner_text.set("PANIC MODE açık")
            self.banner_detail.set("Bot yeni entry açmamalı. Kontrollerden resume/clear panic gerekebilir.")
        elif control["trading_paused"]:
            self.banner_text.set("Trading paused")
            self.banner_detail.set("Bot izleyebilir ama yeni entry tarafı kapalı.")
        elif severity == "DEGRADED":
            self.banner_text.set("DEGRADED ama çekirdek state okunuyor")
            self.banner_detail.set("Son cycle degraded görünüyor. Private API ve DB durumunu cycle detayından kontrol et.")
        elif severity == "OK":
            self.banner_text.set("Bot sağlıklı görünüyor")
            self.banner_detail.set("Son health check OK. Yine de trade kararlarını Signals ekranından oku.")
        else:
            self.banner_text.set("Son durum net değil")
            self.banner_detail.set("Cycle summary parse edilemedi veya henüz cycle yok.")

        for child in self.metric_grid.winfo_children():
            child.destroy()
        metrics = [
            ("Portfolio", format_money(cycle.get("total_balance")), "Son cycle toplamı", COLORS["text"]),
            ("Cash", format_money(cycle.get("cash_balance")), f"Cash ratio: {cycle.get('cash_ratio') or '-'}", COLORS["cyan"]),
            ("Open positions", str(len(positions)), ", ".join(p["symbol"] for p in positions[:4]) or "none", COLORS["green"]),
            ("AI thresholds", self._threshold_summary(), "bot_state: ai_thresholds", COLORS["amber"]),
        ]
        for idx, (title, value, hint, color) in enumerate(metrics):
            self._metric_card(self.metric_grid, title, value, hint, color).grid(row=0, column=idx, sticky="ew", padx=8, pady=8)

        self._clear_panel_body(self.overview_left)
        rows = cycle.get("symbols") or []
        if not rows:
            ttk.Label(self.overview_left, text="Henüz parse edilebilir cycle kararı yok.", style="PanelMuted.TLabel").pack(anchor="w", pady=12)
        else:
            columns = ("symbol", "action", "total", "regime", "exec", "groq")
            tree = self._make_tree(self.overview_left, columns, ("Symbol", "Action", "Total", "Regime", "Exec", "Groq"))
            for row in rows[:12]:
                tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("symbol", "-"),
                        row.get("action", "-"),
                        row.get("total", "-"),
                        row.get("regime", "-"),
                        short_text(row.get("exec", "-"), 70),
                        short_text(row.get("groq", "-"), 80),
                    ),
                )

        self._clear_panel_body(self.overview_right)
        for label, key in (
            ("Panic mode", "panic_mode"),
            ("Trading paused", "trading_paused"),
            ("Manual cycle requested", "manual_cycle_requested"),
            ("Groq force refresh", "force_refresh_groq_requested"),
            ("Threshold force refresh", "force_refresh_thresholds_requested"),
            ("Auto risk guard", "auto_risk_guard_active"),
        ):
            self._status_row(self.overview_right, label, bool(control.get(key)))

    def _threshold_summary(self) -> str:
        raw = self.store.get_bot_state("ai_thresholds")
        if not isinstance(raw, dict):
            return "-"
        buy = raw.get("buy_threshold", "-")
        sell = raw.get("sell_threshold", "-")
        strong_buy = raw.get("strong_buy_threshold", "-")
        strong_sell = raw.get("strong_sell_threshold", "-")
        return f"B {buy} / S {sell} / SB {strong_buy} / SS {strong_sell}"

    def _clear_panel_body(self, panel: ttk.Frame) -> None:
        children = panel.winfo_children()
        for child in children[2:]:
            child.destroy()

    def _status_row(self, parent: tk.Widget, label: str, active: bool) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=5)
        tk.Label(row, text=label, bg=COLORS["panel"], fg=COLORS["muted"]).pack(side="left")
        tk.Label(
            row,
            text=bool_text(active),
            bg=COLORS["panel"],
            fg=bool_color(active),
            font=("Cascadia Code", 10, "bold"),
        ).pack(side="right")

    def _build_signals(self) -> None:
        frame = self._panel(self.content, 0, 0, "Signals", "AI skor, teknik skor ve final aksiyonun denetim ekranı.")
        self.signals_tree = self._make_tree(
            frame,
            ("symbol", "action", "stance", "total", "regime", "streak", "exec", "groq"),
            ("Symbol", "Action", "Stance", "Total", "Regime", "Streak", "Exec", "Groq"),
        )

    def _render_signals(self) -> None:
        self.signals_tree.delete(*self.signals_tree.get_children())
        latest = self.store.latest_cycle()
        rows = parse_latest_cycle(str((latest or {}).get("summary") or "")).get("symbols") or []
        for row in rows:
            self.signals_tree.insert(
                "",
                "end",
                values=(
                    row.get("symbol", "-"),
                    row.get("action", "-"),
                    row.get("stance", "-"),
                    row.get("total", "-"),
                    row.get("regime", "-"),
                    row.get("streak", "-"),
                    short_text(row.get("exec", "-"), 90),
                    short_text(row.get("groq", "-"), 110),
                ),
            )

    def _build_positions(self) -> None:
        frame = self._panel(self.content, 0, 0, "Positions", "SQLite positions snapshot. Market price yoksa PnL tahmini yapılmaz.")
        self.positions_tree = self._make_tree(
            frame,
            ("symbol", "source", "qty", "avg", "realized", "fees", "status", "updated"),
            ("Symbol", "Source", "Qty", "Avg Entry", "Realized PnL", "Fees", "Status", "Updated"),
        )

    def _render_positions(self) -> None:
        self.positions_tree.delete(*self.positions_tree.get_children())
        for row in self.store.positions():
            symbol = str(row.get("symbol") or "").upper()
            source = "CONFIGURED" if not self.store.configured_symbol_set or symbol in self.store.configured_symbol_set else "UNCONFIGURED OPEN"
            self.positions_tree.insert(
                "",
                "end",
                values=(
                    row.get("symbol", "-"),
                    source,
                    format_float(row.get("qty"), 8),
                    format_float(row.get("avg_entry_price"), 6),
                    format_money(row.get("realized_pnl_quote")),
                    format_money(row.get("fees_quote")),
                    row.get("status", "-"),
                    format_ms(row.get("updated_at_ms")),
                ),
            )

    def _build_orders(self) -> None:
        frame = ttk.Frame(self.content)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        orders = self._panel(frame, 0, 0, "Recent Orders", "Exchange order state and local reconciliation.")
        fills = self._panel(frame, 1, 0, "Recent Fills", "Actual trade history and realized PnL impact.")
        self.orders_tree = self._make_tree(
            orders,
            ("time", "symbol", "side", "status", "qty", "quote", "client_id", "reason"),
            ("Time", "Symbol", "Side", "Status", "Qty", "Quote", "Client ID", "Reason"),
        )
        self.fills_tree = self._make_tree(
            fills,
            ("time", "symbol", "side", "qty", "price", "pnl", "reason"),
            ("Time", "Symbol", "Side", "Qty", "Price", "Realized PnL", "Exit Reason"),
        )

    def _render_orders(self) -> None:
        self.orders_tree.delete(*self.orders_tree.get_children())
        for row in self.store.recent_orders():
            self.orders_tree.insert(
                "",
                "end",
                values=(
                    format_ms(row.get("created_at_ms")),
                    row.get("symbol", "-"),
                    row.get("side", "-"),
                    row.get("status", "-"),
                    format_float(row.get("requested_qty"), 8),
                    format_money(row.get("requested_quote")),
                    short_text(row.get("client_order_id"), 22),
                    short_text(row.get("reason"), 80),
                ),
            )
        self.fills_tree.delete(*self.fills_tree.get_children())
        for row in self.store.recent_fills():
            self.fills_tree.insert(
                "",
                "end",
                values=(
                    format_ms(row.get("timestamp_ms")),
                    row.get("symbol", "-"),
                    row.get("side", "-"),
                    format_float(row.get("qty"), 8),
                    format_float(row.get("price"), 6),
                    format_money(row.get("realized_pnl_quote")),
                    row.get("exit_reason") or "-",
                ),
            )

    def _build_cycles(self) -> None:
        frame = ttk.Frame(self.content)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)
        left = self._panel(frame, 0, 0, "Cycle Reports", "Persisted bot cycle history.")
        right = self._panel(frame, 0, 1, "Raw Summary", "Seçilen cycle'ın Telegram benzeri raw çıktısı.")
        self.cycles_tree = self._make_tree(left, ("id", "started", "finished", "status"), ("ID", "Started", "Finished", "Status"))
        self.cycles_tree.bind("<<TreeviewSelect>>", self._on_cycle_select)
        self.cycle_text = tk.Text(
            right,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            wrap="word",
            font=("Cascadia Code", 9),
        )
        self.cycle_text.pack(fill="both", expand=True)
        self.cycle_by_item: dict[str, str] = {}

    def _render_cycles(self) -> None:
        self.cycles_tree.delete(*self.cycles_tree.get_children())
        self.cycle_by_item.clear()
        for row in self.store.recent_cycles():
            item = self.cycles_tree.insert(
                "",
                "end",
                values=(
                    row.get("id"),
                    format_ms(row.get("cycle_started_ms")),
                    format_ms(row.get("cycle_finished_ms")),
                    row.get("status", "-"),
                ),
            )
            self.cycle_by_item[item] = str(row.get("summary") or "")
        if self.cycles_tree.get_children():
            first = self.cycles_tree.get_children()[0]
            self.cycles_tree.selection_set(first)
            self._show_cycle_text(self.cycle_by_item[first])

    def _on_cycle_select(self, _event: tk.Event) -> None:
        selection = self.cycles_tree.selection()
        if not selection:
            return
        self._show_cycle_text(self.cycle_by_item.get(selection[0], ""))

    def _show_cycle_text(self, text: str) -> None:
        self.cycle_text.configure(state="normal")
        self.cycle_text.delete("1.0", "end")
        self.cycle_text.insert("1.0", text or "No summary")
        self.cycle_text.configure(state="disabled")

    def _build_controls(self) -> None:
        frame = self._panel(self.content, 0, 0, "Risk Controls", "Bu ekran doğrudan emir basmaz; bot_state komutlarını güvenli yazar.")
        self.control_state_frame = ttk.Frame(frame, style="Panel.TFrame")
        self.control_state_frame.pack(fill="x", pady=(6, 18))

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Force Refresh Cycle", style="Accent.TButton", command=self.request_force_refresh).pack(fill="x", pady=6)
        ttk.Button(buttons, text="Pause Trading", style="Warn.TButton", command=self.pause_trading).pack(fill="x", pady=6)
        ttk.Button(buttons, text="Resume Trading", style="Accent.TButton", command=self.resume_trading).pack(fill="x", pady=6)
        ttk.Button(buttons, text="Enable Panic Mode", style="Danger.TButton", command=self.enable_panic).pack(fill="x", pady=6)

    def _render_controls(self) -> None:
        for child in self.control_state_frame.winfo_children():
            child.destroy()
        control = self.store.control_state()
        for label, key in (
            ("Panic mode", "panic_mode"),
            ("Trading paused", "trading_paused"),
            ("Manual cycle requested", "manual_cycle_requested"),
            ("Force Groq refresh", "force_refresh_groq_requested"),
            ("Force threshold refresh", "force_refresh_thresholds_requested"),
            ("Auto risk guard", "auto_risk_guard_active"),
        ):
            self._status_row(self.control_state_frame, label, bool(control.get(key)))
        reason = str(control.get("auto_risk_guard_reason") or "").strip()
        if reason:
            ttk.Label(self.control_state_frame, text=f"Auto risk reason: {reason}", style="PanelMuted.TLabel").pack(anchor="w", pady=(8, 0))

    def request_force_refresh(self) -> None:
        if not messagebox.askyesno("Force refresh", "Groq sentiment, AI thresholds ve manual cycle request yazılsın mı?"):
            return
        self.store.set_bot_state("force_refresh_groq_requested", True)
        self.store.set_bot_state("force_refresh_thresholds_requested", True)
        self.store.set_bot_state("manual_cycle_requested", True)
        self.refresh()

    def pause_trading(self) -> None:
        if not messagebox.askyesno("Pause trading", "Yeni entry kapatılsın mı? Açık pozisyonlar otomatik kapatılmaz."):
            return
        self.store.set_bot_state("trading_paused", True)
        self.refresh()

    def resume_trading(self) -> None:
        if not messagebox.askyesno("Resume trading", "Trading pause ve panic mode kapatılsın mı?"):
            return
        self.store.set_bot_state("trading_paused", False)
        self.store.set_bot_state("panic_mode", False)
        self.refresh()

    def enable_panic(self) -> None:
        answer = simpledialog.askstring("Panic mode", 'Panic mode için "PANIC" yaz.')
        if answer != "PANIC":
            messagebox.showinfo("Canceled", "Panic mode değiştirilmedi.")
            return
        self.store.set_bot_state("panic_mode", True)
        self.store.set_bot_state("trading_paused", True)
        self.refresh()

    def _build_settings(self) -> None:
        frame = self._panel(self.content, 0, 0, "Settings Snapshot", "Secret göstermeyen runtime ve DB özeti.")
        self.settings_text = tk.Text(
            frame,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            wrap="word",
            font=("Cascadia Code", 10),
        )
        self.settings_text.pack(fill="both", expand=True)

    def _render_settings(self) -> None:
        counts = self.store.counts()
        lines = [
            f"DB path: {self.store.db_path}",
            f"Configured symbols: {', '.join(self.store.configured_symbols) or '-'}",
            "",
            "Table counts:",
        ]
        for key, value in counts.items():
            lines.append(f"  {key}: {value}")
        stale = self.store.stale_unconfigured_positions()
        if stale:
            lines += ["", "Hidden stale DB-only position rows:"]
            for row in stale:
                lines.append(
                    "  "
                    f"{row.get('symbol')} "
                    f"status={row.get('status')} "
                    f"qty={format_float(row.get('qty'), 8)} "
                    f"updated={format_ms(row.get('updated_at_ms'))}"
                )
        lines += ["", "Safe bot_state keys:"]
        for key, value in self.store.safe_state_rows():
            lines.append(f"  {key}: {value}")
        self.settings_text.configure(state="normal")
        self.settings_text.delete("1.0", "end")
        self.settings_text.insert("1.0", "\n".join(lines))
        self.settings_text.configure(state="disabled")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OKX Bot Control Tower desktop app")
    parser.add_argument("--db", help="SQLite database path. Defaults to DB_PATH/SQLITE_PATH/.env or trading_bot.db.")
    parser.add_argument("--check", action="store_true", help="Validate DB connectivity and print a snapshot without opening UI.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = BotDataStore(resolve_db_path(args.db))
    ok, error = store.is_available()
    if args.check:
        if not ok:
            raise SystemExit(error)
        latest = store.latest_cycle()
        print(
            json.dumps(
                {
                    "db_path": str(store.db_path),
                    "counts": store.counts(),
                    "latest_cycle_started": (latest or {}).get("cycle_started_ms"),
                    "control": store.control_state(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not ok:
        messagebox.showerror(APP_TITLE, error)
    app = BotDesktopApp(store)
    app.mainloop()


if __name__ == "__main__":
    main()
