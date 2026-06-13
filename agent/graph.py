"""
agent/graph.py
--------------
LangGraph ReAct agent with Langfuse observability injected.
Every LLM call and tool execution is traced automatically.
"""

import json
import time
from typing import Any, TypedDict, Annotated

import anthropic
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from agent.prompts import SYSTEM_PROMPT, HUMAN_DEBUG_TEMPLATE
from agent.tools import CLAUDE_TOOL_DEFINITIONS, TOOL_MAP
from config import get_logger, get_settings

logger = get_logger(__name__)


# -- Agent State ---------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    findings: list[dict]
    tool_calls_made: int
    start_time: float
    trace: Any  # AgentTrace instance passed through state


# -- Nodes ---------------------------------------------------------------------

def agent_node(state: AgentState) -> AgentState:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic.api_key)
    messages = _convert_messages(state["messages"])

    logger.info(
        "Agent node called",
        message_count=len(messages),
        tool_calls_so_far=state["tool_calls_made"],
    )

    response = client.messages.create(
        model=settings.anthropic.model,
        max_tokens=settings.anthropic.max_tokens,
        system=SYSTEM_PROMPT,
        tools=CLAUDE_TOOL_DEFINITIONS,
        messages=messages,
    )

    logger.info(
        "Claude response received",
        stop_reason=response.stop_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    # Record LLM call in Langfuse
    trace = state.get("trace")
    if trace:
        trace.record_llm_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=settings.anthropic.model,
            stop_reason=response.stop_reason,
        )

    ai_message = _anthropic_response_to_message(response)
    return {**state, "messages": [ai_message]}


def tool_node(state: AgentState) -> AgentState:
    last_message = state["messages"][-1]
    tool_messages = []
    new_findings = list(state["findings"])
    tool_calls = getattr(last_message, "tool_calls", []) or []
    trace = state.get("trace")

    for tool_call in tool_calls:
        tool_name = tool_call["name"]
        tool_input = tool_call["args"]
        tool_call_id = tool_call["id"]

        logger.info("Executing tool", tool=tool_name, input=tool_input)
        start = time.perf_counter()
        success = True
        result = {}

        try:
            fn = TOOL_MAP.get(tool_name)
            if fn is None:
                result = {"error": f"Unknown tool: {tool_name}"}
                success = False
            else:
                result = fn(**tool_input)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info("Tool executed", tool=tool_name, elapsed_ms=elapsed_ms)

            if isinstance(result, dict) and result.get("has_anomaly"):
                new_findings.append({"tool": tool_name, "result": result})

        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error("Tool execution failed", tool=tool_name, error=str(e))
            result = {"error": str(e)}
            success = False

        # Record tool call in Langfuse
        if trace:
            trace.record_tool_call(
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=result,
                elapsed_ms=elapsed_ms,
                success=success,
            )

        tool_messages.append(ToolMessage(
            content=json.dumps(result, default=str),
            tool_call_id=tool_call_id,
            name=tool_name,
        ))

    return {
        **state,
        "messages": tool_messages,
        "findings": new_findings,
        "tool_calls_made": state["tool_calls_made"] + len(tool_calls),
    }


# -- Routing -------------------------------------------------------------------

def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    if state["tool_calls_made"] >= 20:
        logger.warning("Tool call limit reached")
        return "end"
    return "tools" if tool_calls else "end"


# -- Graph Assembly ------------------------------------------------------------

def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")
    return graph.compile()


_graph = None

def get_agent():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# -- Public API ----------------------------------------------------------------

def run_agent(question: str, session_id: str | None = None) -> dict[str, Any]:
    """
    Run the agent. Wraps the full run in a Langfuse trace if configured.
    """
    from observability.langfuse import AgentTrace

    settings = get_settings()
    agent = get_agent()

    prompt = HUMAN_DEBUG_TEMPLATE.format(
        database=settings.snowflake.database,
        user_question=question,
    )

    with AgentTrace(question=question, session_id=session_id) as trace:
        initial_state: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "findings": [],
            "tool_calls_made": 0,
            "start_time": time.time(),
            "trace": trace,
        }

        logger.info("Agent run started", question=question)
        final_state = agent.invoke(initial_state)

        last_message = final_state["messages"][-1]
        answer = last_message.content if hasattr(last_message, "content") else str(last_message)
        if isinstance(answer, list):
            answer = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in answer
            )

        result = {
            "answer": answer,
            "findings": final_state["findings"],
            "tool_calls_made": final_state["tool_calls_made"],
            "elapsed_seconds": round(time.time() - initial_state["start_time"], 2),
            "status": "success",
        }

        return trace.finish(result)


# -- Message Format Helpers ----------------------------------------------------

def _convert_messages(messages: list) -> list[dict]:
    converted = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            converted.append({"role": "user", "content": str(msg.content)})

        elif isinstance(msg, AIMessage):
            content = []
            if msg.content:
                text = msg.content
                if isinstance(text, list):
                    content.extend(
                        {"type": "text", "text": block.get("text", "")} if isinstance(block, dict)
                        else {"type": "text", "text": str(block)}
                        for block in text
                    )
                else:
                    content.append({"type": "text", "text": str(text)})
            for tc in (msg.tool_calls or []):
                content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["args"],
                })
            if content:
                converted.append({"role": "assistant", "content": content})

        elif isinstance(msg, ToolMessage):
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                converted[-1]["content"].append({
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": str(msg.content),
                })
            else:
                converted.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": msg.tool_call_id, "content": str(msg.content)}]
                })
    return converted


def _anthropic_response_to_message(response) -> AIMessage:
    text_content = ""
    tool_calls = []
    for block in response.content:
        if block.type == "text":
            text_content = block.text
        elif block.type == "tool_use":
            tool_calls.append({"id": block.id, "name": block.name, "args": block.input})
    return AIMessage(content=text_content, tool_calls=tool_calls)
