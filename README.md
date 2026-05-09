# OKX Spot Trading AI-Based Bot

[Turkish README](./README_TR.md)

This repository is a modular Python trading bot for the OKX spot market. It combines technical indicators, market regime detection, deterministic risk and execution controls, TP/SL logic, SQLite-backed state tracking, Telegram notifications, a Tkinter desktop control panel, and an optional AI-assisted research layer.

This project can place live orders. Before using a real account, test with `OKX_SANDBOX=true` and `DRY_RUN=true`.

## Project Goal

Based on the current codebase, the bot is built to:

- scan OKX spot symbols on a fixed cycle
- combine technical score and AI score into a trading decision
- filter entries through risk, cooldown, exposure, and daily loss guards
- persist positions, orders, fills, and cycle state in SQLite
- provide operational visibility through Telegram and the desktop UI

The flow in `main.py` and the settings in `config/settings.py` make it clear that this repository is aimed at controlled testing, iterative development, and strategy tuning rather than a finished production deployment. There is no packaged release flow or deployment automation in the repo.

## Main Components

- Technical analysis: `indicators/indicator_engine.py`
- Regime detection: `strategy/regime_engine.py`
- Signal and score mapping: `strategy/scoring_engine.py`
- Exchange access: `core/exchange.py`
- Order execution: `core/execution_engine.py`
- Risk controls: `core/risk_engine.py`
- TP/SL engine: `core/tpsl_engine.py`
- Reconciliation and position validation: `core/reconciler.py`
- AI and external news analysis: `analysis/rumor_analyzer.py`
- Persistent state and accounting: `db/database.py`, `db/repositories.py`
- Telegram notifications: `reporting/telegram_bot.py`
- Desktop control panel: `desktop_app.py`

## Repository Layout

Important folders and files:

- `main.py`: main bot loop and orchestration
- `desktop_app.py`: local Tkinter-based control panel
- `run_desktop_app.bat`: Windows shortcut for launching the desktop app
- `config/`: `.env` to `Settings` parsing and behavior thresholds
- `core/`: execution, risk, health, reconcile, portfolio, and position logic
- `strategy/`: regime and scoring logic
- `indicators/`: technical indicator calculations
- `analysis/`: Groq, Exa, and CoinGecko-based research and sentiment layer
- `db/`: SQLite schema and repository layer
- `reporting/`: Telegram notifications
- `docs/`: desktop app notes and UI design documents
- `scratch/`: targeted coverage and test scripts
- `logs/`: runtime logs

## Requirements

- Python 3.11+ recommended
- OKX API credentials
- SQLite, included with Python
- Optional Telegram bot token and chat ID for notifications and remote controls
- Optional Groq, Exa, and CoinGecko credentials for AI/research features

## Python Dependencies

Packages currently listed in `requirements.txt`:

- `ccxt`
- `pandas`
- `pandas-ta-classic`
- `python-dotenv`
- `requests`

There is no separate desktop dependency listed. That is consistent with `desktop_app.py`, which uses Tkinter from the standard Python distribution.

## Setup

The repo does not pin a Python version explicitly. The codebase uses `from __future__ import annotations`, `dataclass`, `zoneinfo`, and modern typing, so Python 3.11+ is the safer practical choice.

Example setup:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Environment Variables

Because of the fallback chain in `config/settings.py`, the bot can read both `.env` and `_.env`. It also supports both `DB_PATH` and `SQLITE_PATH` for the database file path.

Start by copying `.env_example`:

```powershell
Copy-Item .env_example .env
```

Important: `.env_example` is not a safe first-run profile as-is. After copying it, change `DRY_RUN=1` before any real test, and consider `LLM_ENABLED=0` until the base execution path is verified.

Minimum critical fields:

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

Important notes:

- `config/settings.py` requires `OKX_API_KEY`, `OKX_SECRET`, and `OKX_PASSPHRASE`. Missing values raise `RuntimeError` on startup.
- `.env_example` uses `SQLITE_PATH` for the database key. The code supports that for backward compatibility.
- The AI research layer is optional. Set `LLM_ENABLED=0` to disable it completely.
- Telegram fields can be left empty. The bot can still run; only notifications are disabled.

## Telegram Control Surface

Telegram is not only a notifier in this project. `main.py` polls Telegram updates and accepts operational commands when `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` are configured.

Examples visible in the current code:

- Status and inspection: `/status`, `/health`, `/positions`, `/dust [max_usdt]`, `/params`, `/streaks`, `/pnl`, `/daily_pnl`, `/exit_stats`, `/price_debug`
- Manual control: `/trigger`, `/force_refresh`, `/reconcile`
- High-impact controls: `/dust_clean [max_usdt]`, `/close_all`, `/panic`, `/pause_trading`, `/resume_trading`

Treat Telegram command access as an operational control surface, not just an alert channel.

## Running

### Main bot

```powershell
python main.py
```

On Windows, the same main bot can be started from the repository root with:

```powershell
.\start_bot.bat
```

The main flow is roughly:

1. Load settings.
2. Initialize exchange, database, and helper engines.
3. Fetch market data each cycle.
4. Evaluate technical score, AI score, regime, and risk controls together.
5. Send orders when conditions allow.
6. Write results to logs and SQLite state.

Recommended safe first-run settings:

```env
OKX_SANDBOX=1
DRY_RUN=1
LLM_ENABLED=0
```

### Desktop control panel

```powershell
python desktop_app.py
```

On Windows:

```powershell
.\run_desktop_app.bat
```

To only verify database connectivity:

