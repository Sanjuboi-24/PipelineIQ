"""
agent/tools/lineage_tracer.py
------------------------------
Tool 4 of 4. Maps data lineage — which tables feed into which,
and what is the downstream blast radius of a failure.

Uses the known dbt model dependency graph for this project.
In production this would query dbt artifacts (manifest.json).
"""

from typing import Any

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)

# Static lineage graph derived from our dbt models.
# In production, parse dbt's manifest.json for dynamic lineage.
LINEAGE_GRAPH = {
    "RAW.ORDERS": {
        "upstream": [],
        "downstream": ["STAGING.STG_ORDERS"],
        "description": "Raw TPC-H orders — ingested by ingest_tpch.py",
    },
    "RAW.LINEITEM": {
        "upstream": [],
        "downstream": ["STAGING.STG_LINEITEMS"],
        "description": "Raw TPC-H line items — ingested by ingest_tpch.py",
    },
    "RAW.CUSTOMER": {
        "upstream": [],
        "downstream": ["STAGING.STG_CUSTOMERS"],
        "description": "Raw TPC-H customers — ingested by ingest_tpch.py",
    },
    "RAW.CUSTOMER_STALE": {
        "upstream": [],
        "downstream": [],
        "description": "Stale customer copy — injected failure, never refreshed",
    },
    "STAGING.STG_ORDERS": {
        "upstream": ["RAW.ORDERS"],
        "downstream": ["MARTS.FCT_ORDERS", "MARTS.FCT_ORDERS_BROKEN"],
        "description": "Cleaned orders — casts types, adds derived columns",
    },
    "STAGING.STG_LINEITEMS": {
        "upstream": ["RAW.LINEITEM"],
        "downstream": ["MARTS.FCT_ORDERS", "MARTS.FCT_ORDERS_BROKEN"],
        "description": "Cleaned line items — adds net/gross price calculations",
    },
    "STAGING.STG_CUSTOMERS": {
        "upstream": ["RAW.CUSTOMER"],
        "downstream": ["MARTS.FCT_ORDERS"],
        "description": "Deduplicated customers",
    },
    "MARTS.FCT_ORDERS": {
        "upstream": ["STAGING.STG_ORDERS", "STAGING.STG_LINEITEMS", "STAGING.STG_CUSTOMERS"],
        "downstream": ["MARTS.MART_REVENUE"],
        "description": "Order fact table — correct grain: one row per line item",
    },
    "MARTS.FCT_ORDERS_BROKEN": {
        "upstream": ["RAW.ORDERS", "RAW.LINEITEM"],
        "downstream": [],
        "description": "Broken fact table — fanout join, 3x row duplication",
    },
    "MARTS.MART_REVENUE": {
        "upstream": ["MARTS.FCT_ORDERS"],
        "downstream": [],
        "description": "Daily revenue aggregation — feeds dashboards",
    },
}


