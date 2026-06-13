"""
observability/langfuse.py
--------------------------
Wraps agent runs with Langfuse tracing.
Captures: token usage, latency, tool calls, cost estimate, accuracy scores.

If Langfuse is not configured (.env keys missing), all calls are no-ops
so the app works without it. Never blocks the agent.
"""

import time
from typing import Any
from functools import wraps

from config import get_logger, get_settings

logger = get_logger(__name__)

# Cost per 1M tokens (Claude Sonnet 4.5 pricing)
INPUT_COST_PER_1M  = 3.00
OUTPUT_COST_PER_1M = 15.00


def _get_langfuse():
    """Return a Langfuse client if configured, else None."""
    settings = get_settings()
    if not settings.langfuse.public_key or not settings.langfuse.secret_key:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=settings.langfuse.public_key,
            secret_key=settings.langfuse.secret_key,
            host=settings.langfuse.host,
        )
    except Exception as e:
        logger.warning("Langfuse init failed", error=str(e))
        return None


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a Claude API call."""
    return round(
        (input_tokens / 1_000_000) * INPUT_COST_PER_1M
        + (output_tokens / 1_000_000) * OUTPUT_COST_PER_1M,
        6,
    )


class AgentTrace:
    """
    Context manager that wraps a full agent run in a Langfuse trace.
    Records: question, answer, tool calls, token usage, latency, cost.

    Usage:
        with AgentTrace(question="...") as trace:
            result = run_agent(question)
            trace.finish(result)
    """

    def __init__(self, question: str, session_id: str | None = None):
        self.question = question
        self.session_id = session_id
        self.start_time = time.time()
        self._lf = _get_langfuse()
        self._trace = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.tool_call_spans: list = []

    def __enter__(self):
        if self._lf:
            try:
                self._trace = self._lf.trace(
                    name="pipeline_debug_run",
                    input={"question": self.question},
                    session_id=self.session_id,
                    metadata={"project": "pipelineiq"},
                )
                logger.info("Langfuse trace started", trace_id=self._trace.id)
            except Exception as e:
                logger.warning("Langfuse trace start failed", error=str(e))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._lf:
            try:
                self._lf.flush()
            except Exception:
                pass

    def record_llm_call(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        stop_reason: str,
    ) -> None:
        """Record a single Claude API call within the trace."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

        if self._trace:
            try:
                self._trace.generation(
                    name="claude_call",
                    model=model,
                    usage={
                        "input": input_tokens,
                        "output": output_tokens,
                        "total": input_tokens + output_tokens,
                    },
                    metadata={
                        "stop_reason": stop_reason,
                        "estimated_cost_usd": estimate_cost(input_tokens, output_tokens),
                    },
                )
            except Exception as e:
                logger.warning("Langfuse generation record failed", error=str(e))

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        tool_output: Any,
        elapsed_ms: float,
        success: bool,
    ) -> None:
        """Record a single tool invocation as a span."""
        self.tool_call_spans.append({
            "tool": tool_name,
            "elapsed_ms": elapsed_ms,
            "success": success,
        })

        if self._trace:
            try:
                self._trace.span(
                    name=f"tool:{tool_name}",
                    input=tool_input,
                    output=tool_output if success else {"error": str(tool_output)},
                    metadata={
                        "elapsed_ms": elapsed_ms,
                        "success": success,
                    },
                )
            except Exception as e:
                logger.warning("Langfuse span record failed", error=str(e))

    def finish(self, result: dict) -> dict:
        """
        Close the trace with final metrics.
        Returns enriched result with observability data attached.
        """
        elapsed = round(time.time() - self.start_time, 2)
        cost = estimate_cost(self.total_input_tokens, self.total_output_tokens)

        metrics = {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_cost_usd": cost,
            "elapsed_seconds": elapsed,
            "tool_calls_made": result.get("tool_calls_made", 0),
            "findings_count": len(result.get("findings", [])),
        }

        if self._trace:
            try:
                self._trace.update(
                    output={"answer": result.get("answer", "")[:500]},
                    metadata=metrics,
                )
            except Exception as e:
                logger.warning("Langfuse trace finish failed", error=str(e))

        logger.info("Agent run metrics", **metrics)
        return {**result, "metrics": metrics}
