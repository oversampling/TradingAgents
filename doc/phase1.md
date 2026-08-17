# Phase 1 Technical Design: Daily Watchlist News and Sentiment Briefing

## 1. Objective

Build a read-only scheduled workflow that:

1. connects to the locally running Moomoo OpenD service;
2. reads securities from the user's Moomoo watchlist and, optionally, current portfolio positions;
3. runs only TradingAgents' News and Sentiment analysts for each eligible security;
4. produces one consolidated HTML report;
5. emails the report after each configured trading day; and
6. preserves enough state and logs to diagnose partial or failed runs.

Phase 1 is a research and notification system. It must not unlock trading, submit orders, or modify the Moomoo watchlist.

## 2. Scope

### In scope

- Moomoo OpenD connectivity through `127.0.0.1:11111` by default.
- Read-only retrieval of one configurable watchlist group.
- Optional union with non-zero positions from the selected Moomoo account.
- US and Malaysia securities.
- Moomoo-to-TradingAgents ticker normalization.
- TradingAgents Sentiment Analyst and News Analyst only.
- One consolidated daily HTML email.
- Local HTML and JSON artifacts for every run.
- SQLite run history and per-symbol status.
- Trading-calendar checks and idempotent scheduling.
- macOS `launchd` deployment using the pattern proven in QuantLysis.
- Manual dry-run and real email-test commands.

### Out of scope

- Fundamentals, technical analysis, research debate, trader, risk, and portfolio-manager nodes.
- 10-Q/10-K retrieval.
- Trading recommendations or automated order execution.
- Watchlist modification.
- Intraday alerts.
- Portfolio allocation or concentration analysis.
- Attachments, SMS, Slack, or mobile push.
- Distributed execution or a hosted server.

## 3. Reused patterns from QuantLysis

The design should reuse proven behavior from `/Users/jinyee/Documents/QuantLysis` rather than importing that application as a runtime dependency.

### OpenD adapter

Reference: `src/quantlysis/providers/moomoo.py`

Reuse these design properties:

- lazy import of the `moomoo` SDK;
- `OpenQuoteContext` for watchlists;
- `OpenSecTradeContext` only when positions are enabled;
- `SecurityFirm.FUTUMY`;
- context managers that always call `close()`;
- `RET_OK` validation with actionable errors;
- allowlisted, read-only methods only;
- US and Malaysia market filtering;
- explicit account selection when multiple eligible accounts exist.

Phase 1 should copy or adapt only the necessary read models and provider code into TradingAgents. It should not add trade-unlock or order methods.

### Email delivery

Reference: `src/quantlysis/mailer.py`

Reuse:

- Gmail SMTP over SSL at `smtp.gmail.com:465`;
- `EmailMessage` with plain-text fallback and HTML alternative;
- Gmail app password from `.env`;
- a 20-second connection timeout;
- a dedicated `MailDeliveryError` that does not expose credentials.

### Scheduler deployment

References:

- `scripts/deploy.sh`
- `config/com.quantlysis.daily.plist`
- the Scheduling section in the QuantLysis README

Use the same private production layout under `~/Library/Application Support/`, isolated virtual environment, `.env` mode `0600`, LaunchAgent, explicit stdout/stderr logs, deploy-time health check, and `launchctl kickstart` test flow.

## 4. Important TradingAgents constraint

Passing only two selected analysts does not currently mean “run only two analysts.” This code:

```python
TradingAgentsGraph(selected_analysts=("social", "news"))
```

selects the two analyst nodes, but `GraphSetup.setup_graph()` still connects the last analyst to:

```text
Bull Researcher
  -> Bear Researcher
  -> Research Manager
  -> Trader
  -> risk debate
  -> Portfolio Manager
```

That full path is unnecessary for a news briefing and would increase runtime and API cost.

Phase 1 must add an analyst-only graph mode.

### Proposed API

```python
graph = TradingAgentsGraph(
    selected_analysts=("social", "news"),
    execution_mode="analysts_only",
    config=config,
)

final_state = graph.propagate_reports(
    ticker="INTC",
    trade_date="2026-08-14",
    asset_type="stock",
)
```

`execution_mode` values:

- `full`: existing behavior and default for backward compatibility.
- `analysts_only`: the final selected analyst's clear node connects directly to `END`.

`propagate_reports()` should return the final state without requiring `final_trade_decision`, writing trading memory, calculating realized returns, or invoking signal processing.

