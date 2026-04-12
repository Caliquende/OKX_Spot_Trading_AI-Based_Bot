# OKX Spot Trading Bot

[Türkçe README](./README_TR.md)

A modular spot trading bot for OKX built around technical signals, market regime detection, risk controls, TP/SL logic, Telegram monitoring, and an optional AI research layer.
Legal Disclaimer: Systems used in live markets carry financial risk.

## Status

This repository is a **working alpha**.
It is suitable for study, testing, and iterative development. It is **not** presented as a guaranteed production-grade trading system.

## What the Bot Does

On each cycle, the bot broadly does the following:

1. Checks exchange and database health.
2. Fetches OHLCV data for each symbol.
3. Calculates technical indicators.
4. Refreshes research and sentiment context when needed.
5. Detects market regime.
6. Produces a combined score.
7. Applies TP/SL, risk, and execution gates.
8. Sends orders when conditions allow.
9. Reconciles order, fill, and position state.

## Architecture Overview

### Main runtime flow

- `main.py` orchestrates the main loop.
- `core/` contains exchange, execution, reconcile, risk, TP/SL, health, and portfolio engines.
- `strategy/` contains signal mapping and market regime logic.
- `indicators/` generates technical indicators.
- `analysis/` contains CoinGecko, Exa, and LLM-based research logic.
- `db/` stores persistent state and accounting data.
- `reporting/` handles Telegram notifications.

### Key design idea

The bot is **AI-based in research and signal shaping**, but **execution safety is deterministic**.

In practice, this means:
- the AI layer can influence sentiment and threshold behavior,
- while execution, locking, reconciliation, and order/accounting logic should remain strict and auditable.

## Core Components

### Exchange layer

`core/exchange.py` communicates with OKX through ccxt. The rest of the system should not depend on raw OKX responses directly.

### Execution layer

`core/execution_engine.py` applies decisions. It does not decide **what** to trade; it decides **how** to submit and manage orders safely.

### Reconcile layer

`core/reconciler.py` is one of the most sensitive parts of the project.
Its main responsibilities are:
- syncing order states,
- writing executed trades into `fills`,
- rebuilding current position state.

For spot trading, the practical rule is:
- **exchange balance** is the primary source for current open quantity,
- **fill history** is mainly used for average entry and realized PnL.

### Strategy layer

- `strategy/scoring_engine.py`: technical score to action mapping
- `strategy/regime_engine.py`: market regime detection such as TREND or RANGE

### Risk layer

`core/risk_engine.py` controls limits such as:
- total exposure,
- per-symbol exposure,
- single-trade size,
- scale-in constraints.

### TP/SL layer

`core/tpsl_engine.py` produces stop-loss and take-profit decisions such as `PARTIAL_CLOSE` or `FULL_CLOSE`. It does not submit orders itself.

### Research / LLM layer

`analysis/rumor_analyzer.py` can use:
- CoinGecko news,
- Exa web search,
- Groq with fallback model chaining.

This layer adds research context on top of the technical score.

## Database Model

The project uses SQLite.

Main table roles:
- `orders`: submitted order records
- `fills`: executed trade rows
- `positions`: current summarized position state
- `symbol_locks`: cooldown and lock state
- `bot_state`: small persistent state such as streaks, regime data, and pending exit metadata
- `cycle_reports`: cycle summaries

## Setup

### 1. Prepare Python

Use a virtual environment if possible.

Example starter dependencies:

```bash
pip install ccxt pandas pandas-ta-classic requests python-dotenv
```

### 2. Prepare `.env`

The project can read `.env` and `_.env`.

Minimum required fields:

```env
OKX_API_KEY=your_key
OKX_SECRET=your_secret
OKX_PASSPHRASE=your_passphrase

OKX_SANDBOX=true
DRY_RUN=true

SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
TIMEFRAME=15m
LOOP_SECONDS=60
DB_PATH=trading_bot.db
```

Optional AI/research example:

