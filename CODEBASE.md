# TradingAgents Codebase Guide

This document explains how the repository is organized, how an analysis moves through the system, where data and reports are stored, and where to make common changes.

## 1. What this project does

TradingAgents is a LangGraph-based, multi-agent market-analysis pipeline. A run accepts a ticker and analysis date, gathers several kinds of market evidence, asks specialized LLM agents to produce reports and debate them, and ends with a portfolio decision.

At a high level:

```text
CLI or Python API
    |
    v
TradingAgentsGraph
    |
    +--> Market Analyst --------> market report
    +--> Sentiment Analyst -----> sentiment report
    +--> News Analyst ----------> news report
    +--> Fundamentals Analyst --> fundamentals report
                                      |
                                      v
                          Bull/Bear research debate
                                      |
                               Research Manager
                                      |
                                   Trader
                                      |
                    Aggressive/Conservative/Neutral debate
                                      |
                              Portfolio Manager
                                      |
                         final BUY/HOLD/SELL decision
```

This is a research framework. It does not place real trades.

## 2. Main entry points

### Command-line interface

The installed command is defined in `pyproject.toml`:

```toml
[project.scripts]
tradingagents = "cli.main:app"
```

The CLI is a single Typer command, not a command with an `analyze` subcommand:

```bash
tradingagents
tradingagents --checkpoint
tradingagents --no-checkpoint
tradingagents --clear-checkpoints
```

Important CLI files:

- `cli/main.py`: prompts, live display, graph execution, logs, and report-saving prompts.
- `cli/utils.py`: provider/model selection and API-key prompts.
- `cli/models.py`: CLI-facing data models.
- `cli/stats_handler.py`: timing and token/tool statistics.
- `cli/config.py`: presentation-oriented CLI configuration.

The CLI collects the ticker, date, asset type, analyst selection, LLM provider, models, and research depth. It then constructs `TradingAgentsGraph` and runs `propagate()`.

### Python API

The primary programmatic entry point is:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["quick_think_llm"] = "gpt-5.6-luna"
config["deep_think_llm"] = "gpt-5.6-luna"

graph = TradingAgentsGraph(config=config)
final_state, decision = graph.propagate("INTC", "2026-08-10")
```

Use `graph.save_reports(final_state, "INTC")` to create the same report tree as the CLI.

## 3. Configuration lifecycle

### Environment loading

`tradingagents/__init__.py` loads `.env` and `.env.enterprise` when the package is imported. Existing process environment values take precedence.

### Defaults

`tradingagents/default_config.py` contains `DEFAULT_CONFIG`, including:

- LLM provider and quick/deep models.
- Debate and risk round counts.
- data-vendor choices.
- report, cache, checkpoint, and memory paths.
- benchmark mappings.
- temperature and provider-specific reasoning settings.

`TRADINGAGENTS_*` environment variables override selected defaults. Examples:

```dotenv
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.6-luna
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.6-luna
TRADINGAGENTS_OPENAI_REASONING_EFFORT=medium
TRADINGAGENTS_CHECKPOINT_ENABLED=true
TRADINGAGENTS_CACHE_DIR=/custom/cache/path
```

`tradingagents/dataflows/config.py` keeps the effective runtime configuration used by data tools. `TradingAgentsGraph.__init__()` calls `set_config()` so graph and tools share the same settings.

## 4. Graph construction and execution

### Orchestrator

`tradingagents/graph/trading_graph.py` contains `TradingAgentsGraph`, the main coordinator. Its constructor:

1. Applies configuration to the dataflow layer.
2. Creates cache and results directories.
3. Constructs quick- and deep-thinking LLM clients.
4. Creates LangGraph `ToolNode` collections.
5. Creates conditional routing and helper components.
6. Builds and compiles the workflow.

`propagate(ticker, trade_date, asset_type)` is the public execution method. It resolves old memory outcomes, optionally opens a SQLite checkpointer, then calls `_run_graph()`.

### State

`tradingagents/agents/utils/agent_states.py` defines `AgentState`. Important fields include:

- `messages`: LangChain/LangGraph message history.
- `company_of_interest`, `asset_type`, `trade_date`.
- `instrument_context`: deterministic ticker/company identity.
- Four analyst reports.
- Bull/bear debate state.
- Trader plan.
- Three-way risk debate state.
- Final trade decision.
- `past_context`: lessons from completed historical decisions.

`tradingagents/graph/propagation.py` creates the initial state and supplies LangGraph execution arguments, including the recursion limit.

### Graph topology

`tradingagents/graph/setup.py` builds the `StateGraph`.

The selected analysts run sequentially. Each analyst follows this loop:

```text
Analyst LLM
   |
   +-- emitted tool calls? --> analyst ToolNode --> Analyst LLM again
   |
   +-- no tool calls -------> clear temporary messages --> next analyst
