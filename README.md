# OKX Spot Trading Bot

[Turkish README / Turkce README](./README_TR.md)

A modular OKX spot trading bot built around technical signals, market regime detection, deterministic execution controls, risk controls, TP/SL logic, Telegram monitoring, and an optional AI research layer.

Legal Disclaimer: Systems used in live markets carry financial risk.

## Current Status

This repository is a focused alpha with:
- **Logic verification through extensive white-box testing (95%+ branch coverage on core modules).**
- **Refactored accounting layer (`reconciler.py`) simplified from 843 to 167 lines for high auditability.**
- Strategy tuning and iterative development.
- It is not presented as a guaranteed production-grade system.

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

The current version reflects recent structural and logic hardening:

- **Reconciler Refactoring:** Core accounting logic simplified for better maintainability and error handling.
- **High Test Coverage:** Achieved 95%-100% branch coverage across `ExecutionEngine`, `TPSLEngine`, and `RegimeEngine`.
- **Database Integrity:** Repositories now verified via in-memory SQLite test suites.
- Spot position quantity now follows exchange balance as the main source of truth.
- Fill history is mainly used for average entry and realized PnL.
- Ghost order cleanup is less noisy and state-aware.
- AI model fallback chain and refresh TTL are configurable from `.env`.
- TP/SL logic now includes candle-high-aware take profit detection, break-even stop logic, and trailing rollback protection.

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
Order submission, locks, reconciliation, and accounting remain strict and auditable.

## Core Components

### Exchange layer
`core/exchange.py` talks to OKX through ccxt.

### Execution layer
`core/execution_engine.py` applies already-made decisions safely. Verified with 100% branch coverage.

### Reconcile layer
`core/reconciler.py` is the sensitive accounting core. Syncs order state, writes fills, and rebuilds positions. Simplified for high reliability.

### TP/SL layer
`core/tpsl_engine.py` protects profits with Stop Loss, Partial/Full Take Profit, Break-Even Stop, and Trailing Protection.

## Testing

The project emphasizes logic verification through white-box testing. Comprehensive test suites are located in the `scratch/` directory.

### Run all tests with coverage
```bash
$env:PYTHONPATH="."
pytest scratch/ --cov=core --cov=db.repositories --cov=strategy --cov-branch --cov-report=term-missing
```

### Coverage Status
- `strategy/regime_engine.py`: **100% Branch Coverage**
- `core/execution_engine.py`: **100% Branch Coverage**
- `core/tpsl_engine.py`: **96% Branch Coverage**
- `db/repositories.py`: **90% Branch Coverage** (Verified with in-memory SQLite)
- `core/reconciler.py`: **90% Branch Coverage**

## Environment Configuration

### Minimum required fields
```env
OKX_API_KEY=your_key
OKX_SECRET=your_secret
OKX_PASSPHRASE=your_passphrase
OKX_SANDBOX=true
DRY_RUN=true
SYMBOLS=BTC/USDT,ETH/USDT
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
GROQ_CACHE_TTL_SECONDS=7200
EXA_API_KEY=your_exa_key
```

### TP/SL example
```env
TPSL_ENABLED=true
STOP_LOSS_PCT=0.06
PARTIAL_TAKE_PROFIT_PCT=0.04
FULL_TAKE_PROFIT_PCT=0.08
BREAK_EVEN_STOP_ENABLED=true
TRAILING_TAKE_PROFIT_ENABLED=true
```

## Important Config Areas

### Risk and sizing
- `MAX_OPEN_POSITIONS`, `MAX_SYMBOL_EXPOSURE_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_DAILY_REALIZED_LOSS_USDT`, `MAX_DAILY_DRAWDOWN_PCT`.

### Scale-in
- `SCALE_IN_ENABLED`, `SCALE_IN_TRIGGER_STREAK`, `MAX_SCALE_IN_COUNT`.

## Safety Warning

This project can place orders. Before using a real account:
- test in sandbox,
- observe multiple cycles in `DRY_RUN=true`,
- verify reconcile behavior before trusting live capital.