```env
LLM_ENABLED=true
GROQ_API_KEY=your_groq_key
GROQ_MODEL=groq/compound
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_FALLBACK_MODEL=llama-3.3-70b-versatile
EXA_API_KEY=your_exa_key
COINGECKO_DEMO_API_KEY=your_demo_key
```

Optional Telegram example:

```env
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Run

```bash
python main.py
```

After startup:
- runtime logs are written under `logs/`
- the SQLite database is created at `DB_PATH`

## Recommended First Start

For an initial test:

```env
OKX_SANDBOX=true
DRY_RUN=true
LLM_ENABLED=false
```

Meaning:
- use sandbox instead of a real account,
- do not place real orders,
- test execution and reconcile behavior first.

## Important Configuration Areas

### Trade and risk

- `MIN_ORDER_QUOTE_USDT`
- `MIN_FREE_USDT`
- `MAX_OPEN_POSITIONS`
- `MAX_SYMBOL_EXPOSURE_PCT`
- `MAX_TOTAL_EXPOSURE_PCT`
- `MAX_SINGLE_TRADE_PCT`

### Signal thresholds

- `BUY_THRESHOLD`
- `STRONG_BUY_THRESHOLD`
- `SELL_THRESHOLD`
- `STRONG_SELL_THRESHOLD`

### Scale-in

- `SCALE_IN_ENABLED`
- `SCALE_IN_TRIGGER_STREAK`
- `STRONG_SCALE_IN_TRIGGER_STREAK`
- `MAX_SCALE_IN_COUNT`

### TP/SL

- `TPSL_ENABLED`
- `STOP_LOSS_PCT`
- `PARTIAL_TAKE_PROFIT_PCT`
- `FULL_TAKE_PROFIT_PCT`

### Reconcile

- `POSITION_SOURCE_MODE`
- `MIN_POSITION_VALUE_USDT`
- `RECONCILE_WARN_ABS_QUOTE_USDT`
- `RECONCILE_WARN_RATIO`
- `LIVE_FORCE_CLOSE_ON_ZERO_BALANCE`

### AI / research

- `LLM_ENABLED`
- `GROQ_MODEL`
- `GROQ_FALLBACK_MODEL`
- `GROQ_FALLBACK_FALLBACK_MODEL`
- `EXA_API_KEY`
- `COINGECKO_DEMO_API_KEY`

## Logs to Watch

Key log families:
- `[RECON]`
- `[POSITION]`
- `[POSITION MISMATCH]`
- `[BUY SENT]` / `[SELL SENT]`
- `[TPSL CHECK]` / `[TPSL TRIGGER]`
- `[GROQ REFRESH]`
- `[AI THRESHOLD]`
- `[GHOST ORDER CLEANED]`

Important files:
- `logs/bot.log`
- `logs/okx_debug.log`

## Telegram Commands

Command parsing lives in `main.py`.
Telegram transport lives in `reporting/telegram_bot.py`.

This README is intentionally high level. For the authoritative command list, check the slash-command handling blocks in `main.py`.

## Reading Order

If you want to understand the project quickly, read in this order:

1. `README_EN.md`
2. `main.py`
3. `core/reconciler.py`
4. `core/execution_engine.py`
5. `strategy/scoring_engine.py`
6. `strategy/regime_engine.py`
7. `analysis/rumor_analyzer.py`
8. `db/database.py`
9. `db/repositories.py`

## Design Notes

- This bot is focused on **spot trading**.
- Reconcile and position accounting are the most fragile layers.
- The `positions` table stores current summary state, not full history.
- The `fills` table is the accounting history.
- The AI layer can shape decisions, but execution safety must remain deterministic.

## Safety Warning

Before using a real account:
- test in sandbox,
- observe at least a few cycles with `DRY_RUN=true`,
- start with conservative limits,
- read `bot.log` carefully,
- verify reconcile behavior before trusting live capital.

This project can place orders.
A wrong `.env` configuration can cause real financial loss.
