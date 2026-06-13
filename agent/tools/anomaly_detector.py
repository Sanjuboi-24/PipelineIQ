"""
agent/tools/anomaly_detector.py
--------------------------------
Tool 2 of 4. Runs statistical checks across the pipeline to detect
anomalies. Returns structured findings the agent reasons over.

Detects:
  - Null spikes (>5% in any column)
  - Stale tables (not refreshed in >24h)
  - Row count fanout (mart rows >> source rows)
  - Statistical outliers (values >5 std deviations from mean)
"""

from typing import Any

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)


def detect_null_anomalies(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Check every column in a table for null spikes.
    Threshold: >5% nulls is a warning, >20% is critical.
    """
    client = get_client()
    null_stats = client.get_null_stats(table_name, schema=schema)
    row_count = client.get_row_count(table_name, schema=schema)

    findings = []
    for stat in null_stats:
        pct = stat.get("null_pct") or 0.0
        if pct > 5:
            findings.append({
                "column": stat["column"],
                "null_pct": pct,
                "severity": "CRITICAL" if pct > 20 else "WARNING",
                "affected_rows": round(row_count * pct / 100),
            })

    return {
        "table": table_name,
        "schema": schema,
        "total_rows": row_count,
        "null_anomalies": findings,
        "has_anomaly": len(findings) > 0,
    }


def detect_staleness(table_name: str, schema: str = "RAW") -> dict[str, Any]:
    """
    Check if a table hasn't been updated in over 24 hours.
    Compares LAST_ALTERED from Snowflake information schema.
    """
    client = get_client()
    freshness = client.get_freshness(table_name, schema=schema)

    hours_stale = freshness.get("HOURS_STALE", 0) or 0
    last_altered = freshness.get("LAST_ALTERED", "unknown")

    return {
        "table": table_name,
        "schema": schema,
        "last_altered": str(last_altered),
        "hours_stale": hours_stale,
        "is_stale": hours_stale > 24,
        "severity": "CRITICAL" if hours_stale > 48 else ("WARNING" if hours_stale > 24 else "OK"),
    }


def detect_row_count_anomaly(
    source_table: str,
    derived_table: str,
    source_schema: str = "RAW",
    derived_schema: str = "MARTS",
) -> dict[str, Any]:
    """
    Compare row counts between a source and derived table.
    A ratio > 1.5x suggests a fanout join bug.
    A ratio < 0.5x suggests data loss.
    """
    client = get_client()

    source_rows = client.get_row_count(source_table, schema=source_schema)
    derived_rows = client.get_row_count(derived_table, schema=derived_schema)

    if source_rows == 0:
        return {"error": f"Source table {source_table} has 0 rows"}

    ratio = round(derived_rows / source_rows, 2)

    anomaly_type = None
    severity = "OK"
    if ratio > 1.5:
        anomaly_type = "FANOUT"
        severity = "CRITICAL" if ratio > 3 else "WARNING"
    elif ratio < 0.5:
        anomaly_type = "DATA_LOSS"
        severity = "CRITICAL" if ratio < 0.1 else "WARNING"

    return {
        "source_table": f"{source_schema}.{source_table}",
        "derived_table": f"{derived_schema}.{derived_table}",
        "source_rows": source_rows,
        "derived_rows": derived_rows,
        "ratio": ratio,
        "anomaly_type": anomaly_type,
        "severity": severity,
        "has_anomaly": anomaly_type is not None,
        "interpretation": (
            f"Derived table has {ratio}x rows vs source. "
            f"{'Likely a fanout join — each source row matches multiple derived rows.' if anomaly_type == 'FANOUT' else ''}"
            f"{'Data loss detected — derived table is missing rows.' if anomaly_type == 'DATA_LOSS' else ''}"
            f"{'Row counts look healthy.' if anomaly_type is None else ''}"
        ),
    }


def detect_statistical_outliers(
    table_name: str,
    column_name: str,
    schema: str = "RAW",
) -> dict[str, Any]:
    """
    Detect values that are statistical outliers using z-score approach.
    Values more than 5 standard deviations from the mean are flagged.
    Used to detect the currency bug (10x price inflation).
    """
    client = get_client()

    sql = f"""
        SELECT
            COUNT(*)                                AS total_rows,
            MIN({column_name})                      AS min_val,
            MAX({column_name})                      AS max_val,
            AVG({column_name})                      AS avg_val,
            STDDEV({column_name})                   AS stddev_val,
            PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY {column_name})            AS median_val,
            PERCENTILE_CONT(0.95) WITHIN GROUP
                (ORDER BY {column_name})            AS p95_val,
            PERCENTILE_CONT(0.99) WITHIN GROUP
                (ORDER BY {column_name})            AS p99_val,
            -- Count values beyond 5 std deviations
            SUM(CASE
                WHEN ABS({column_name} - AVG({column_name}) OVER ())
                     > 5 * STDDEV({column_name}) OVER ()
                THEN 1 ELSE 0
            END)                                    AS outlier_count
        FROM {client.settings.database}.{schema}.{table_name}
        WHERE {column_name} IS NOT NULL
    """

    results = client.execute(sql)
    if not results:
        return {"error": "No data returned"}

    row = results[0]
    avg = float(row.get("AVG_VAL") or 0)
    stddev = float(row.get("STDDEV_VAL") or 1)
    max_val = float(row.get("MAX_VAL") or 0)
    median = float(row.get("MEDIAN_VAL") or 0)
    outlier_count = int(row.get("OUTLIER_COUNT") or 0)

    # Max/median ratio is a strong signal for currency bugs
    max_median_ratio = round(max_val / median, 1) if median > 0 else 0
    has_anomaly = outlier_count > 0 or max_median_ratio > 50

    return {
        "table": table_name,
        "schema": schema,
        "column": column_name,
        "stats": {
            "min": float(row.get("MIN_VAL") or 0),
            "max": max_val,
            "avg": round(avg, 2),
            "stddev": round(stddev, 2),
            "median": median,
            "p95": float(row.get("P95_VAL") or 0),
            "p99": float(row.get("P99_VAL") or 0),
        },
        "outlier_count": outlier_count,
        "max_to_median_ratio": max_median_ratio,
        "has_anomaly": has_anomaly,
        "severity": "CRITICAL" if has_anomaly else "OK",
        "interpretation": (
            f"Max value is {max_median_ratio}x the median. "
            f"{'Strong signal of data corruption — likely unit/currency mismatch.' if max_median_ratio > 50 else 'Values look statistically normal.'}"
        ),
    }


def run_full_anomaly_scan(schemas: list[str] | None = None) -> dict[str, Any]:
    """
    Run all anomaly checks across the full pipeline.
    This is the agent's first comprehensive sweep before targeted inspection.
    Returns a prioritized list of findings.
    """
    client = get_client()
    target_schemas = schemas or ["RAW", "STAGING", "MARTS"]
    all_findings = []

    for schema in target_schemas:
        try:
            tables = client.get_tables(schema=schema)
            for table in tables:
                table_name = table["TABLE_NAME"]

                # Null check on every table
                null_result = detect_null_anomalies(table_name, schema=schema)
                if null_result["has_anomaly"]:
                    for finding in null_result["null_anomalies"]:
                        all_findings.append({
                            "type": "NULL_SPIKE",
                            "table": f"{schema}.{table_name}",
                            "detail": finding,
                            "severity": finding["severity"],
                        })

                # Staleness check
                stale_result = detect_staleness(table_name, schema=schema)
                if stale_result["is_stale"]:
                    all_findings.append({
                        "type": "STALE_TABLE",
                        "table": f"{schema}.{table_name}",
                        "detail": stale_result,
                        "severity": stale_result["severity"],
                    })

        except Exception as e:
            logger.warning("Scan error", schema=schema, error=str(e))

    # Row count checks between known source/derived pairs
    fanout_checks = [
        ("ORDERS", "FCT_ORDERS_BROKEN", "RAW", "MARTS"),
    ]
    for src, derived, src_schema, derived_schema in fanout_checks:
        try:
            result = detect_row_count_anomaly(src, derived, src_schema, derived_schema)
            if result.get("has_anomaly"):
                all_findings.append({
                    "type": "ROW_COUNT_ANOMALY",
                    "table": f"{derived_schema}.{derived}",
                    "detail": result,
                    "severity": result["severity"],
                })
        except Exception:
            pass

    # Sort by severity: CRITICAL first
    severity_order = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
    all_findings.sort(key=lambda x: severity_order.get(x["severity"], 3))

    return {
        "total_findings": len(all_findings),
        "critical": sum(1 for f in all_findings if f["severity"] == "CRITICAL"),
        "warnings": sum(1 for f in all_findings if f["severity"] == "WARNING"),
        "findings": all_findings,
    }


ANOMALY_DETECTOR_TOOLS = [
    {
        "name": "run_full_anomaly_scan",
        "description": (
            "Run a comprehensive anomaly scan across all pipeline tables. "
            "Checks for null spikes, stale tables, and row count anomalies. "
            "Use this after listing tables to get a prioritized list of problems."
        ),
        "function": run_full_anomaly_scan,
        "input_schema": {
            "type": "object",
            "properties": {
                "schemas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Schemas to scan. Defaults to RAW, STAGING, MARTS.",
                }
            },
        },
    },
    {
        "name": "detect_null_anomalies",
        "description": (
            "Check a specific table for null value spikes in any column. "
            "Returns per-column null percentages and severity ratings."
        ),
        "function": detect_null_anomalies,
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
        "name": "detect_statistical_outliers",
        "description": (
            "Detect statistical outliers in a numeric column using z-score and "
            "max/median ratio analysis. Use this when you suspect a currency bug "
            "or data corruption in a financial column."
        ),
        "function": detect_statistical_outliers,
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
        "name": "detect_row_count_anomaly",
        "description": (
            "Compare row counts between a source table and a derived/mart table. "
            "A ratio > 1.5x indicates a fanout join. A ratio < 0.5x indicates data loss."
        ),
        "function": detect_row_count_anomaly,
        "input_schema": {
            "type": "object",
            "properties": {
                "source_table": {"type": "string"},
                "derived_table": {"type": "string"},
                "source_schema": {"type": "string", "default": "RAW"},
                "derived_schema": {"type": "string", "default": "MARTS"},
            },
            "required": ["source_table", "derived_table"],
        },
    },
]
