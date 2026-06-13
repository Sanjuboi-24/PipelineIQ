"""
inject_failures.py
------------------
Injects 4 realistic, named failure scenarios into the pipeline
so the AI agent has something to detect and fix.

Failures:
  1. NULL_INJECTION    — sets 40% of O_TOTALPRICE to NULL in ORDERS
  2. STALE_TABLE       — creates a copy of CUSTOMER that is never refreshed
  3. FANOUT_JOIN       — creates a broken mart view with a 3x row duplication
  4. CURRENCY_BUG      — multiplies L_EXTENDEDPRICE by 100 in a subset of rows

Run:
    python -m pipeline.inject_failures          # inject all
    python -m pipeline.inject_failures --reset  # restore clean state
    python -m pipeline.inject_failures --list   # show current failure status
"""

import argparse
import sys

from rich.console import Console
from rich.table import Table

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)
console = Console()


# ── Failure definitions ────────────────────────────────────────────────────

FAILURES = {
    "NULL_INJECTION": {
        "description": "40% of O_TOTALPRICE set to NULL in RAW.ORDERS",
        "impact": "fct_orders revenue calculations return NULL for 40% of rows",
        "inject_sql": """
            UPDATE {db}.RAW.ORDERS
            SET O_TOTALPRICE = NULL
            WHERE MOD(O_ORDERKEY, 10) < 4
        """,
        "reset_sql": None,  # Can't restore NULLs — reset requires re-ingestion
        "detect_query": """
            SELECT
                ROUND(SUM(CASE WHEN O_TOTALPRICE IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)
                    AS null_pct
            FROM {db}.RAW.ORDERS
        """,
        "detect_threshold": "null_pct > 5",
    },
    "STALE_TABLE": {
        "description": "RAW.CUSTOMER_STALE created but never refreshed — simulates a broken pipeline job",
        "impact": "dim_customers joins to stale data; new customers missing",
        "inject_sql": """
            CREATE OR REPLACE TABLE {db}.RAW.CUSTOMER_STALE AS
            SELECT * FROM {db}.RAW.CUSTOMER
            WHERE C_CUSTKEY <= 10000
        """,
        "reset_sql": "DROP TABLE IF EXISTS {db}.RAW.CUSTOMER_STALE",
        "detect_query": """
            SELECT
                DATEDIFF('hour', LAST_ALTERED, CURRENT_TIMESTAMP()) AS hours_stale,
                ROW_COUNT
            FROM {db}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'RAW' AND TABLE_NAME = 'CUSTOMER_STALE'
        """,
        "detect_threshold": "hours_stale > 0",
    },
    "FANOUT_JOIN": {
        "description": "MARTS.FCT_ORDERS_BROKEN has a many-to-many join causing 3x row duplication",
        "impact": "Revenue aggregations are inflated by ~3x; row count doesn't match source",
        "inject_sql": """
            CREATE OR REPLACE TABLE {db}.MARTS.FCT_ORDERS_BROKEN AS
            SELECT
                o.O_ORDERKEY,
                o.O_CUSTKEY,
                o.O_TOTALPRICE,
                o.O_ORDERDATE,
                l.L_LINENUMBER,
                l.L_EXTENDEDPRICE,
                l.L_QUANTITY
            FROM {db}.RAW.ORDERS o
            -- Intentionally joining without L_ORDERKEY = O_ORDERKEY uniqueness guard
            -- This creates a fanout: each order row joins to ALL its lineitems
            JOIN {db}.RAW.LINEITEM l ON l.L_ORDERKEY = o.O_ORDERKEY
        """,
        "reset_sql": "DROP TABLE IF EXISTS {db}.MARTS.FCT_ORDERS_BROKEN",
        "detect_query": """
            SELECT
                COUNT(*) AS broken_rows,
                (SELECT COUNT(*) FROM {db}.RAW.ORDERS) AS source_rows,
                ROUND(COUNT(*) * 1.0 / (SELECT COUNT(*) FROM {db}.RAW.ORDERS), 2) AS fanout_ratio
            FROM {db}.MARTS.FCT_ORDERS_BROKEN
        """,
        "detect_threshold": "fanout_ratio > 1.5",
    },
    "CURRENCY_BUG": {
        "description": "10% of L_EXTENDEDPRICE values multiplied by 100 in RAW.LINEITEM",
        "impact": "mart_revenue shows ~10x revenue inflation for affected orders",
        "inject_sql": """
            UPDATE {db}.RAW.LINEITEM
            SET L_EXTENDEDPRICE = L_EXTENDEDPRICE * 100
            WHERE MOD(L_ORDERKEY, 10) = 0
        """,
        "reset_sql": """
            UPDATE {db}.RAW.LINEITEM
            SET L_EXTENDEDPRICE = L_EXTENDEDPRICE / 100
            WHERE MOD(L_ORDERKEY, 10) = 0
              AND L_EXTENDEDPRICE > 1000000
        """,
        "detect_query": """
            SELECT
                MAX(L_EXTENDEDPRICE) AS max_price,
                AVG(L_EXTENDEDPRICE) AS avg_price,
                STDDEV(L_EXTENDEDPRICE) AS stddev_price
            FROM {db}.RAW.LINEITEM
        """,
        "detect_threshold": "max_price > 10000000",
    },
}