This preserves the existing trading workflow while providing a correct, inexpensive path for scheduled research.

## 5. Proposed architecture

```text
macOS LaunchAgent
        |
        v
daily-news CLI command
        |
        +--> acquire single-run lock
        +--> create SQLite run record
        +--> confirm applicable trading session
        |
        v
MoomooReadOnlyProvider
        |
        +--> configured watchlist group
        +--> optional US/MY positions
        |
        v
normalize + deduplicate instruments
        |
        v
bounded sequential analysis
        |
        +--> Sentiment Analyst
        |      + Yahoo Finance news
        |      + StockTwits
        |      + Reddit
        |
        +--> News Analyst
               + ticker news
               + global news
               + FRED (optional)
               + Polymarket (optional)
        |
        v
persist per-symbol results and failures
        |
        v
render consolidated HTML + JSON
        |
        +--> save locally
        +--> send one Gmail message
        |
        v
complete SQLite run record and release lock
```

## 6. Package layout

Proposed additions:

```text
tradingagents/daily_briefing/
├── __init__.py
├── cli.py                 # non-interactive scheduled entry point
├── config.py              # validated environment-backed settings
├── models.py              # immutable provider/run/report models
├── pipeline.py            # orchestration and partial-failure policy
├── market_calendar.py     # US/MY session eligibility
├── symbol_mapper.py       # Moomoo -> TradingAgents symbols
├── reporting.py           # consolidated HTML renderer
├── mailer.py              # Gmail SMTP delivery
├── storage.py             # SQLite schema and operations
└── providers/
    └── moomoo.py          # strictly read-only OpenD adapter

config/
└── com.tradingagents.daily-news.plist

scripts/
└── deploy-daily-news.sh

tests/
├── test_daily_briefing_config.py
├── test_daily_briefing_moomoo.py
├── test_daily_briefing_pipeline.py
├── test_daily_briefing_reporting.py
├── test_daily_briefing_storage.py
├── test_daily_briefing_symbols.py
└── test_analysts_only_graph.py
```

Add a console script without changing the existing interactive command:

```toml
[project.scripts]
tradingagents = "cli.main:app"
tradingagents-daily = "tradingagents.daily_briefing.cli:main"
```

## 7. Moomoo integration

### Connection

Use:

```python
moomoo.OpenQuoteContext(
    host=settings.moomoo_host,
    port=settings.moomoo_port,
    security_firm=moomoo.SecurityFirm.FUTUMY,
)
```

OpenD must already be running and logged in. The scheduler must not start, log in to, or unlock OpenD.

### Watchlist retrieval

1. Call `get_user_security_group(UserSecurityGroupType.ALL)` for diagnostics.
2. Call `get_user_security(settings.moomoo_watchlist_group)`.
3. Keep supported market prefixes only.
4. Reject missing/empty codes.
5. Close the quote context in `finally`.

Default group:

```dotenv
MOOMOO_WATCHLIST_GROUP=portfolio
```

### Optional positions

When `INCLUDE_PORTFOLIO_POSITIONS=true`:

1. discover eligible real, active, normal accounts;
2. require `MOOMOO_ACCOUNT_ID` if selection is ambiguous;
3. query US and MY positions;
4. include positions with quantity greater than zero; and
5. union them with the watchlist by canonical symbol.

No position quantities, costs, or account values need to enter the LLM prompt or email in Phase 1. The position source is used only to ensure held securities are monitored. The report may show a boolean `Holding` badge.

## 8. Symbol normalization

Moomoo and Yahoo Finance use different formats.

Initial mapping:

| Moomoo | TradingAgents/Yahoo | Market |
|---|---|---|
| `US.INTC` | `INTC` | US |
| `US.BRK.B` | `BRK.B` | US |
| `MY.1155` | `1155.KL` | Malaysia |

Rules:

```python
if code.startswith("US."):
    ticker = code.removeprefix("US.")
elif code.startswith("MY."):
    ticker = code.removeprefix("MY.") + ".KL"
else:
    unsupported
```

The mapper must return both the original broker code and normalized ticker. Deduplicate case-insensitively by normalized ticker while merging `watchlist` and `holding` source flags.

Unsupported symbols must be recorded as skipped, not silently discarded.

## 9. Trading-day semantics

