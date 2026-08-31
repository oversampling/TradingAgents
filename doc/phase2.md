# Phase 2 Proposal: Moomoo Realized and Unrealized P&L Tracking

## 1. Objective

Add a strictly read-only portfolio accounting module that records and reports:

- **unrealized P&L** for currently open Moomoo positions;
- **realized P&L** from closed or partially closed lots; and
- daily account/position snapshots, reconciliation status, and data freshness.

It extends Phase 1's Moomoo integration. It does not unlock trading, place an
order, modify an order, transfer cash, or modify a watchlist.

## 2. Moomoo API basis

The design uses official Moomoo OpenAPI v10.9 endpoints only.

| Need | Moomoo endpoint | Why it is used |
|---|---|---|
| Current open-position P&L | `position_list_query` | Returns position quantity, cost, market value, `pl_val`, `unrealized_pl`, `realized_pl`, `pl_ratio`, currency, and position ID. |
| Historical executed fills | `history_deal_list_query` | Returns live-account fills with stable deal ID, order ID, side, quantity, price, code, and execution timestamp. |
| Account equity/cash reconciliation | `accinfo_query` | Returns account-level net assets, securities market value, cash, and purchasing power. |
| Cash-flow classification | `get_acc_cash_flow` | Identifies deposits, withdrawals, transfers, FX, buys/sells, interest, and other cash-affecting events. |

Moomoo documents that position queries may use cached OpenD data or request a
refresh; a refreshed call is subject to a maximum of 10 requests per 30 seconds
per account. The job will issue at most one refresh per selected account/market
per snapshot. [Get Positions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html)

Historical deals are documented as live-trading only. They accept a specific
account ID and bounded date/time range; when no range is supplied, the default
window is the prior 90 days. Therefore Phase 2 will synchronize explicit
90-day-or-smaller windows and persist all fetched fills locally. [Get Historical
Deals](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html)

## 3. Definitions and source of truth

### Unrealized P&L

Unrealized P&L is the gain or loss on quantities that are still open:

```text
unrealized P&L = current market value − cost basis of open quantity
```

For the first release, Moomoo's `unrealized_pl` is the displayed broker value.
We also retain `market_val`, nominal/current price, `cost_price`,
`diluted_cost`, `average_cost`, `pl_val`, `pl_ratio`, currency, and position ID
to make that display auditable. Moomoo describes the securities-account P&L
ratio as based on diluted cost; futures use average cost instead. [Get
Positions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html)

### Realized P&L

Realized P&L is the gain or loss caused by a sale/cover that closes all or part
of a prior lot. It cannot safely be reconstructed from one daily position
snapshot alone, because partial fills, multiple purchase lots, corporate
actions, fees, and changing position quantities matter.

Phase 2 will retain two values:

1. **Broker-reported realized P&L** — the `realized_pl` returned by the current
   position endpoint when Moomoo provides it.
2. **Locally calculated realized P&L** — a deterministic lot ledger derived
   from historical fills, labelled `estimated_realized_pnl` until reconciled.

They must never be silently substituted for one another. The report will show
the difference and call out material mismatches.

### Accounting convention

The initial local ledger uses **FIFO by account + instrument + position side**:

```text
BUY  opens long lots
SELL closes oldest long lots first
SELL_SHORT opens short lots
BUY_BACK closes oldest short lots first
```

For a long lot:

```text
realized gross P&L = closing proceeds − opening cost
```

For a short lot:

```text
realized gross P&L = opening proceeds − cover cost
```

The local value is gross of fees until a fee-allocation rule is validated. Tax
reporting, tax-lot elections, and official broker statements are out of scope;
this is portfolio monitoring, not tax advice.

## 4. Scope

### In scope

- One configured Moomoo live account, or explicitly configured separate US/MY
  account IDs.
- US and Malaysia equities supported by the existing Phase 1 mapper.
- Current unrealized P&L snapshot per open position.
- Historical-fill synchronization and idempotent local storage.
- FIFO estimated realized P&L.
- Broker/local reconciliation and exception reporting.
- Account-level cash/equity snapshots.
- Daily P&L HTML report and optional integration into the Phase 1 email.
- CLI commands for backfill, snapshot, report, and reconciliation.

### Out of scope for the first release

- Any Moomoo trading/unlock/order API.
- Tax reporting, tax-lot election, wash-sale rules, or tax forms.
- Options, futures, complex strategies, multi-leg options, and fractional
  corporate-action handling.
- Cross-currency aggregation into a chosen base currency.
- Deposits/withdrawals treated as investment return.
- Intraday streaming P&L.

## 5. Architecture