def inject_all() -> None:
    client = get_client()
    db = client.settings.database

    console.print("\n[bold red]⚠  Injecting pipeline failures...[/bold red]\n")

    for name, failure in FAILURES.items():
        try:
            sql = failure["inject_sql"].format(db=db)
            client.execute(sql)
            console.print(f"[red]✓[/red] [bold]{name}[/bold] — {failure['description']}")
            logger.info("Failure injected", failure=name)
        except Exception as e:
            console.print(f"[yellow]⚠ {name} skipped:[/yellow] {e}")
            logger.warning("Failure injection skipped", failure=name, reason=str(e))

    console.print("\n[bold]All failures injected. Run the agent to detect them.[/bold]")


def reset_all() -> None:
    """
    Reset reversible failures. NULL_INJECTION requires full re-ingestion.
    """
    client = get_client()
    db = client.settings.database

    console.print("\n[bold yellow]Resetting pipeline failures...[/bold yellow]\n")

    for name, failure in FAILURES.items():
        reset_sql = failure.get("reset_sql")
        if reset_sql is None:
            console.print(f"[dim]{name} — requires re-ingestion to reset (skipping)[/dim]")
            continue
        try:
            client.execute(reset_sql.format(db=db))
            console.print(f"[green]✓[/green] [bold]{name}[/bold] reset")
        except Exception as e:
            console.print(f"[yellow]⚠ {name}:[/yellow] {e}")

    console.print("\n[bold green]Reset complete.[/bold green]")


def list_status() -> None:
    client = get_client()
    db = client.settings.database

    table = Table(title="Failure Status", header_style="bold cyan")
    table.add_column("Failure")
    table.add_column("Description")
    table.add_column("Active?", justify="center")

    for name, failure in FAILURES.items():
        try:
            results = client.execute(failure["detect_query"].format(db=db))
            row = results[0] if results else {}
            # Evaluate threshold heuristically
            threshold = failure["detect_threshold"]
            key, op, val = threshold.split()
            actual = row.get(key.upper(), row.get(key, 0)) or 0
            active = eval(f"{float(actual)} {op} {float(val)}")  # noqa: S307
            status = "[red]ACTIVE[/red]" if active else "[green]clean[/green]"
        except Exception:
            status = "[dim]unknown[/dim]"

        table.add_row(name, failure["description"], status)

    console.print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject or reset pipeline failures")
    parser.add_argument("--reset", action="store_true", help="Reset all reversible failures")
    parser.add_argument("--list", action="store_true", help="Show current failure status")
    args = parser.parse_args()

    if args.reset:
        reset_all()
    elif args.list:
        list_status()
    else:
        inject_all()