“Every trading day” is ambiguous for a mixed US/Malaysia watchlist because the exchanges have different holidays and close in different Malaysia calendar windows.

Phase 1 definition:

- one consolidated run at a configured Malaysia time on weekdays;
- determine the most recently completed session independently for each market;
- analyze a security only when its market has a completed session not already processed;
- skip a market on its holiday;
- send no email when no monitored market has a new completed session;
- store the market session date separately from the scheduler's local run date.

Recommended schedule:

```dotenv
REPORT_TIMEZONE=Asia/Kuala_Lumpur
REPORT_TIME=08:00
```

At 08:00 Malaysia time, the prior US session has normally completed. For Malaysia securities, the latest completed session is generally the previous local trading day. This creates one predictable morning briefing.

Use an exchange-calendar library rather than weekday-only checks. Suggested calendars:

- XNYS or XNAS for US securities;
- XKLS for Bursa Malaysia.

The exact dependency must be validated against supported calendar names during implementation. The calendar result, not the wall-clock date, becomes `trade_date` passed to TradingAgents.

## 10. Analysis execution

### Per-symbol flow

For each eligible symbol:

```python
config = DEFAULT_CONFIG.copy()
config.update({
    "checkpoint_enabled": False,
    "results_dir": settings.runtime_results_dir,
    "data_cache_dir": settings.runtime_cache_dir,
})

graph = TradingAgentsGraph(
    selected_analysts=("social", "news"),
    execution_mode="analysts_only",
    config=config,
)

state = graph.propagate_reports(
    normalized_ticker,
    session_date.isoformat(),
    asset_type="stock",
)
```

Capture only:

- `sentiment_report`;
- `news_report`;
- resolved company identity;
- source market and Moomoo code;
- session date;
- analysis duration;
- success/failure status.

Do not call `save_reports()` because its tree assumes a complete trading workflow. The daily pipeline owns a purpose-built consolidated report.

### LLM reuse

Constructing `TradingAgentsGraph` per ticker repeatedly creates provider clients and graph objects. Phase 1 should introduce an analyst-only runner that can be reused across symbols while resetting state per invocation.

Safe initial execution policy:

- sequential processing;
- configurable maximum symbols per run;
- no concurrent LLM calls initially;
- SDK retry budget through `TRADINGAGENTS_LLM_MAX_RETRIES`;
- continue to the next symbol after an isolated symbol failure.

Concurrency can be considered after rate-limit and cost measurements exist.

### Cost controls

```dotenv
DAILY_MAX_SYMBOLS=10
DAILY_LLM_PROVIDER=openai
DAILY_QUICK_MODEL=gpt-5.6-luna
DAILY_GLOBAL_NEWS_MODE=once
```

The current News Analyst may retrieve identical global news, macro indicators, and prediction markets for every ticker. Phase 1 should fetch shared global context once per run and reuse it across symbol prompts, while ticker-specific news remains per symbol. This is an important cost and latency optimization.

If shared-context refactoring is deferred, the maximum symbol count must be conservative and usage measured before enabling a large watchlist.

## 11. Report design

Generate one HTML email with:

### Header

- generation timestamp and timezone;
- covered market session dates;
- successful, failed, and skipped counts;
- data-source health summary;
- explicit “research only, not financial advice” notice.

### Executive summary

- strongest positive sentiment;
- strongest negative sentiment;
- highest-confidence reports;
- symbols with material news catalysts;
- symbols with unavailable or low-confidence social data.

Phase 1 may compute this summary deterministically from structured sentiment fields and report metadata. It should not require another LLM call.

### Per-symbol section

```text
INTC — Intel Corporation
Market: US | Session: 2026-08-14 | Holding: Yes
Sentiment: Moderately Bearish (-0.42) | Confidence: Medium

Sentiment report
...

News report
...
```

### Failure appendix

List symbols that failed, the failed stage, and a sanitized error message. Never include API keys, Gmail passwords, account IDs, or complete stack traces in email.

### Artifacts

```text
<runtime>/reports/2026-08-15/
├── daily-news-2026-08-15.html
├── daily-news-2026-08-15.json
└── symbols/
    ├── INTC.json
    └── 1155.KL.json
```

Write artifacts atomically using temporary files followed by `replace()`.

## 12. Email delivery

Configuration:

```dotenv
GMAIL_ADDRESS=sender@example.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
REPORT_RECIPIENT=recipient@example.com
```

