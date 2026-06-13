from .schema_inspector import SCHEMA_INSPECTOR_TOOLS
from .anomaly_detector import ANOMALY_DETECTOR_TOOLS
from .fix_generator import FIX_GENERATOR_TOOLS
from .lineage_tracer import LINEAGE_TRACER_TOOLS

# All tools registered for the agent
ALL_TOOLS = (
    SCHEMA_INSPECTOR_TOOLS
    + ANOMALY_DETECTOR_TOOLS
    + FIX_GENERATOR_TOOLS
    + LINEAGE_TRACER_TOOLS
)

# Build lookup map by name for fast dispatch
TOOL_MAP = {tool["name"]: tool["function"] for tool in ALL_TOOLS}

# Claude API tool definitions (input_schema format)
CLAUDE_TOOL_DEFINITIONS = [
    {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["input_schema"],
    }
    for tool in ALL_TOOLS
]

__all__ = ["ALL_TOOLS", "TOOL_MAP", "CLAUDE_TOOL_DEFINITIONS"]