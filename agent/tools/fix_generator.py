"""
agent/tools/fix_generator.py
-----------------------------
Tool 3 of 4. Given a diagnosed anomaly, generates a concrete fix.
Outputs: corrected SQL, dbt model patch, or dbt test to add.

This is the money tool — it's what makes the agent actionable,
not just diagnostic.
"""

from typing import Any

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)


def generate_null_fix(
    table_name: str,
    column_name: str,
    schema: str = "RAW",
) -> dict[str, Any]:
    """
    Generate a fix for a null spike anomaly.
    Returns: dbt test to add, upstream investigation SQL, and remediation options.
    """
    client = get_client()
    db = client.settings.database

    # Sample the nulls to understand the pattern
    sample_sql = f"""
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN {column_name} IS NULL THEN 1 ELSE 0 END) AS null_rows,
            ROUND(SUM(CASE WHEN {column_name} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS null_pct,
            -- Check if nulls are clustered by time (suggests a pipeline failure window)
            MIN(CASE WHEN {column_name} IS NULL THEN O_ORDERDATE END) AS first_null_date,
            MAX(CASE WHEN {column_name} IS NULL THEN O_ORDERDATE END) AS last_null_date
        FROM {db}.{schema}.{table_name}
    """

    try:
        stats = client.execute(sample_sql)
        null_stats = stats[0] if stats else {}
    except Exception:
        null_stats = {}

    dbt_test = f"""
# Add to your schema.yml to prevent this in future:
models:
  - name: {table_name.lower()}
    columns:
      - name: {column_name.lower()}
        tests:
          - not_null
          - dbt_utils.not_null_proportion:
              at_least: 0.95  # Alert if null rate exceeds 5%
""".strip()

    remediation_sql = f"""
-- Option 1: Identify the source of nulls
SELECT *
FROM {db}.{schema}.{table_name}
WHERE {column_name} IS NULL
LIMIT 100;

-- Option 2: Backfill from upstream if available
-- UPDATE {db}.{schema}.{table_name} t
-- SET {column_name} = upstream.value
-- FROM upstream_table upstream
-- WHERE t.join_key = upstream.join_key
-- AND t.{column_name} IS NULL;

-- Option 3: Flag and exclude nulls in downstream models
-- Add to your dbt mart model:
-- WHERE {column_name} IS NOT NULL
""".strip()

    return {
        "anomaly_type": "NULL_SPIKE",
        "table": f"{schema}.{table_name}",
        "column": column_name,
        "diagnosis": (
            f"Column {column_name} has elevated null rate. "
            f"Null stats: {null_stats}. "
            "Most likely caused by: upstream source sending NULLs, "
            "a failed JOIN dropping values, or an ETL transformation bug."
        ),
        "dbt_test_to_add": dbt_test,
        "remediation_sql": remediation_sql,
        "immediate_action": f"Run: SELECT * FROM {db}.{schema}.{table_name} WHERE {column_name} IS NULL LIMIT 100 to sample affected rows.",
    }