Email subject:

```text
TradingAgents Daily News & Sentiment — 2026-08-15
```

Delivery happens only after the HTML and JSON reports are durably saved. If SMTP fails, the run is marked `report_ready_email_failed`; rerunning with `--send-only <run-id>` must resend the existing artifact without paying for another analysis.

## 13. Persistence and idempotency

Use SQLite rather than relying only on files.

### Tables

#### `daily_runs`

```text
id
scheduled_local_date
started_at
completed_at
status
us_session_date
my_session_date
watchlist_group
symbol_count
success_count
failure_count
report_html_path
report_json_path
email_sent_at
error_summary
```

#### `daily_symbol_results`

```text
id
run_id
moomoo_code
normalized_ticker
company_name
market
session_date
is_watchlist
is_holding
status
sentiment_band
sentiment_score
sentiment_confidence
sentiment_report
news_report
duration_seconds
error_stage
error_message
```

Unique constraints:

- one successful daily run per pair of market session dates and watchlist group;
- one symbol result per run and normalized ticker.

### Single-run lock

Use a PID/file lock in the private runtime directory. If a previous process holds the lock, exit successfully with a clear log message. This prevents duplicate email and concurrent OpenD access when a delayed run overlaps a manual trigger.

## 14. Configuration

Use this minimum production `.env` (stored outside the repository):

```dotenv
MOOMOO_HOST=127.0.0.1
MOOMOO_PORT=11111
MOOMOO_WATCHLIST_GROUP=portfolio
MOOMOO_ACCOUNT_ID=<your read-only account id>
INCLUDE_PORTFOLIO_POSITIONS=true
DAILY_MAX_SYMBOLS=10
DAILY_RUNTIME_DIR=/Users/jinyee/Library/Application Support/TradingAgentsDaily/data
REPORT_TIMEZONE=Asia/Kuala_Lumpur
REPORT_TIME=08:00
US_CALENDAR=XNYS
MY_CALENDAR=XKLS
SEND_PARTIAL_REPORTS=true
ALERT_ON_FULL_FAILURE=true
GMAIL_ADDRESS=<sender@gmail.com>
GMAIL_APP_PASSWORD=<gmail-app-password>
REPORT_RECIPIENT=<recipient@example.com>
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.6-luna
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.6-luna
```

`MOOMOO_ACCOUNT_ID` is optional only when OpenD finds exactly one eligible real
account shared by US and Malaysia. Set it explicitly whenever multiple accounts
exist; the job otherwise fails rather than silently selecting one.

## 15. Full `launchctl` execution flow

The scheduler is **`launchd` / `launchctl`** (not cron). The LaunchAgent runs
in the logged-in macOS user session, so Moomoo OpenD must already be running
and logged in under the same user. It never starts OpenD, unlocks trading, or
submits orders.

### 15.1 What happens at 08:00

```text
launchd StartCalendarInterval (08:00 Asia/Kuala_Lumpur system time)
  -> runs TradingAgentsDaily/.venv/bin/tradingagents-daily --env-file ... run
  -> loads and validates non-secret configuration
  -> obtains daily.lock (otherwise exits: another run is active)
  -> determines latest completed XNYS and XKLS sessions
  -> checks SQLite: skip sessions already fully reported
  -> OpenD: read portfolio watchlist + optional positive positions
  -> normalizes US./MY. broker codes and caps the union at 10 symbols
  -> for each eligible market/session, runs analyst-only Social -> News graph
  -> persists each result immediately in SQLite
  -> writes HTML and JSON artifacts atomically
  -> sends one Gmail HTML report (or preserves artifact if delivery fails)
  -> records final status, releases daily.lock, exits
```

The two analyst nodes may call their existing data sources (Yahoo Finance,
StockTwits, Reddit, ticker/global news, optional macro and prediction-market
tools). Individual ticker failures remain in the report failure appendix and
do not stop the other tickers. A failure before analysis triggers a sanitized
operational email when `ALERT_ON_FULL_FAILURE=true`.

### 15.2 First installation

Run these commands from this repository. They create a private deployed copy,
an isolated virtual environment and a user-level LaunchAgent:

```zsh
mkdir -p "$HOME/Library/Application Support/TradingAgentsDaily"
chmod +x scripts/deploy-daily-news.sh
scripts/deploy-daily-news.sh
```

