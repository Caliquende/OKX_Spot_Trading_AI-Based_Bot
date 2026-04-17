# OKX Bot Control Tower UI Design

This document defines an implementation-ready UI direction for the OKX spot trading bot. The UI should be an operations cockpit first, not a decorative trading terminal. Its main job is to make bot state, AI decisions, risk gates, and exchange execution easy to audit before any manual action is taken.

## 1. Product Constraints And Goals

Primary user: one technical operator monitoring and controlling a live or sandbox OKX spot bot.

Runtime context:
- Data is currently available through SQLite tables, runtime settings, OKX API checks, logs, and Telegram notifications.
- Telegram remains the fast alert surface; the web UI becomes the deeper inspection and control surface.
- The UI must clearly separate read-only monitoring from trade-affecting controls.
- The bot can run in unstable market conditions, so the interface must emphasize state, uncertainty, and guardrails.

Core goals:
- Show whether the bot is safe to trust within 5 seconds.
- Explain each symbol decision without opening raw logs.
- Expose AI score, technical score, total score, regime, thresholds, and execution result together.
- Make risk controls obvious but difficult to trigger accidentally.
- Preserve enough audit detail to understand why a position was opened, held, scaled, or closed.

Non-goals for the first UI version:
- No charting-heavy trading terminal.
- No portfolio analytics suite.
- No direct strategy editing from the UI until read-only observability is stable.
- No hover-only actions for trading controls.

Visual direction:
- Use a dark graphite operations cockpit style.
- Use amber for degraded/risk states, crimson for panic/critical states, green only for genuinely healthy or profitable states, and cyan/blue for neutral information.
- Use tabular or monospace numerals for prices, balances, scores, and quantities.
- Keep density high on desktop, but switch to stacked cards on mobile.

## 2. IA And Navigation Model

Primary navigation:
- Overview
- Positions
- Signals
- Risk & Controls
- Orders & Fills
- Cycle Reports
- Settings Snapshot

Desktop navigation:
- Fixed left rail with section names and compact status indicator.
- Main content area for the active screen.
- Optional right-side drawer for decision traces, order details, or risk details.

Tablet navigation:
- Top segmented navigation.
- Detail drawer opens as a full-height side panel.

Mobile navigation:
- Bottom navigation with Overview, Positions, Signals, Controls, and More.
- Critical global status remains sticky at the top.

Core entities:
- Symbol
- Position
- Order
- Fill
- Cycle report
- Health check
- AI threshold set
- Groq sentiment result
- Risk control state
- Runtime setting

High-frequency workflows:
- Check if bot is healthy.
- Review latest cycle decisions.
- Inspect why a symbol action was HOLD, BUY, SELL, PARTIAL_CLOSE, or FULL_CLOSE.
- Verify whether an order was sent, blocked, filled, or stale.
- Pause trading or trigger panic mode.
- Review open positions and risk state.

## 3. Screen Specs

### Overview

Purpose: provide a complete trust snapshot for the bot.

Hierarchy:
- Global status banner: RUNNING, DEGRADED, PAUSED, PANIC, or CRITICAL.
- Health strip: exchange, public API, private API, DB.
- Portfolio strip: total balance, cash balance, cash ratio, total exposure, open positions.
- AI state card: current thresholds, Groq model, last sentiment refresh time.
- Latest cycle panel: summarized symbol decisions.
- Alerts panel: failed health checks, blocked entries, stale orders, locks, recent exits.

States:
- Loading: skeleton tiles for health, portfolio, and latest cycle.
- Empty: "No cycle report yet" with last boot time if available.
- Success: all cards populated with timestamps.
- Partial: show stale/missing panels independently, not a full-screen error.
- Error: global status banner explains which source failed.

Primary actions:
- Refresh data.
- Open latest cycle detail.
- Open Risk & Controls.

### Positions

Purpose: show active exposure and position-level risk.

Hierarchy:
- Summary row: active position count, total exposure, cash ratio, largest symbol exposure.
- Position table on desktop.
- Position cards on mobile.
- Detail drawer with average entry, current quantity, realized PnL, estimated unrealized PnL, TPSL thresholds, peak PnL, lock state, and scale-in state.

Required fields:
- Symbol
- Quantity
- Average entry
- Estimated value
- Realized PnL
- TPSL status
- Peak PnL
- Cooldown/lock
- Scale-in count
- Last execution result

States:
- Empty: "No open positions".
- Partial: position exists but ticker unavailable; show quantity and average entry, mark market-derived values stale.
- Error: DB or exchange failure shown in the position card and global banner.

Primary actions:
- View decision trace.
- View orders/fills for symbol.
- Partial close, full close, and lock symbol only after confirmation.

