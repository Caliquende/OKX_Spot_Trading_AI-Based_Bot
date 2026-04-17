# OKX Bot Control Tower Desktop App

This is a real local desktop application for the bot, not a static mockup.

It uses Python's built-in Tkinter GUI toolkit, so it does not add a new dependency. It reads the bot's SQLite database and shows the latest operational state in a desktop window.

## Run

Double-click:

```text
run_desktop_app.bat
```

Or run from a terminal:

```powershell
python desktop_app.py
```

To validate DB connectivity without opening the UI:

```powershell
python desktop_app.py --check
```

To point the app at a different SQLite file:

```powershell
python desktop_app.py --db trading_bot.db
```

## What It Shows

- Overview: latest health/cycle state, portfolio snapshot, open positions, AI threshold summary, and recent symbol decisions.
- Signals: latest parsed cycle decisions with action, stance, total score, regime, execution note, and Groq summary.
- Positions: current SQLite position snapshot.
- Orders & Fills: recent orders and actual fill history.
- Cycle Reports: persisted cycle list and raw cycle summary.
- Risk Controls: guarded controls that write bot command flags to `bot_state`.
- Settings Snapshot: safe DB and runtime-state summary without showing secrets.

## Control Safety

The app does not directly place exchange orders.

The Risk Controls screen writes the same kind of state flags that the bot already reads:

- Force Refresh Cycle sets `force_refresh_groq_requested`, `force_refresh_thresholds_requested`, and `manual_cycle_requested`.
- Pause Trading sets `trading_paused=true`.
- Resume Trading clears `trading_paused` and `panic_mode`.
- Enable Panic Mode requires typing `PANIC`, then sets `panic_mode=true` and `trading_paused=true`.

Destructive actions such as direct close-all are intentionally not implemented in the first desktop version.

## Symbol Filtering

The Positions screen uses `SYMBOLS` from `.env` as the configured universe.

It shows:

- configured symbols from `.env`,
- any unexpected unconfigured position if it is still `OPEN` and has quantity.

It hides stale unconfigured closed rows from the main Positions screen, because those rows can otherwise look like live pairs. Hidden stale rows are listed in Settings Snapshot under `Hidden stale DB-only position rows`.

## Current Limits

- The app is a first desktop control tower version, not a packaged `.exe` yet.
- It does not fetch live market prices directly; it reads persisted bot state.
- If the bot changes the cycle report text format, the Signals parser may need an update.
- If Python is not on PATH, use the same Python interpreter you use to run the bot.

## Next Packaging Step

If you want a standalone Windows executable later, package it with PyInstaller:

```powershell
pyinstaller --onefile --windowed --name OKXBotControlTower desktop_app.py
```

Do that only after the UI behavior is accepted, because packaging adds another validation surface.