```

After the final analyst:

1. Bull and Bear Researchers alternate for the configured number of rounds.
2. Research Manager judges the debate.
3. Trader creates an investment plan.
4. Aggressive, Conservative, and Neutral risk agents debate.
5. Portfolio Manager produces `final_trade_decision`.

`tradingagents/graph/conditional_logic.py` owns these routing decisions. `analyst_execution.py` maps analyst keys such as `market` and `fundamentals` to graph node names and report fields.

## 5. Agent roles

Agent factory functions live under `tradingagents/agents/`.

### Analysts

- `analysts/market_analyst.py`: price history, technical indicators, and verified market snapshot.
- `analysts/sentiment_analyst.py`: short-term market/social sentiment.
- `analysts/news_analyst.py`: company/global news, insiders, macro data, and prediction markets.
- `analysts/fundamentals_analyst.py`: company overview, statements, and SEC quarterly filings.

Analysts use the quick-thinking model because they repeatedly gather and summarize tool evidence.

### Research and decisions

- `researchers/bull_researcher.py`: builds the bullish case.
- `researchers/bear_researcher.py`: builds the bearish case.
- `managers/research_manager.py`: judges the investment debate.
- `trader/trader.py`: converts research into a trade plan.
- `risk_mgmt/*.py`: examines aggressive, conservative, and neutral risk postures.
- `managers/portfolio_manager.py`: makes the final portfolio decision.

Research Manager and Portfolio Manager use the deep-thinking model. Structured response schemas and Markdown conversion are defined in `agents/schemas.py` and `agents/utils/structured.py`.

## 6. Tools and data vendors

LangChain tool wrappers live in `tradingagents/agents/utils/*_tools.py`. They expose stable tool names to LLM agents while delegating actual retrieval to `tradingagents/dataflows/`.

### Vendor router

`tradingagents/dataflows/interface.py` provides `route_to_vendor()` and maps categories/methods to implementations.

Default categories include:

| Category | Default vendor | Examples |
|---|---|---|
| Core stock APIs | Yahoo Finance | OHLCV prices |
| Technical indicators | Yahoo Finance | RSI, MACD, moving averages |
| Fundamentals | Yahoo Finance | overview and statements |
| News | Yahoo Finance | company/global news |
| Macro | FRED | inflation, rates, employment |
| Prediction markets | Polymarket | market-implied event probabilities |

Alpha Vantage can replace Yahoo Finance for supported categories. A comma-separated vendor value creates an explicit fallback chain.

The shared error hierarchy in `dataflows/errors.py` distinguishes:

- no usable market data;
- vendor rate limiting;
- missing vendor configuration;
- other vendor failures.

Optional macro and prediction-market failures degrade gracefully. Core price and fundamental failures are treated more strictly.

### Main dataflow modules

- `y_finance.py`: stock information, statements, prices, and insider data.
- `stockstats_utils.py`: cached OHLCV and technical calculations.
- `yfinance_news.py`: Yahoo news retrieval and filtering.
- `reddit.py` and `stocktwits.py`: sentiment sources.
- `alpha_vantage_*.py`: Alpha Vantage implementations.
- `fred.py`: macroeconomic time series.
- `polymarket.py`: prediction-market context.
- `market_data_validator.py`: deterministic snapshot verification.
- `symbol_utils.py`: ticker normalization and asset-specific mappings.

## 7. SEC quarterly-filing pipeline

The repository includes a custom SEC EDGAR integration in `tradingagents/dataflows/sec_edgar.py`.

For stock analyses, `fundamentals_analyst.py` deterministically prefetches SEC context before the analyst's normal tool loop. This is deliberate: filing retrieval does not depend on whether the LLM decides to call a tool.

The pipeline:

1. Reads `SEC_USER_AGENT` from the environment.
2. Resolves ticker to SEC CIK using the SEC company-ticker map.
3. Loads the company's submissions history.
4. Filters to `10-Q` and `10-Q/A` filed on or before the selected analysis date.
5. Selects the newest filing for the latest two distinct reporting periods.
6. Downloads and caches each official primary filing.
7. Removes hidden inline-XBRL, scripts, styles, and excess whitespace.
8. Saves a clean text companion for humans and LLM input.
9. Fits key sections into the model context while retaining MD&A, risks, controls, legal proceedings, statements, and notes.

Configuration:

```dotenv
SEC_USER_AGENT="Your Name contact@example.com"
```

Default storage:

```text
~/.tradingagents/cache/sec_filings/
├── company_tickers.json
└── <CIK>/
    └── <ACCESSION>/
        ├── <primary-document>.htm
        └── <primary-document>.clean.txt
```

The `.htm` file is the official inline-XBRL source. The `.clean.txt` file is the readable extraction. Cache-write failures raise `SecEdgarCacheError` and stop the run; unavailable/non-US filings degrade to an explicit context message.

Current scope is quarterly `10-Q`/`10-Q/A`. Annual `10-K`, foreign `6-K`/`20-F`, and earnings-release `8-K` exhibits are not yet implemented.

## 8. LLM provider abstraction

`tradingagents/llm_clients/factory.py` selects a client implementation:

- OpenAI and OpenAI-compatible providers: `openai_client.py`.
- Anthropic: `anthropic_client.py`.
- Google: `google_client.py`.
- Azure OpenAI: `azure_client.py`.
- Amazon Bedrock: `bedrock_client.py`.

`openai_client.py` contains a provider registry covering OpenAI, xAI, DeepSeek, Qwen, GLM, MiniMax, OpenRouter, Ollama, NVIDIA, Groq, Mistral, Kimi, and generic OpenAI-compatible endpoints.

`model_catalog.py` drives CLI model choices. Custom model IDs remain supported when a model is missing from the curated list. `validators.py` warns about unknown models but permits them. `capabilities.py` and client-specific logic prevent unsupported parameters, such as reasoning effort, from reaching incompatible models.

## 9. Persistence and generated files

TradingAgents has several distinct persistence mechanisms.

### Decision memory

Default path:

```text
~/.tradingagents/memory/trading_memory.md
```

`agents/utils/memory.py` appends a successful decision as pending. On a later run for the same ticker, `TradingAgentsGraph` fetches realized ticker and benchmark returns, asks the reflection agent what worked, and atomically updates the entry. Recent same-ticker decisions and cross-ticker lessons are injected into future runs.

This is structured decision/reflection memory, not model training.

### Checkpoints

Checkpointing is opt-in. SQLite databases live under:

```text
~/.tradingagents/cache/checkpoints/<TICKER>.db
```

`graph/checkpointer.py` creates LangGraph SQLite savers. A checkpoint thread is keyed by ticker, date, analyst selection, debate depth, risk depth, and asset type. Successful completion clears the active thread's checkpoint.

### Internal state logs

`TradingAgentsGraph._log_state()` writes the completed graph state into the configured results directory. The CLI also creates `message_tool.log` for visible agent/tool activity.

### Human-readable reports

`tradingagents/reporting.py` writes:

```text
<report directory>/
├── 1_analysts/
│   ├── market.md
│   ├── sentiment.md
│   ├── news.md
│   └── fundamentals.md
├── 2_research/
├── 3_trading/
├── 4_risk/
├── 5_portfolio/
└── complete_report.md
```

The CLI asks whether and where to save this tree after a successful run.

## 10. Testing and quality checks

Tests are under `tests/` and use pytest markers declared in `pyproject.toml`:

- `unit`: fast and isolated.
- `integration`: external-service integration.
- `smoke`: quick end-to-end checks.

Run all tests:

```bash
source .venv/bin/activate
pytest -q
```

Run lint:

```bash
ruff check .
```

SEC-specific tests:

```bash
pytest -q tests/test_sec_edgar.py tests/test_fundamentals_filing_prefetch.py
```

Network access is mocked in unit tests. Live provider tests are skipped unless their credentials are configured.

## 11. Common extension points

### Add a new analyst

1. Create the agent factory under `tradingagents/agents/analysts/`.
2. Add its state report field to `AgentState`.
3. Register its execution metadata in `graph/analyst_execution.py`.
4. Add its tools in `TradingAgentsGraph._create_tool_nodes()`.
5. Add a factory mapping in `GraphSetup.setup_graph()`.
6. Add routing logic and report persistence.
7. Add graph-shape, reporting, and tool-loop tests.

### Add a new data vendor

1. Implement retrieval in `tradingagents/dataflows/`.
2. Raise the shared vendor errors where appropriate.
3. Register the implementation in `dataflows/interface.py`.
4. Update defaults/configuration documentation.
5. Add mocked router and formatting tests.

### Add a new fundamental document type

For example, to support 10-K:

1. Generalize `sec_edgar.list_filings()` to accept form families.
2. Select annual periods independently from quarterly periods.
3. Add annual-specific section extraction and context budgets.
4. Preserve the `filing_date <= trade_date` rule.
5. Include form metadata in the clean-text output/report.
6. Add amendment, historical-date, cache, and no-data tests.

## 12. Practical debugging map

If a run fails or produces weak output, inspect these layers in order:

1. **Environment:** `.env`, API keys, `SEC_USER_AGENT`.
2. **Installed code:** use editable installation during development:
   `pip install --no-deps --no-build-isolation -e .`.
3. **CLI log:** `message_tool.log` for tool calls and agent messages.
4. **Cache:** `~/.tradingagents/cache/` for filings/checkpoints/data.
5. **Analyst report:** `1_analysts/*.md` to identify which evidence was absent.
6. **Graph state:** configured results directory JSON state log.
7. **Focused tests:** run the test file closest to the failing component.

The editable install is important: a plain `pip install .` copies the package into the virtual environment. Later source edits will not affect the `tradingagents` executable until it is reinstalled. An editable install points the environment back to this working tree.

## 13. Files to read first

For a compact onboarding path, read these in order:

1. `tradingagents/default_config.py`
2. `tradingagents/graph/trading_graph.py`
3. `tradingagents/graph/setup.py`
4. `tradingagents/agents/utils/agent_states.py`
5. One analyst, such as `agents/analysts/fundamentals_analyst.py`
6. `tradingagents/dataflows/interface.py`
7. `tradingagents/reporting.py`
8. `cli/main.py`

Together, these files show configuration, orchestration, graph topology, state, agent/tool behavior, vendor routing, output generation, and the user-facing execution path.
