# OKX Spot Trading Bot

[Turkish README / Turkce README](./README_TR.md)

A modular OKX spot trading bot built around technical signals, market regime detection, deterministic execution controls, risk controls, TP/SL logic, Telegram monitoring, and an optional AI research layer.

Legal Disclaimer: Systems used in live markets carry financial risk.

## Current Status

This repository is a working alpha focused on:
- learning,
- testing,
- iterative development,
- strategy tuning.

It is not presented as a guaranteed production-grade system.

## What The Bot Does

On each cycle, the bot broadly does the following:

1. Checks exchange and database health.
2. Fetches OHLCV data for each symbol.
3. Calculates technical indicators.
4. Refreshes AI research context when TTL expires.
5. Detects market regime such as TREND, RANGE, CHOP, or VOLATILE.
6. Builds a technical score and an AI score.
7. Produces a combined score using the average of technical and AI scores.
8. Applies TP/SL, risk, lock, and execution gates.
9. Reconciles orders, fills, and positions back into SQLite state.

## What Changed Recently

The current version reflects the latest strategy work:

- Spot position quantity now follows exchange balance as the main source of truth.
- Fill history is mainly used for average entry and realized PnL.
- Ghost order cleanup is less noisy and state-aware.
- AI model fallback chain is configurable from `.env`.
- AI refresh TTL is configurable from `.env`.
- AI score band is now `-24 .. 24`.
- Default AI stance fallback scores are now:
  - `SELL = -8`
  - `STRONG_SELL = -16`
  - `BUY = 8`
  - `STRONG_BUY = 16`
- Combined score is now:
  - `(technical_score + ai_score) / 2`
- TP/SL logic now includes:
  - candle-high-aware take profit detection,
  - break-even stop logic,
  - trailing take-profit rollback protection.

## Architecture

### Main flow

- `main.py` orchestrates the runtime loop.
- `core/` contains exchange, execution, reconcile, risk, TP/SL, portfolio, and health logic.
- `strategy/` contains signal mapping and regime logic.
- `indicators/` builds technical indicators.
- `analysis/` contains CoinGecko, Exa, and Groq-based AI research.
- `db/` stores persistent state and accounting data.
- `reporting/` handles Telegram messaging.

### Design principle

The bot is AI-assisted in research and score shaping, but execution safety is deterministic.

That means:
- AI can influence sentiment and thresholds,
- but order submission, locks, reconciliation, and accounting should remain strict and auditable.

## Core Components

### Exchange layer

`core/exchange.py` talks to OKX through ccxt.

### Execution layer

`core/execution_engine.py` applies already-made decisions safely.

### Reconcile layer

`core/reconciler.py` is one of the most sensitive parts of the project.

Its main jobs are:
- sync order state,
- write fills into the database,
- rebuild current positions,
- keep bot accounting aligned with exchange state.

For spot trading, the current practical rule is:
- exchange balance decides open quantity,
- fills support accounting details such as average entry and realized PnL.

### Strategy layer

- `strategy/scoring_engine.py`: maps score to action
- `strategy/regime_engine.py`: detects market regime

### TP/SL layer

`core/tpsl_engine.py` now protects profits more aggressively with:
- stop loss,
- partial take profit,
- full take profit,
- break-even stop,
- trailing rollback protection.

### Research layer

`analysis/rumor_analyzer.py` can use:
- CoinGecko news,
- Exa search,
- Groq AI with multi-step model fallback.

## Database

SQLite is used.

Main table roles:
- `orders`: submitted order records
- `fills`: executed trade rows
- `positions`: current summarized position state
- `symbol_locks`: cooldown and lock state
- `bot_state`: small persistent state such as streaks, pending exit reasons, regime state, and TP/SL state
- `cycle_reports`: cycle summaries

## Environment Configuration

The project reads `.env` and `_.env`.

### Minimum required fields

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

### AI / research example