def generate_fanout_fix(
    broken_table: str,
    schema: str = "MARTS",
) -> dict[str, Any]:
    """
    Generate a fix for a fanout join anomaly.
    Returns the corrected SQL with proper grain definition.
    """
    client = get_client()
    db = client.settings.database

    corrected_sql = f"""
-- PROBLEM: {schema}.{broken_table} has a fanout join
-- Each ORDERS row joins to multiple LINEITEM rows without aggregation
-- This inflates row counts and all SUM() metrics by ~6x

-- BROKEN (current):
-- SELECT o.*, l.*
-- FROM ORDERS o
-- JOIN LINEITEM l ON l.L_ORDERKEY = o.O_ORDERKEY
-- Result: 1 order row × N line items = N rows per order

-- FIXED Option A: Keep line-item grain (correct for fct_orders)
CREATE OR REPLACE TABLE {db}.{schema}.FCT_ORDERS AS
SELECT
    o.O_ORDERKEY                        AS order_key,
    o.O_CUSTKEY                         AS customer_key,
    o.O_ORDERDATE                       AS order_date,
    o.O_ORDERSTATUS                     AS order_status,
    l.L_LINENUMBER                      AS line_number,   -- grain column
    l.L_EXTENDEDPRICE                   AS extended_price,
    l.L_QUANTITY                        AS quantity,
    l.L_DISCOUNT                        AS discount_pct,
    ROUND(l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT), 2) AS net_price
FROM {db}.RAW.ORDERS o
-- Correct join: explicit grain is one row per order+line combination
JOIN {db}.RAW.LINEITEM l
    ON l.L_ORDERKEY = o.O_ORDERKEY      -- join condition
ORDER BY o.O_ORDERKEY, l.L_LINENUMBER;

-- FIXED Option B: Aggregate to order grain (for revenue rollups)
CREATE OR REPLACE TABLE {db}.{schema}.MART_ORDER_SUMMARY AS
SELECT
    o.O_ORDERKEY                        AS order_key,
    o.O_CUSTKEY                         AS customer_key,
    o.O_ORDERDATE                       AS order_date,
    COUNT(l.L_LINENUMBER)               AS line_item_count,
    SUM(l.L_EXTENDEDPRICE)              AS gross_revenue,
    SUM(l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT)) AS net_revenue
FROM {db}.RAW.ORDERS o
JOIN {db}.RAW.LINEITEM l ON l.L_ORDERKEY = o.O_ORDERKEY
GROUP BY 1, 2, 3;   -- aggregate removes the fanout
""".strip()

    dbt_test = """
# Add to schema.yml to catch fanout regressions:
models:
  - name: fct_orders
    tests:
      - dbt_utils.equal_rowcount:
          compare_model: ref('stg_lineitems')  # grain should match lineitems
    columns:
      - name: order_key
        tests:
          - not_null
          # Do NOT add unique here — order_key repeats once per line item
          # The grain is order_key + line_number
      - name: line_number
        tests:
          - not_null
""".strip()

    return {
        "anomaly_type": "FANOUT_JOIN",
        "table": f"{schema}.{broken_table}",
        "diagnosis": (
            "Many-to-many join between ORDERS and LINEITEM without aggregation. "
            "Each order has ~6 line items on average, so row count is ~6x source. "
            "All SUM() revenue metrics are inflated by the same factor."
        ),
        "root_cause": (
            "JOIN without defining the output grain. "
            "The fix is either: (a) keep the line-item grain and rename the table to reflect it, "
            "or (b) add GROUP BY to aggregate to order grain."
        ),
        "corrected_sql": corrected_sql,
        "dbt_test_to_add": dbt_test,
        "immediate_action": f"DROP TABLE {db}.{schema}.{broken_table} and recreate using Option A or B above.",
    }


def generate_currency_fix(
    table_name: str,
    column_name: str,
    schema: str = "RAW",
    inflation_factor: int = 100,
) -> dict[str, Any]:
    """
    Generate a fix for a currency/unit bug (values inflated by a constant factor).
    """
    client = get_client()
    db = client.settings.database

    # Find the exact rows affected
    diagnosis_sql = f"""
-- Identify affected rows
SELECT
    COUNT(*) AS affected_rows,
    MIN({column_name}) AS min_inflated,
    MAX({column_name}) AS max_inflated,
    AVG({column_name}) AS avg_inflated
FROM {db}.{schema}.{table_name}
WHERE {column_name} > (
    SELECT PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY {column_name})
    FROM {db}.{schema}.{table_name}
) * 10;
""".strip()

    fix_sql = f"""
-- Fix: reverse the inflation for affected rows
-- IMPORTANT: Run in a transaction and verify row count before committing

BEGIN;

-- Step 1: Preview the fix
SELECT
    {column_name} AS current_value,
    {column_name} / {inflation_factor} AS corrected_value
FROM {db}.{schema}.{table_name}
WHERE {column_name} > 1000000  -- threshold for inflated values
LIMIT 20;

-- Step 2: Apply the fix (uncomment after previewing)
-- UPDATE {db}.{schema}.{table_name}
-- SET {column_name} = {column_name} / {inflation_factor}
-- WHERE {column_name} > 1000000;

-- Step 3: Verify
-- SELECT MAX({column_name}), AVG({column_name})
-- FROM {db}.{schema}.{table_name};

COMMIT;
""".strip()

    dbt_test = f"""
# Add to schema.yml to catch future currency bugs:
models:
  - name: {table_name.lower()}
    columns:
      - name: {column_name.lower()}
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 200000  # max realistic TPC-H line item price
              config:
                severity: error
""".strip()

    return {
        "anomaly_type": "CURRENCY_BUG",
        "table": f"{schema}.{table_name}",
        "column": column_name,
        "diagnosis": (
            f"Column {column_name} contains values inflated by ~{inflation_factor}x. "
            "Max/median ratio far exceeds normal distribution. "
            "Consistent with a currency unit conversion error (e.g. dollars vs cents) "
            "or a multiplication applied to a subset of rows."
        ),
        "root_cause": (
            f"A transformation multiplied {column_name} by {inflation_factor} "
            "for a subset of rows — likely a conditional UPDATE or a bad CASE statement "
            "in an ETL job that applied a currency conversion to already-converted values."
        ),
        "diagnosis_sql": diagnosis_sql,
        "fix_sql": fix_sql,
        "dbt_test_to_add": dbt_test,
        "immediate_action": f"Run diagnosis_sql first to quantify impact before applying fix_sql.",
    }