### Signals

Purpose: make bot decisions explainable and catch extreme AI behavior quickly.

Hierarchy:
- Filter bar: cycle time, symbol, action, regime, model.
- Decision matrix: one row per symbol.
- Score visualization: indicator score, AI score, total score, threshold bands.
- Decision trace drawer.

Required fields:
- Symbol
- Regime
- Trend bias
- Indicator score
- AI score
- Total score
- Buy/sell thresholds
- Final action
- Stance
- Streak
- Execution result
- Groq summary

Decision trace drawer:
- Raw score components.
- Regime diagnostics.
- AI sentiment provider outputs.
- Applied thresholds.
- Risk and execution gates.
- Final reason.

States:
- Empty: "No signal cycle yet".
- Partial: AI unavailable but technical score available.
- Error: mark failed provider without hiding technical state.

Primary actions:
- Open decision trace.
- Compare previous cycle.
- Copy compact report text for debugging.

### Risk & Controls

Purpose: centralize manual intervention while preventing accidental destructive actions.

Hierarchy:
- Current control state: panic mode, trading paused, entries blocked, auto risk guard.
- Safe actions: force refresh, reconcile now, refresh health.
- Guarded actions: pause trading, resume trading, clear symbol lock.
- Danger zone: panic, close all, full close symbol.

States:
- Loading: controls disabled until current state is known.
- Success: controls enabled according to state.
- Error: trade-affecting controls disabled if private API or DB check fails.

Primary actions:
- Force refresh.
- Pause trading.
- Resume trading.
- Enable panic mode.
- Close all positions.

Safeguards:
- Danger actions require explicit typed confirmation.
- Confirm dialogs must show symbol, quantity, estimated value, and mode.
- Close-all must list affected symbols before confirmation.
- No action should execute from hover-only controls.

### Orders & Fills

Purpose: show exchange execution and local reconciliation state.

Hierarchy:
- Open orders table.
- Recent fills table.
- Exit statistics panel.
- Daily realized PnL summary.

Required order fields:
- Symbol
- Side
- Type
- Status
- Quantity
- Filled quantity
- Client order ID
- Exchange order ID
- Created/updated time
- Stale/ghost status

Required fill fields:
- Symbol
- Side
- Quantity
- Price
- Fee
- Timestamp
- Source
- Realized PnL impact

States:
- Empty: "No recent orders/fills".
- Partial: exchange unavailable, DB records still visible with stale label.
- Error: table-level error, not full-page error.

Primary actions:
- Filter by symbol.
- Open order detail.
- Open related cycle.

### Cycle Reports

Purpose: turn the Telegram BOT CYCLE message into an inspectable timeline.

Hierarchy:
- Cycle list with timestamp, health severity, number of actions, and execution count.
- Cycle detail timeline.
- Symbol decision sections.

Timeline sections:
- Cycle start.
- Health check.
- AI threshold update.
- Sentiment refresh.
- Per-symbol decision.
- Execution result.
- Cycle end snapshot.

States:
- Empty: "No persisted cycle reports".
- Partial: report text exists but parsed fields unavailable.
- Error: DB read failure.

Primary actions:
- Open cycle detail.
- Filter cycles by symbol/action/severity.
- Copy report.

### Settings Snapshot

Purpose: expose runtime configuration safely.

Hierarchy:
- Environment mode: sandbox/live.
- Symbol list.
- Risk settings.
- AI/Groq settings.
- Threshold defaults.
- Cooldown and lock settings.
- Read-only raw config view.

States:
- Success: grouped config sections.
- Error: config load issue.

Primary actions:
- Copy safe config summary.
- Open documentation link.

Rules:
- Secrets must never render.
- API keys, tokens, and passwords must display as masked/unavailable.
- The screen is read-only in the first version.

## 4. Component Architecture

### StatusBanner

Contract:
- props: status, severity, title, message, updatedAt, actions.
- variants: running, degraded, paused, panic, critical.
- behavior: sticky on mobile, top-of-page on desktop.

### HealthStrip

Contract:
- props: exchangeOk, publicOk, privateOk, dbOk, errors, checkedAt.
- behavior: each check is a labeled pill with text and icon.
- error state: failed checks expose short error details in an expandable row.

### MetricTile

Contract:
- props: label, value, unit, trend, severity, sublabel, loading.
- usage: portfolio, exposure, cash, PnL, position count.

### SymbolDecisionRow

Contract:
- props: symbol, regime, action, stance, indicatorScore, aiScore, totalScore, thresholds, execution, groqSummary.
- behavior: row opens DecisionTraceDrawer.
- mobile: transforms into a card.