```powershell
python desktop_app.py --check
```

To point it at a different SQLite file:

```powershell
python desktop_app.py --db trading_bot.db
```

### Windows auto-start

Use `start_bot.bat` for Windows startup automation. Test it manually first:

```powershell
.\start_bot.bat
```

To register a Task Scheduler task that starts the bot when the current user logs in, run PowerShell from the repository root:

```powershell
schtasks /Create /TN "OKX Spot Bot" /TR "`"$PWD\start_bot.bat`"" /SC ONLOGON /RL LIMITED /F
```

Operational notes:

- The task runs under the current Windows user.
- Keep `.env` configured before enabling the task.
- Use `DRY_RUN=1` and `OKX_SANDBOX=1` until the startup path is verified.
- Check `logs\bot.log` after login to confirm the bot actually started.
- Disable it with:

```powershell
schtasks /Delete /TN "OKX Spot Bot" /F
```

The desktop app does not send orders directly. It writes control flags into `bot_state`. According to `docs/desktop_app.md`, controls such as force refresh, pause trading, and panic mode are managed through that channel.

## Configuration Areas

Important setting groups visible in the repo:

- Exchange and runtime mode: `OKX_SANDBOX`, `DRY_RUN`, `OKX_TD_MODE`
- Data collection: `SYMBOLS`, `TIMEFRAME`, `OHLCV_LIMIT`, `LOOP_SECONDS`
- Order and exposure limits: `MIN_ORDER_QUOTE_USDT`, `MAX_OPEN_POSITIONS`, `MAX_SYMBOL_EXPOSURE_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_SINGLE_TRADE_PCT`
- Daily risk guards: `MAX_DAILY_REALIZED_LOSS_USDT`, `MAX_DAILY_DRAWDOWN_PCT`
- Scale-in behavior: `SCALE_IN_ENABLED`, `SCALE_IN_TRIGGER_STREAK`, `MAX_SCALE_IN_COUNT`
- TP/SL: `STOP_LOSS_PCT`, `PARTIAL_TAKE_PROFIT_PCT`, `FULL_TAKE_PROFIT_PCT`, `BREAK_EVEN_*`, `TRAILING_TAKE_PROFIT_*`
- Regime engine: `REGIME_*`
- Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `NOTIFY_EVERY_CYCLE`
- AI and research: `LLM_ENABLED`, `GROQ_*`, `EXA_API_KEY`, `COINGECKO_*`

## Database and State

Based on `db/database.py`, the main tables are:

- `orders`: submitted order records
- `fills`: executed trade records and fill-level realized PnL
- `positions`: current per-symbol summarized position state
- `symbol_locks`: cooldown and lock state
- `bot_state`: small but critical runtime and control keys
- `cycle_reports`: per-cycle summaries

SQLite is opened in WAL mode. That is a sensible choice because the bot and desktop app may read and write against the same database file.

## Logs and Operational Monitoring

The code currently writes to two log files:

- `logs/bot.log`
- `logs/okx_debug.log`

`main.py` owns the main runtime log. `core/exchange.py` also appends low-level exchange adapter debug output to `logs/okx_debug.log`.

Useful log markers to watch:

- `[RECONCILE]`
- `[BUY SENT]`
- `[SELL SENT]`
- `[TPSL CHECK]`
- `[TPSL TRIGGER]`
- `[GROQ REFRESH]`
- `[AI THRESHOLD]`

## Development Notes

- `scratch/` contains targeted coverage and test scripts instead of a conventional `tests/` package. There is no `pytest.ini` visible in the repo root.
- `docs/ui_design.md` contains implementation notes for a future control surface.
- `docs/ui_mockup.*` looks like UI design output rather than part of the core runtime.
- The working tree currently includes runtime artifacts such as `trading_bot.db`, `test.db`, `.coverage`, and `logs/`. These are useful for understanding behavior but are not required source files.

## Known Limits

From the current source files:

- The project does not appear to have a finished production packaging or deployment flow.
- The desktop application is an initial control panel, not a packaged `.exe`.
- The AI research layer depends on external APIs and services. It has fallback logic, but it is not deterministic.
- `core/reconciler.py` is critical for position correctness. Its behavior should be watched closely before trusting live capital.
- `scratch/` scripts are useful development checks, but they are not a formal CI suite.

## Security

This project follows robust security protocols:
- **Dependabot:** Automated dependency and GitHub Actions updates.
- **CodeQL:** Static Application Security Testing (SAST) to detect vulnerabilities.
- **Security Policy:** Defined in [SECURITY.md](./SECURITY.md).
- **Proactive Scanning:** Integrated Bandit and pip-audit in CI/CD pipelines.
- **Pre-commit Hooks:** Local checks for secrets, private keys, and code quality.

## Safe Validation Flow


Recommended validation order:

1. Fill in `.env`.
2. Start with `OKX_SANDBOX=1` and `DRY_RUN=1`.
3. Run `python main.py`.
4. Watch `logs/bot.log` for health, reconcile, and signal flow.
5. Run `python desktop_app.py --check` to verify DB connectivity.
6. Run `python desktop_app.py` to confirm the UI can read produced state.
7. Only move toward live execution after those checks.

## Suggested Reading Order

For someone new to the repo:

1. `README.md`
2. `README_TR.md`
3. `main.py`
4. `config/settings.py`
5. `core/reconciler.py`
6. `core/tpsl_engine.py`
7. `strategy/scoring_engine.py`
8. `analysis/rumor_analyzer.py`
9. `db/database.py`
10. `docs/desktop_app.md`
