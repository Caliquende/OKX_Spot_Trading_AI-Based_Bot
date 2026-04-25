# OKX Spot Trading Bot

[Turkish README / Turkce README](./README_TR.md)

A modular OKX spot trading bot built around technical signals, market regime detection, deterministic execution controls, risk controls, TP/SL logic, Telegram monitoring, and an optional AI research layer.

Legal Disclaimer: Systems used in live markets carry financial risk.

## Current Status

This repository is a focused alpha with:
- **Logic verification through extensive white-box testing (95%+ branch coverage on core modules).**
- Refactored accounting layer for high auditability and simplified reconciliation.
- Strategy tuning and iterative development.

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

## Recent Structural Improvements

- **Reconciler Refactoring:** Core accounting logic (`reconciler.py`) simplified from 843 to 167 lines for better maintainability.
- **High Test Coverage:** Achieved 95%-100% branch coverage across `ExecutionEngine`, `TPSLEngine`, and `RegimeEngine`.
- **Database Integrity:** Repositories now verified via in-memory SQLite test suites.
- **Spot Consistency:** Exchange balance is used as the primary source of truth for open quantity.

## Architecture

### Main flow

- `main.py` orchestrates the runtime loop.
- `core/` contains exchange, execution, reconcile, risk, TP/SL, portfolio, and health logic.
- `strategy/` contains signal mapping and regime logic.
- `indicators/` builds technical indicators.
- `analysis/` contains AI research.
- `db/` stores persistent state and accounting data.

### Design principle

The bot is AI-assisted in research, but execution safety is deterministic. Order submission, locks, and accounting remain strict and auditable.

## Core Components

### Reconcile layer (`core/reconciler.py`)
One of the most sensitive parts. Syncs order state, writes fills into the database, and rebuilds current positions to align bot accounting with the exchange.

### Execution layer (`core/execution_engine.py`)
Safely translates decisions into exchange orders with strict lock and cooldown management.

### TP/SL layer (`core/tpsl_engine.py`)
Protects profits with Stop Loss, Partial/Full Take Profit, Break-Even Stop, and Trailing Protection.

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
- `db/repositories.py`: **90% Branch Coverage** (In-memory SQL verified)
- `core/reconciler.py`: **90% Branch Coverage**

## Environment Configuration

The project reads `.env` and `_.env`.

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

## Safety Warning

Before using a real account:
- test in sandbox,
- observe multiple cycles in `DRY_RUN=true`,
- verify reconcile behavior before trusting live capital.