def get_upstream_lineage(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Return all upstream dependencies for a table.
    Traverses the lineage graph recursively to find root sources.
    """
    key = f"{schema.upper()}.{table_name.upper()}"
    node = LINEAGE_GRAPH.get(key)

    if not node:
        return {
            "table": key,
            "error": f"Table {key} not found in lineage graph",
            "known_tables": list(LINEAGE_GRAPH.keys()),
        }

    # BFS to find all upstream nodes
    visited = set()
    queue = [key]
    all_upstream = []

    while queue:
        current = queue.pop(0)
        current_node = LINEAGE_GRAPH.get(current, {})
        for upstream in current_node.get("upstream", []):
            if upstream not in visited:
                visited.add(upstream)
                all_upstream.append(upstream)
                queue.append(upstream)

    return {
        "table": key,
        "description": node["description"],
        "direct_upstream": node["upstream"],
        "all_upstream": all_upstream,
        "root_sources": [t for t in all_upstream if not LINEAGE_GRAPH.get(t, {}).get("upstream")],
        "lineage_depth": len(all_upstream),
    }


def get_downstream_blast_radius(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Return everything downstream of a broken table.
    This tells you: if this table is broken, what else breaks?
    Critical for prioritizing fixes.
    """
    key = f"{schema.upper()}.{table_name.upper()}"
    node = LINEAGE_GRAPH.get(key)

    if not node:
        return {
            "table": key,
            "error": f"Table {key} not found in lineage graph",
        }

    # BFS downstream
    visited = set()
    queue = [key]
    all_downstream = []
    terminal_nodes = []  # nodes with no further downstream (dashboards, exports)

    while queue:
        current = queue.pop(0)
        current_node = LINEAGE_GRAPH.get(current, {})
        for downstream in current_node.get("downstream", []):
            if downstream not in visited:
                visited.add(downstream)
                all_downstream.append(downstream)
                queue.append(downstream)
                # Terminal = no further downstream
                if not LINEAGE_GRAPH.get(downstream, {}).get("downstream"):
                    terminal_nodes.append(downstream)

    return {
        "broken_table": key,
        "description": node["description"],
        "direct_downstream": node["downstream"],
        "all_affected_tables": all_downstream,
        "terminal_nodes": terminal_nodes,
        "blast_radius": len(all_downstream),
        "impact_summary": (
            f"A failure in {key} affects {len(all_downstream)} downstream table(s). "
            f"Terminal outputs affected: {', '.join(terminal_nodes) if terminal_nodes else 'none'}."
        ),
    }


def get_full_lineage(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Return complete lineage — both upstream and downstream — for a table.
    Use this when diagnosing a failure to understand full context.
    """
    upstream = get_upstream_lineage(table_name, schema)
    downstream = get_downstream_blast_radius(table_name, schema)

    key = f"{schema.upper()}.{table_name.upper()}"

    return {
        "table": key,
        "upstream": upstream,
        "downstream": downstream,
        "summary": (
            f"{key} has {len(upstream.get('all_upstream', []))} upstream dependencies "
            f"and affects {downstream.get('blast_radius', 0)} downstream tables. "
            f"Root sources: {upstream.get('root_sources', [])}. "
            f"Terminal outputs: {downstream.get('terminal_nodes', [])}."
        ),
    }


def get_pipeline_dag() -> dict[str, Any]:
    """
    Return the full pipeline DAG as a structured object.
    Useful for the agent to understand the complete data flow at once.
    """
    return {
        "nodes": list(LINEAGE_GRAPH.keys()),
        "edges": [
            {"from": upstream, "to": table}
            for table, node in LINEAGE_GRAPH.items()
            for upstream in node["upstream"]
        ],
        "layers": {
            "RAW": [k for k in LINEAGE_GRAPH if k.startswith("RAW.")],
            "STAGING": [k for k in LINEAGE_GRAPH if k.startswith("STAGING.")],
            "MARTS": [k for k in LINEAGE_GRAPH if k.startswith("MARTS.")],
        },
        "graph": LINEAGE_GRAPH,
    }


LINEAGE_TRACER_TOOLS = [
    {
        "name": "get_downstream_blast_radius",
        "description": (
            "Find all downstream tables affected by a failure in a given table. "
            "Use this to understand the full impact of an anomaly before reporting it. "
            "Returns affected tables and terminal outputs like dashboards."
        ),
        "function": get_downstream_blast_radius,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "schema": {"type": "string", "default": "RAW"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_full_lineage",
        "description": (
            "Get complete data lineage for a table — both upstream sources and downstream impacts. "
            "Use this when you need full context on a suspicious table."
        ),
        "function": get_full_lineage,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "schema": {"type": "string", "default": "RAW"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_pipeline_dag",
        "description": (
            "Get the full pipeline DAG — all tables and their relationships. "
            "Use this early in the investigation to understand the complete data flow."
        ),
        "function": get_pipeline_dag,
        "input_schema": {"type": "object", "properties": {}},
    },
]