### ScoreBar

Contract:
- props: indicatorScore, aiScore, totalScore, min, max, thresholds.
- behavior: visualizes threshold bands and highlights extreme AI score variance.
- accessibility: includes text equivalents for all values.

### ThresholdBand

Contract:
- props: buy, strongBuy, sell, strongSell.
- behavior: renders semantic boundaries on ScoreBar.

### PositionRiskCard

Contract:
- props: symbol, qty, avg, realizedPnl, estimatedValue, tpsl, peakPnlPct, cooldown, scaleCount, blockedReason.
- behavior: shows guardrail state before any trade action.

### DecisionTraceDrawer

Contract:
- props: symbol, cycleId, scoreDetails, regimeDiagnostics, aiProviderResults, thresholds, finalAction, executionResult.
- behavior: persistent drawer on desktop, full-screen modal on mobile.

### RiskActionButton

Contract:
- props: actionType, symbol, severity, disabledReason, confirmationMode, onConfirm.
- behavior: destructive actions require typed confirmation.

### EventTimeline

Contract:
- props: events, selectedEventId.
- event types: health, threshold, sentiment, decision, order, fill, lock, cash.

### DataTable

Contract:
- props: columns, rows, loading, emptyMessage, errorMessage, rowAction.
- behavior: keyboard navigable rows, sticky headers on desktop.

## 5. Responsive And Accessibility Rules

Breakpoints:
- Mobile: below 768px.
- Tablet: 768px to 1279px.
- Desktop: 1280px and above.

Desktop behavior:
- Use left navigation.
- Use dense tables for Positions, Signals, Orders, and Fills.
- Use a right-side drawer for details.

Tablet behavior:
- Use top navigation.
- Tables can stay, but hide low-priority columns behind row expansion.
- Detail drawer may overlay content.

Mobile behavior:
- Use bottom navigation.
- Convert tables into cards.
- Keep the global status banner sticky.
- Put destructive controls behind full-screen confirmation.

Accessibility:
- Minimum touch target is 44px.
- Do not rely on color alone; every state needs text.
- Preserve heading hierarchy per screen.
- All controls must be keyboard reachable.
- Dialogs trap focus and return focus after close.
- Score visualizations must include readable numeric labels.
- Data refreshes should announce status changes without stealing focus.

Copy rules:
- Use direct operational language.
- Prefer "Trading paused" over vague labels like "Inactive".
- Prefer "Private API failed" over "Exchange degraded" when the specific check is known.
- For blocked execution, show the exact blocked reason from the bot.

## 6. Delivery Plan

Phase 1: read-only shell and Overview.
- Build app frame, navigation, global status banner, health strip, portfolio cards, and latest cycle summary.
- Backend endpoints: overview, health, latest cycle, positions.

Phase 2: Positions and Signals.
- Build position table/cards.
- Build decision matrix, score bars, threshold bands, and decision trace drawer.
- Backend endpoints: latest signals, symbol decision trace, current positions.

Phase 3: Orders, Fills, and Cycle Reports.
- Build orders table, fills table, cycle list, and cycle detail timeline.
- Backend endpoints: orders, fills, cycle reports, daily PnL, exit stats.

Phase 4: guarded controls.
- Add force refresh, pause/resume, panic, close all, close symbol, and reconcile actions.
- Add typed confirmations and disabled states based on health.

Phase 5: settings snapshot and polish.
- Add read-only runtime config.
- Add masked secret handling.
- Add visual QA and mobile QA pass.

Validation checklist:
- Overview shows OK, DEGRADED, PAUSED, PANIC, and CRITICAL states correctly.
- Private API failure disables trade-affecting controls.
- DB failure disables all control actions that require persisted state.
- Empty positions, empty orders, and missing cycle reports render clear empty states.
- Extreme AI score and final action are visible on the same row.
- Decision trace explains score, thresholds, risk gates, and execution result.
- Mobile layout has no hover-only interactions.
- Destructive actions cannot be triggered without confirmation.
- Secrets never render in Settings Snapshot.

Regression risks:
- UI may imply precision for market values if ticker data is stale; stale labels are mandatory.
- Too many columns can hide the actual bot decision; Signals must prioritize action, total, AI score, regime, and exec.
- Control actions can become dangerous if health gating is incomplete.
- Parsed cycle reports may drift from Telegram formatting; keep raw report available as fallback.
- If `.env` settings change while the bot is running, Settings Snapshot must distinguish configured values from live runtime values.

Simplification fallback:
- If full UI scope is too large, ship only Overview, Positions, Signals, and read-only Cycle Reports first.
- Keep all trade-affecting controls in Telegram until UI health gating and confirmations are proven.
