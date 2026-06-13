"""
agent/tools/schema_inspector.py
--------------------------------
Tool 1 of 4. Inspects Snowflake schemas and returns structured
metadata that the LangGraph agent uses for reasoning.

Returns: table list, column definitions, null stats, freshness.
"""

from typing import Any

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)


def inspect_table(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Full inspection of a single table.
    Returns columns, null stats, row count, and freshness.
    This is called by the LangGraph agent as a tool.
    """
    client = get_client()
    logger.info("Inspecting table", table=table_name, schema=schema)

    columns = client.get_columns(table_name, schema=schema)
    null_stats = client.get_null_stats(table_name, schema=schema)
    row_count = client.get_row_count(table_name, schema=schema)
    freshness = client.get_freshness(table_name, schema=schema)

    # Flag suspicious columns
    high_null_cols = [
        s for s in null_stats if (s.get("null_pct") or 0) > 10
    ]

    return {
        "table": table_name,
        "schema": schema,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "null_stats": null_stats,
        "high_null_columns": high_null_cols,
        "freshness": freshness,
        "hours_stale": freshness.get("HOURS_STALE", 0),
        "is_stale": (freshness.get("HOURS_STALE", 0) or 0) > 24,
    }


def list_pipeline_tables(schemas: list[str] | None = None) -> dict[str, Any]:
    """
    List all tables across schemas with health signals.
    Gives the agent a full map of the pipeline before deep inspection.
    """
    client = get_client()
    target_schemas = schemas or ["RAW", "STAGING", "MARTS"]
    result = {}

    for schema in target_schemas:
        try:
            tables = client.get_tables(schema=schema)
            result[schema] = tables
        except Exception as e:
            logger.warning("Could not list schema", schema=schema, error=str(e))
            result[schema] = []

    return result


# LangChain-compatible tool definitions (used when wiring into LangGraph)
SCHEMA_INSPECTOR_TOOLS = [
    {
        "name": "inspect_table",
        "description": (
            "Inspect a Snowflake table to get its column definitions, null statistics, "
            "row count, and freshness. Use this to understand the current state of a table "
            "before diagnosing anomalies."
        ),
        "function": inspect_table,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "The table name to inspect"},
                "schema": {"type": "string", "description": "The schema name (RAW, STAGING, or MARTS)", "default": "RAW"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "list_pipeline_tables",
        "description": (
            "List all tables in the pipeline across RAW, STAGING, and MARTS schemas. "
            "Use this as the first step to get a full map of the pipeline."
        ),
        "function": list_pipeline_tables,
        "input_schema": {
            "type": "object",
            "properties": {
                "schemas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of schemas to inspect",
                    "default": ["RAW", "STAGING", "MARTS"],
                }
            },
        },
    },
]