The installer copies the repository's `.env` to the private production path on
every deploy and sets mode `0600`. Update the repository `.env`, then rerun the
installer; do not edit the deployed copy because it will be replaced.

The installer reuses an existing deployed virtual environment. This is
intentional: recreating an existing macOS venv can fail when its `include/`
directory was created with restrictive permissions. If the existing venv is
actually broken (for example, its `bin/python` is missing), remove only that
specific deployed venv and rerun the installer:

```zsh
rm -rf "$HOME/Library/Application Support/TradingAgentsDaily/.venv"
scripts/deploy-daily-news.sh
```

The deploy script performs this exact `launchctl` sequence after copying the
validated plist to `~/Library/LaunchAgents/`:

```zsh
launchctl bootout "gui/$(id -u)/com.tradingagents.daily-news" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.tradingagents.daily-news.plist"
launchctl enable "gui/$(id -u)/com.tradingagents.daily-news"
```

The installed plist is intentionally absolute-path based. Its execution
identity and locations are:

| Item | Value |
|---|---|
| Label | `com.tradingagents.daily-news` |
| Binary | `~/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily` |
| App copy | `~/Library/Application Support/TradingAgentsDaily/app` |
| Env file | `~/Library/Application Support/TradingAgentsDaily/.env` |
| stdout | `~/Library/Application Support/TradingAgentsDaily/logs/daily-news.out.log` |
| stderr | `~/Library/Application Support/TradingAgentsDaily/logs/daily-news.err.log` |
| Runtime DB/artifacts | path selected by `DAILY_RUNTIME_DIR` |

### 15.3 Verify before scheduling a real email

1. Start and log in to Moomoo OpenD.
2. Check connectivity and the configured watchlist, without LLM or email use:

```zsh
"$HOME/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily" \
  --env-file "$HOME/Library/Application Support/TradingAgentsDaily/.env" doctor
```

3. Inspect the exact normalized, bounded symbol universe:

```zsh
"$HOME/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily" \
  --env-file "$HOME/Library/Application Support/TradingAgentsDaily/.env" symbols
```

4. Run one real analysis but save artifacts only (no email):

```zsh
"$HOME/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily" \
  --env-file "$HOME/Library/Application Support/TradingAgentsDaily/.env" run --no-send --force
```

5. After checking the HTML artifact, send a saved run without re-running LLMs:

```zsh
"$HOME/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily" \
  --env-file "$HOME/Library/Application Support/TradingAgentsDaily/.env" send-only <run-id>
```

### 15.4 Test the installed LaunchAgent

`kickstart` runs the same command launchd will use. It may use LLM credits and
send an email, so use it only after the no-send test above:

```zsh
launchctl kickstart -k "gui/$(id -u)/com.tradingagents.daily-news"
sleep 5
launchctl print "gui/$(id -u)/com.tradingagents.daily-news"
tail -n 100 "$HOME/Library/Application Support/TradingAgentsDaily/logs/daily-news.out.log"
tail -n 100 "$HOME/Library/Application Support/TradingAgentsDaily/logs/daily-news.err.log"
```

`launchctl print` shows `last exit code`. An exit code of `0` plus a completed
or intentionally skipped SQLite run is healthy. A duplicate-session skip is
normal and is not an error.

### 15.5 Update/reload after code or plist changes

Edit the source checkout, then redeploy. This synchronizes the deployed app
and performs `bootout -> bootstrap -> enable`, so it also reloads plist edits:

```zsh
scripts/deploy-daily-news.sh
launchctl print "gui/$(id -u)/com.tradingagents.daily-news"
```

Do not edit the deployed `app/` copy: the next deploy replaces it. Edit the
repository and redeploy instead.

### 15.6 Disable, re-enable, and remove

Temporarily disable future scheduled starts while retaining all data:

```zsh
launchctl disable "gui/$(id -u)/com.tradingagents.daily-news"
```

Re-enable it:

```zsh
launchctl enable "gui/$(id -u)/com.tradingagents.daily-news"
```

Unload and remove only the scheduler (reports and SQLite data remain):

```zsh
launchctl bootout "gui/$(id -u)/com.tradingagents.daily-news" 2>/dev/null || true
rm "$HOME/Library/LaunchAgents/com.tradingagents.daily-news.plist"
```

The final `rm` removes one explicitly named plist and is recoverable by
rerunning `scripts/deploy-daily-news.sh`.