```text
LaunchAgent / manual CLI
        |
        v
PnlSyncPipeline (single-run lock)
        |
        +--> MoomooReadOnlyProvider
        |       +--> account discovery / explicit account selection
        |       +--> position_list_query(refresh_cache=True)
        |       +--> accinfo_query(refresh_cache=True)
        |       +--> history_deal_list_query(start, end)
        |       +--> get_acc_cash_flow (reconciliation only)
        |
        v
SQLite transaction
        +--> immutable fill upsert
        +--> account and position snapshots
        +--> FIFO lot reconstruction
        +--> realized/unrealized reconciliation
        |
        v
HTML + JSON P&L report
        |
        +--> optional section in Phase 1 email
        +--> local artifact and CLI query
```

All Moomoo calls remain in a new explicit allowlist. The provider must expose
only account discovery, positions, account funds, historical fills, and cash
flows. Code review must reject imports or calls to `unlock_trade`,
`place_order`, `modify_order`, transfer, or watchlist-write APIs.

## 6. Data model

Use the existing Phase 1 SQLite database or a dedicated `pnl.sqlite3` inside
the same private runtime directory. A dedicated database is preferred so the
daily-news pipeline cannot block financial-history migrations.

### Core tables

#### `pnl_sync_runs`

```text
id, started_at, completed_at, status, account_id, markets,
fills_from, fills_to, position_snapshot_at, error_summary
```

#### `broker_fills`

```text
account_id, deal_id, order_id, code, stock_name, market,
trd_side, quantity, price, execution_time, currency,
raw_payload_json, first_seen_at, last_seen_at
```

Primary key: `(account_id, deal_id)`.

The raw payload is retained because a broker correction must be auditable. On
a duplicate deal ID, immutable economic fields must match; otherwise flag a
`broker_fill_correction` rather than overwriting silently.

#### `open_lots`

```text
lot_id, account_id, code, position_side, opening_deal_id,
opened_at, original_quantity, remaining_quantity, opening_price,
currency, status
```

#### `lot_closures`

```text
closure_id, account_id, closing_deal_id, opening_lot_id,
quantity_closed, opening_price, closing_price,
gross_realized_pnl, currency, closed_at
```

Unique key: `(closing_deal_id, opening_lot_id)`.

#### `position_snapshots`

```text
snapshot_id, snapshot_at, account_id, code, position_id, market,
position_side, quantity, sellable_quantity, current_price,
market_value, cost_price, diluted_cost, average_cost,
broker_unrealized_pnl, broker_realized_pnl, broker_pnl_value,
pnl_ratio, currency, raw_payload_json
```

Unique key: `(snapshot_at, account_id, position_id)`.

#### `account_snapshots`

```text
snapshot_id, snapshot_at, account_id, market, currency,
net_assets, cash, securities_market_value, buying_power,
raw_payload_json
```

#### `pnl_reconciliation`

```text
snapshot_id, account_id, code, currency,
broker_unrealized_pnl, local_open_lot_unrealized_pnl,
broker_realized_pnl, fifo_estimated_realized_pnl,
unrealized_difference, realized_difference, status, notes
```

## 7. Synchronization design

### Initial backfill

Historical-deal defaults are insufficient for a portfolio that has existed
longer than 90 days. The first run must require:

```dotenv
PNL_HISTORY_START=2024-01-01
```

The sync breaks the interval into 60-day windows (deliberately below the
documented 90-day default window), queries each market/account, and upserts by
deal ID. It records the last successful window so interruption is resumable.

If the configured start date is later than the first opening fill of a still
open position, the FIFO ledger is incomplete. In that case:

- Moomoo broker unrealized P&L remains valid for monitoring;
- local cost/realized values are labelled `incomplete_history`; and
- the report tells the user to backfill from an earlier date or seed opening
  lots from a broker statement.

### Incremental sync

On each daily run:

1. start at the last completed fill timestamp minus a 48-hour overlap;
2. query fills through the current time in explicit windows;
3. deduplicate by `(account_id, deal_id)`;
4. acquire refreshed positions and account data;
5. write all rows in one SQLite transaction;
6. rebuild only affected lots/instruments; and
7. write reconciliation outcomes.

The overlap handles delayed report availability and idempotency handles
duplicate retrieval.

### Snapshot time

Use the existing 08:00 Asia/Kuala_Lumpur scheduled workflow initially. It is
after the normal US session and before the Malaysia session. The report labels
each snapshot with the exact OpenD retrieval timestamp and does not claim a
single common market close.

## 8. Reconciliation rules

### Position reconciliation

For each open instrument/side:

```text
sum(local remaining lot quantity) == broker position quantity
```

If not equal, mark `quantity_mismatch` and do not present local realized P&L
as final for that instrument.

### Unrealized reconciliation

When local open-lot price and broker nominal/current price are comparable:

```text
local unrealized = broker market value − sum(remaining quantity × opening price)
difference = broker unrealized P&L − local unrealized
```

Use a configurable currency-level tolerance, initially `0.01`. Differences may
come from fees, diluted-cost conventions, corporate actions, or rounding.

### Realized reconciliation

```text
difference = broker realized P&L − FIFO estimated realized P&L
```

Do not automatically “fix” a difference. Report one of:

