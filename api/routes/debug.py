"""
api/routes/debug.py
--------------------
Debug endpoint — triggers the AI agent and returns the diagnosis.
Supports both regular JSON response and streaming via WebSocket.
"""

import time
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from agent.graph import run_agent
from config import get_logger

router = APIRouter()
logger = get_logger(__name__)


class DebugRequest(BaseModel):
    question: str = "Investigate this pipeline. Find all anomalies and generate fixes."
    table_name: str | None = None
    schema: str = "RAW"


class DebugResponse(BaseModel):
    status: str
    answer: str
    findings: list[dict]
    tool_calls_made: int
    elapsed_seconds: float


@router.post("/run", response_model=DebugResponse)
async def run_debug(request: DebugRequest):
    """
    Run the AI debug agent against the Snowflake pipeline.
    Returns a full diagnosis with root causes and fixes.
    """
    question = request.question
    if request.table_name:
        question = f"Investigate the {request.schema}.{request.table_name} table specifically. {question}"

    logger.info("Debug run requested", question=question)
    start = time.time()

    try:
        result = run_agent(question)
        return DebugResponse(
            status="success",
            answer=result["answer"],
            findings=result["findings"],
            tool_calls_made=result["tool_calls_made"],
            elapsed_seconds=result["elapsed_seconds"],
        )
    except Exception as e:
        logger.error("Debug run failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/stream")
async def stream_debug(websocket: WebSocket):
    """
    WebSocket endpoint for streaming agent progress in real time.
    Used by the Streamlit UI to show tool calls as they happen.
    Phase 3 will add per-step streaming here.
    """
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        question = data.get("question", "Investigate this pipeline.")

        await websocket.send_json({"type": "start", "message": "Agent starting..."})

        result = run_agent(question)

        await websocket.send_json({
            "type": "complete",
            "answer": result["answer"],
            "findings": result["findings"],
            "tool_calls_made": result["tool_calls_made"],
            "elapsed_seconds": result["elapsed_seconds"],
        })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