def generate_staleness_fix(
    table_name: str,
    schema: str = "RAW",
    hours_stale: float = 0,
) -> dict[str, Any]:
    """
    Generate remediation steps for a stale table.
    """
    client = get_client()
    db = client.settings.database

    return {
        "anomaly_type": "STALE_TABLE",
        "table": f"{schema}.{table_name}",
        "diagnosis": (
            f"Table {schema}.{table_name} has not been updated in {hours_stale:.1f} hours. "
            "This indicates the pipeline job responsible for refreshing this table has failed, "
            "been disabled, or is running on a broken schedule."
        ),
        "root_cause": (
            "Pipeline job failure. Common causes: "
            "(1) Upstream source connection timeout, "
            "(2) Warehouse suspended and not auto-resuming, "
            "(3) dbt model dependency failure blocking refresh, "
            "(4) Scheduled job silently failing without alerting."
        ),
        "investigation_steps": [
            f"1. Check Snowflake query history for {table_name} — when was the last COPY INTO or INSERT?",
            "2. Check your orchestrator (Airflow/dbt Cloud/Prefect) for failed runs in the last 48h.",
            "3. Verify the warehouse is running: SHOW WAREHOUSES;",
            f"4. Manually trigger the pipeline job for {table_name}.",
            "5. Add a freshness check to dbt sources.yml (see below).",
        ],
        "dbt_freshness_test": f"""
# Add to sources.yml to alert on stale data:
sources:
  - name: raw
    tables:
      - name: {table_name}
        freshness:
          warn_after: {{count: 12, period: hour}}
          error_after: {{count: 24, period: hour}}
        loaded_at_field: _LOADED_AT  # or LAST_ALTERED equivalent
""".strip(),
        "immediate_action": f"Run: SHOW TABLES LIKE '{table_name}' IN SCHEMA {db}.{schema}; to confirm last_altered timestamp.",
    }


FIX_GENERATOR_TOOLS = [
    {
        "name": "generate_null_fix",
        "description": (
            "Generate a fix for a null spike anomaly in a specific column. "
            "Returns diagnosis, dbt test to prevent recurrence, and remediation SQL."
        ),
        "function": generate_null_fix,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "column_name": {"type": "string"},
                "schema": {"type": "string", "default": "RAW"},
            },
            "required": ["table_name", "column_name"],
        },
    },
    {
        "name": "generate_fanout_fix",
        "description": (
            "Generate a fix for a fanout join anomaly. "
            "Returns corrected SQL with proper grain definition and dbt tests."
        ),
        "function": generate_fanout_fix,
        "input_schema": {
            "type": "object",
            "properties": {
                "broken_table": {"type": "string"},
                "schema": {"type": "string", "default": "MARTS"},
            },
            "required": ["broken_table"],
        },
    },
    {
        "name": "generate_currency_fix",
        "description": (
            "Generate a fix for a currency or unit conversion bug where values "
            "are inflated by a constant factor. Returns diagnosis SQL and corrective UPDATE."
        ),
        "function": generate_currency_fix,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "column_name": {"type": "string"},
                "schema": {"type": "string", "default": "RAW"},
                "inflation_factor": {"type": "integer", "default": 100},
            },
            "required": ["table_name", "column_name"],
        },
    },
    {
        "name": "generate_staleness_fix",
        "description": (
            "Generate remediation steps for a stale table that hasn't been refreshed. "
            "Returns investigation steps and dbt freshness test to add."
        ),
        "function": generate_staleness_fix,
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "schema": {"type": "string", "default": "RAW"},
                "hours_stale": {"type": "number", "default": 0},
            },
            "required": ["table_name"],
        },
    },
]