- `matched` — within tolerance;
- `incomplete_history` — local fill history cannot support comparison;
- `expected_method_difference` — e.g. fees/cost methodology;
- `investigate` — quantity, currency, or unexplained P&L mismatch.

## 9. Report design

### Daily summary

```text
Portfolio P&L Snapshot — 2026-08-17 08:00 MYT

Open positions: 12
Unrealized P&L: USD +1,240.22 | MYR -315.40
Realized P&L since 2026-01-01: USD +540.11 | MYR +120.50
Reconciliation: 10 matched, 2 need review
```

Currency totals remain separate in Phase 2. Summing USD and MYR without a
timestamped FX policy would be misleading.

### Position table

| Code | Name | Qty | Cost | Market value | Unrealized P&L | P&L % | Source | Reconciliation |
|---|---:|---:|---:|---:|---:|---:|---|---|

### Realized activity table

| Close date | Code | Qty closed | FIFO estimated realized P&L | Broker realized P&L | Status |
|---|---|---:|---:|---:|---|

### Exceptions

- incomplete history;
- unmatched quantity;
- missing currency;
- broker/local P&L difference above tolerance;
- API availability or stale snapshot.

No account IDs, account values beyond selected totals, raw fills, or personal
data are sent to an LLM. The P&L calculation and report are deterministic.

## 10. CLI and scheduling

Add a separate non-interactive command:

```text
tradingagents-pnl doctor
tradingagents-pnl backfill --from 2024-01-01
tradingagents-pnl sync
tradingagents-pnl report --as-of 2026-08-17
tradingagents-pnl reconcile --as-of 2026-08-17
```

`sync` can be called from the existing daily LaunchAgent before the news and
sentiment pipeline, but it should first be deployed and tested as its own
LaunchAgent. This keeps accounting failures isolated from the research email.

Suggested Phase 2 schedule:

```text
07:45 MYT  P&L sync and reconciliation
08:00 MYT  Phase 1 news/sentiment briefing, optionally embedding P&L summary
```

## 11. Configuration

```dotenv
# Phase 2 — read-only P&L tracking
PNL_RUNTIME_DIR=~/.tradingagents/pnl
PNL_HISTORY_START=2024-01-01
PNL_SYNC_OVERLAP_HOURS=48
PNL_RECONCILIATION_TOLERANCE=0.01
PNL_INCLUDE_US=true
PNL_INCLUDE_MY=true
PNL_ACCOUNT_ID=<optional explicit account ID>
PNL_REFRESH_POSITIONS=true
```

`PNL_ACCOUNT_ID` follows the same account-selection policy as Phase 1: select
the sole eligible real account automatically; fail if multiple accounts are
eligible and no explicit ID is provided.

## 12. Safety, correctness, and privacy

- Use `TrdEnv.REAL` only for historical-fill P&L, because Moomoo documents
  historical deal queries as live-trading only.
- Use `acc_id`, never `acc_index`; Moomoo recommends account ID because account
  indexes can change when accounts are added or closed. [Get Historical
  Deals](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html)
- Store secrets only in `.env` mode `0600`; do not write them to reports/logs.
- Keep raw broker payloads local under the private runtime directory.
- Never send raw account values, account IDs, or fill-level history to LLMs.
- Treat all records as financial monitoring information, not a broker
  statement or tax document.

## 13. Testing and acceptance criteria

### Tests

- mocked OpenD position, fill, account, and cash-flow responses;
- idempotent fill upsert and 48-hour overlap;
- FIFO long, partial sale, multiple lots, short/cover, and same-day fills;
- quantity and P&L reconciliation tolerances;
- incomplete-history detection;
- multi-currency totals remain separate;
- no provider module exposes a trading/unlock method;
- SQLite migration and restart/resume tests;
- report snapshot tests with sanitized errors.

### Acceptance criteria

1. An initial backfill can be resumed safely and stores each deal once.
2. Every displayed unrealized P&L has an OpenD snapshot timestamp and broker
   source value.
3. Every local realized P&L amount can be traced to opening/closing deal IDs.
4. Broker/local differences are visible rather than silently hidden.
5. No deposit, withdrawal, or FX transfer is labelled investment P&L.
6. USD and MYR are never summed without an explicit later FX policy.
7. A data/API failure never creates fabricated P&L; it produces a stale or
   failed status with the last successful snapshot retained.
8. Tests run without OpenD, network access, Gmail, or an LLM.

## 14. Delivery sequence

1. Extend the read-only provider with historical fills, position snapshots,
   account funds, and cash-flow methods.
2. Create the P&L database, migrations, fill sync, and initial backfill CLI.
3. Implement deterministic FIFO lots and reconciliation.
4. Add HTML/JSON report and manual review commands.
5. Deploy a separate P&L LaunchAgent and validate it against a known broker
   statement period.
6. Only after reconciliation is trusted, embed the summarized P&L section in
   the Phase 1 daily research email.
