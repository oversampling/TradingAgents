import logging

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.dataflows.sec_edgar import (
    SecEdgarCacheError,
    get_quarterly_filing as fetch_quarterly_filing,
)

logger = logging.getLogger(__name__)

_FILING_CONTEXT_MARKER = "[SEC QUARTERLY FILING CONTEXT]"


def _has_filing_context(messages) -> bool:
    return any(
        isinstance(getattr(message, "content", None), str)
        and message.content.startswith(_FILING_CONTEXT_MARKER)
        for message in messages
    )


def _prefetch_filing_context(state):
    """Fetch SEC context exactly once before the analyst's tool-calling loop.

    Filing retrieval is deterministic for stocks instead of depending on the
    model to choose the tool. Failures remain context, not graph failures, so a
    foreign/private company can still be analyzed from structured fundamentals.
    """
    messages = state["messages"]
    if state.get("asset_type", "stock") != "stock" or _has_filing_context(messages):
        return None

    ticker = state["company_of_interest"]
    trade_date = state["trade_date"]
    try:
        filing_text = fetch_quarterly_filing(ticker, trade_date)
    except SecEdgarCacheError:
        logger.exception("Fatal: SEC quarterly filing could not be saved for %s", ticker)
        raise
    except Exception as exc:
        logger.warning("SEC quarterly filing unavailable for %s: %s", ticker, exc)
        filing_text = f"SEC quarterly filing unavailable: {exc}"

    return HumanMessage(
        content=(
            f"{_FILING_CONTEXT_MARKER}\n"
            "Treat the following as primary-source filing evidence. Cite its filing date, "
            "accession number, and official SEC URL in the report when available.\n\n"
            f"{filing_text}"
        )
    )


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements. SEC quarterly-filing context is attached automatically for stocks when available. Analyze the latest eligible 10-Q and its preceding quarter, cite the filing date, accession number, and official SEC URL, and distinguish filing evidence from your interpretation. If SEC filing data is unavailable, say so and continue with the structured fundamentals tools."
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        filing_context = _prefetch_filing_context(state)
        invoke_messages = state["messages"]
        if filing_context is not None:
            invoke_messages = [*invoke_messages, filing_context]

        result = chain.invoke(invoke_messages)

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": ([filing_context] if filing_context is not None else []) + [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