```env
LLM_ENABLED=true
GROQ_API_KEY=your_groq_key

GROQ_MODEL=groq/compound
GROQ_FALLBACK_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_FALLBACK_MODEL=llama-3.3-70b-versatile
GROQ_FALLBACK_FALLBACK_FALLBACK_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
GROQ_FALLBACK_FALLBACK_FALLBACK_FALLBACK_MODEL=llama-3.1-8b-instant

GROQ_CACHE_TTL_SECONDS=7200
THRESHOLD_UPDATE_TTL_SECONDS=7200

EXA_API_KEY=your_exa_key
COINGECKO_DEMO_API_KEY=your_demo_key
```

### TP/SL example

```env
TPSL_ENABLED=true
STOP_LOSS_PCT=0.06
PARTIAL_TAKE_PROFIT_ENABLED=true
PARTIAL_TAKE_PROFIT_PCT=0.04
FULL_TAKE_PROFIT_ENABLED=true
FULL_TAKE_PROFIT_PCT=0.08

BREAK_EVEN_STOP_ENABLED=true
BREAK_EVEN_ACTIVATION_PCT=0.03
BREAK_EVEN_BUFFER_PCT=0.002

TRAILING_TAKE_PROFIT_ENABLED=true
TRAILING_TAKE_PROFIT_ACTIVATION_PCT=0.05
TRAILING_TAKE_PROFIT_GIVEBACK_PCT=0.02
```

### Daily risk guard example

Set either value above `0` to activate the automatic entry block.
When triggered, the bot blocks new entries and scale-ins, but exits still remain allowed.

```env
MAX_DAILY_REALIZED_LOSS_USDT=100
MAX_DAILY_DRAWDOWN_PCT=0.08
```

## Run

```bash
python main.py
```

After startup:
- runtime logs are written under `logs/`
- the SQLite database is created under `DB_PATH`

## Recommended Safe Start

For a first run:

```env
OKX_SANDBOX=true
DRY_RUN=true
LLM_ENABLED=false
```

Meaning:
- use sandbox,
- do not send real orders,
- validate execution and reconcile behavior first.

## Important Config Areas

### Risk and sizing

- `MIN_ORDER_QUOTE_USDT`
- `MIN_FREE_USDT`
- `MAX_OPEN_POSITIONS`
- `MAX_SYMBOL_EXPOSURE_PCT`
- `MAX_TOTAL_EXPOSURE_PCT`
- `MAX_SINGLE_TRADE_PCT`
- `MAX_DAILY_REALIZED_LOSS_USDT`
- `MAX_DAILY_DRAWDOWN_PCT`

### Thresholds

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

- `STOP_LOSS_PCT`
- `PARTIAL_TAKE_PROFIT_PCT`
- `FULL_TAKE_PROFIT_PCT`
- `BREAK_EVEN_ACTIVATION_PCT`
- `TRAILING_TAKE_PROFIT_ACTIVATION_PCT`
- `TRAILING_TAKE_PROFIT_GIVEBACK_PCT`

### AI

- `GROQ_MODEL`
- `GROQ_FALLBACK_MODEL`
- `GROQ_FALLBACK_FALLBACK_MODEL`
- `GROQ_FALLBACK_FALLBACK_FALLBACK_MODEL`
- `GROQ_FALLBACK_FALLBACK_FALLBACK_FALLBACK_MODEL`
- `GROQ_CACHE_TTL_SECONDS`
- `THRESHOLD_UPDATE_TTL_SECONDS`

## Logs To Watch

Key logs:
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

## Recommended Reading Order

1. `README.md`
2. `README_TR.md`
3. `main.py`
4. `core/reconciler.py`
5. `core/tpsl_engine.py`
6. `strategy/scoring_engine.py`
7. `analysis/rumor_analyzer.py`
8. `db/database.py`
9. `db/repositories.py`

## Safety Warning

This project can place orders.

Before using a real account:
- test in sandbox,
- observe multiple cycles in `DRY_RUN=true`,
- start with conservative exposure,
- read `bot.log`,
- verify reconcile behavior before trusting live capital.