Proposed `.env.example` additions:

```dotenv
# Moomoo OpenD
MOOMOO_HOST=127.0.0.1
MOOMOO_PORT=11111
MOOMOO_WATCHLIST_GROUP=All
MOOMOO_ACCOUNT_ID=
INCLUDE_PORTFOLIO_POSITIONS=true

# Daily report
REPORT_TIMEZONE=Asia/Kuala_Lumpur
REPORT_TIME=08:00
REPORT_RECIPIENT=
DAILY_MAX_SYMBOLS=25
DAILY_GLOBAL_NEWS_MODE=once

# Gmail SMTP
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=

# LLM
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.6-luna
TRADINGAGENTS_LLM_MAX_RETRIES=6

# Optional analyst data
FRED_API_KEY=

# Private production storage
DAILY_DATABASE_URL=sqlite:///data/daily-news.db
DAILY_REPORTS_DIR=reports
DAILY_CACHE_DIR=cache
```

Validate:

- `.env` permissions are `0600`;
- valid port, timezone, clock time, positive maximum symbols;
- sender and recipient resemble email addresses;
- Gmail app password is required only when sending;
- OpenAI/provider key is present before analysis;
- Moomoo account ID is positive when configured.

Never log secret values.

## 16. CLI contract

```bash
# Validate configuration, OpenD, Gmail configuration, and output directories
tradingagents-daily doctor

# Show normalized symbols without calling an LLM or sending email
tradingagents-daily symbols

# Run analysis, save reports, and send one email
tradingagents-daily run

# Run analysis and save reports, but do not email
tradingagents-daily run --no-send

# Ignore calendar/idempotency for an explicit manual test
tradingagents-daily run --force

# Resend a saved report without rerunning analysis
tradingagents-daily send-only <run-id>
```

`symbols` must be the first operational test because it proves OpenD connection, watchlist selection, account selection, and ticker normalization without spending LLM credits.

## 17. Failure policy

| Failure | Behavior |
|---|---|
| OpenD unavailable | Fail run before LLM calls; send optional operational alert if SMTP config is valid. |
| Watchlist group missing | Fail with available group names in local logs. |
| Empty watchlist and no positions | Complete as no-op; do not send normal report. |
| Unsupported Moomoo symbol | Mark symbol skipped and continue. |
| One ticker data/LLM failure | Record failed symbol and continue. |
| Provider 429 | Honor SDK retries; record symbol failure after exhaustion. |
| Gmail failure | Preserve reports; mark email failure; allow `send-only`. |
| SQLite/report write failure | Fail run; do not send an email claiming success. |
| No new market session | Exit successfully without analysis or email. |

A report should still be sent when some symbols fail, provided at least one symbol succeeds. The email must prominently state that it is partial.

## 18. Security and privacy

- Keep Moomoo operations strictly read-only.
- Do not include account ID, quantities, costs, cash, or portfolio value in LLM prompts.
- Store `.env` as mode `0600`; production directories as `0700`.
- Do not put secrets in the LaunchAgent plist.
- Do not log environment dumps, request authorization headers, or full email messages.
- Sanitize exception text before inserting it into email.
- Treat news/social content as untrusted text and keep it inside delimited prompt sections.
- Cap article/message counts and report sizes.
- Use a dedicated Gmail app password, not the account password.

## 19. macOS deployment

Use a private production location:

```text
/Users/jinyee/Library/Application Support/TradingAgentsDaily/
├── .env
├── .venv/
├── data/daily-news.db
├── cache/
├── reports/
└── logs/
```

Recommended LaunchAgent label:

```text
com.tradingagents.daily-news
```

The LaunchAgent should execute:

```text
/Users/jinyee/Library/Application Support/TradingAgentsDaily/.venv/bin/tradingagents-daily run --send
```

Use `StartCalendarInterval` for the configured local time and retain calendar/idempotency checks inside the application. Scheduler timing alone is insufficient for market holidays, sleep/wake behavior, or manual retries.

Prefer a user LaunchAgent over Unix `cron` on macOS because:

- OpenD is a logged-in desktop-user service;
- LaunchAgents run in the user's GUI session;
- `launchctl` provides status and last-exit information;
- stdout/stderr can be routed to stable log files; and
- missed runs after sleep are handled more predictably.

Runtime requirements:

- the user must be logged in;
- Moomoo OpenD must be running and logged in;
- the Mac must be awake or the job will run after wake;
- internet access and provider credits must be available.

## 20. Observability

Use standard Python logging with one run ID in every record.

Log:

- application version;
- run ID and scheduler/manual trigger;
- OpenD connection status;
- group and symbol counts, not account values;
- normalized ticker mapping;
- per-symbol stage, duration, and status;
- provider retry exhaustion;
- artifact paths;
- SMTP delivery result;
- final run summary.

Do not log full LLM prompts by default because they contain large third-party content. A debug mode may save sanitized tool inputs and outputs under the private run directory.

## 21. Testing strategy

### Unit tests

- OpenD success/error handling with a fake SDK.
- Context closure on success and exception.
- watchlist-group filtering.
- account ambiguity and configured account selection.
- US/MY symbol mapping and deduplication.
- market-session determination across weekends and holidays.
- analyst-only graph terminates after the last selected analyst.
- analyst-only state contains news and sentiment but no trade decision.
- partial ticker failures.
- report escaping and secret redaction.
- Gmail MIME structure and delivery errors.
- SQLite idempotency and `send-only` behavior.

### Integration tests

- local OpenD watchlist retrieval, explicitly opt-in;
- one-symbol LLM run, explicitly opt-in and cost-bearing;
- Gmail delivery to the configured recipient, explicitly opt-in;
- LaunchAgent deployment and immediate kickstart.

### Regression tests

The existing full TradingAgents graph must retain its current node topology and outputs when `execution_mode="full"` or when the option is omitted.

## 22. Implementation sequence

### Milestone 1: Read-only universe

- configuration models;
- Moomoo provider;
- symbol mapper;
- `doctor` and `symbols` commands;
- mocked unit tests.

Exit criterion: the CLI prints the correct deduplicated ticker universe without LLM calls.

### Milestone 2: Analyst-only execution

- `execution_mode="analysts_only"`;
- `propagate_reports()`;
- no decision memory or signal processing;
- topology and regression tests.

Exit criterion: one ticker returns only `sentiment_report` and `news_report`.

### Milestone 3: Durable daily pipeline

- market calendars;
- SQLite run/result records;
- file lock;
- per-symbol partial-failure handling;
- HTML/JSON renderer.

Exit criterion: a manual run produces durable reports and can be safely retried without duplication.

### Milestone 4: Email and deployment

- Gmail mailer;
- `--send` and `send-only`;
- private production installer;
- LaunchAgent;
- operational documentation.

Exit criterion: an immediate production test reads OpenD, analyzes a small configured watchlist, saves artifacts, sends one email, and records success.

## 23. Phase 1 acceptance criteria

Phase 1 is complete when all of the following are true:

1. No code path can unlock trading, place orders, or modify a Moomoo watchlist.
2. `doctor` diagnoses OpenD, configuration, directories, provider credentials, and email readiness.
3. `symbols` shows the expected US/MY watchlist and held symbols without an LLM call.
4. Each eligible ticker runs only Sentiment and News analysts.
5. The full Bull/Bear/Trader/Risk/Portfolio path is not executed.
6. A market holiday does not create a duplicate analysis or email.
7. One ticker failure does not discard successful ticker reports.
8. A consolidated HTML and JSON report are saved atomically.
9. One HTML email is sent only after report persistence succeeds.
10. Failed email delivery can be retried without rerunning LLM analysis.
11. SQLite prevents duplicate successful runs for the same market sessions.
12. The LaunchAgent runs from a private production directory with private secrets.
13. Unit tests pass without OpenD, network access, Gmail, or paid LLM calls.
14. Existing TradingAgents full-analysis behavior and tests remain unchanged.

## 24. Confirmed Phase 1 deployment decisions

The following decisions were confirmed on 2026-08-17:

| Decision | Confirmed value |
|---|---|
| Moomoo watchlist group | `portfolio` |
| Include current holdings automatically | `true` |
| Maximum symbols per run | `10` |
| Schedule | `08:00 Asia/Kuala_Lumpur` |
| US market calendar | XNYS |
| Send a partial report when at least one ticker succeeds | `true` |
| Send a separate operational email when the entire run fails | `true` |

`portfolio` is treated as the exact Moomoo watchlist group name. The implementation should fail with a clear diagnostic if OpenD does not return a group with that exact name.
