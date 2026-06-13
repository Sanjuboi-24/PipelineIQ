"""
ingest_tpch.py
--------------
Downloads TPC-H Scale Factor 1 data (~1 GB, 8 tables) using the
`dbgen` approach via the `duckdb` TPC-H extension (no binary needed),
then bulk-loads each table into Snowflake RAW schema using write_pandas.

Run:
    python -m pipeline.ingest_tpch

Tables loaded:
    ORDERS, LINEITEM, CUSTOMER, SUPPLIER, PART, PARTSUPP, NATION, REGION
"""

import time
from typing import Any

import duckdb
import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)
console = Console()

# TPC-H tables at SF=1 — approximate row counts for validation
TPCH_TABLES = {
    "ORDERS":    {"sf1_rows": 1_500_000,  "key_col": "O_ORDERKEY"},
    "LINEITEM":  {"sf1_rows": 6_000_000,  "key_col": "L_ORDERKEY"},
    "CUSTOMER":  {"sf1_rows": 150_000,    "key_col": "C_CUSTKEY"},
    "SUPPLIER":  {"sf1_rows": 10_000,     "key_col": "S_SUPPKEY"},
    "PART":      {"sf1_rows": 200_000,    "key_col": "P_PARTKEY"},
    "PARTSUPP":  {"sf1_rows": 800_000,    "key_col": "PS_PARTKEY"},
    "NATION":    {"sf1_rows": 25,          "key_col": "N_NATIONKEY"},
    "REGION":    {"sf1_rows": 5,           "key_col": "R_REGIONKEY"},
}

# Chunk size for write_pandas — keeps memory reasonable
CHUNK_ROWS = 200_000


def generate_tpch_table(table_name: str, scale_factor: float = 1.0) -> pd.DataFrame:
    """
    Use DuckDB's built-in TPC-H extension to generate a table as a DataFrame.
    No external binaries or downloads needed.
    """
    con = duckdb.connect()
    con.execute("INSTALL tpch; LOAD tpch;")
    con.execute(f"CALL dbgen(sf={scale_factor})")
    df = con.execute(f"SELECT * FROM {table_name.lower()}").df()
    # Uppercase column names to match Snowflake convention
    df.columns = [c.upper() for c in df.columns]
    con.close()
    return df


def load_table(
    table_name: str,
    df: pd.DataFrame,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Load a single DataFrame into Snowflake RAW schema."""
    client = get_client()
    start = time.perf_counter()

    client.load_dataframe(
        df=df,
        table_name=table_name,
        schema="RAW",
        overwrite=overwrite,
    )

    elapsed = round((time.perf_counter() - start), 2)
    row_count = client.get_row_count(table_name, schema="RAW")

    return {
        "table": table_name,
        "rows_expected": TPCH_TABLES[table_name]["sf1_rows"],
        "rows_loaded": row_count,
        "elapsed_s": elapsed,
        "status": "✅" if row_count > 0 else "❌",
    }


def run_ingestion(scale_factor: float = 1.0, overwrite: bool = True) -> list[dict]:
    """
    Full ingestion pipeline:
    1. Setup Snowflake database + schemas
    2. Generate each TPC-H table via DuckDB
    3. Bulk-load into Snowflake RAW
    4. Validate row counts
    5. Return summary report
    """
    client = get_client()

    console.print("\n[bold cyan]PipelineIQ — TPC-H Ingestion[/bold cyan]")
    console.print(f"Scale factor: [bold]{scale_factor}[/bold] (~{scale_factor}GB)")
    console.print(f"Target: [bold]{client.settings.database}.RAW[/bold]\n")

    # Step 1: Setup
    console.print("[yellow]Setting up Snowflake database and schemas...[/yellow]")
    client.setup_database()

    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Ingesting TPC-H tables", total=len(TPCH_TABLES))

        for table_name in TPCH_TABLES:
            progress.update(task, description=f"[cyan]Generating {table_name}...[/cyan]")

            try:
                logger.info("Generating TPC-H table", table=table_name, sf=scale_factor)
                df = generate_tpch_table(table_name, scale_factor)

                progress.update(task, description=f"[green]Loading {table_name} → Snowflake ({len(df):,} rows)...[/green]")
                result = load_table(table_name, df, overwrite=overwrite)
                results.append(result)
                logger.info("Table loaded", **result)

            except Exception as e:
                logger.error("Failed to load table", table=table_name, error=str(e))
                results.append({
                    "table": table_name,
                    "rows_expected": TPCH_TABLES[table_name]["sf1_rows"],
                    "rows_loaded": 0,
                    "elapsed_s": 0,
                    "status": "❌",
                    "error": str(e),
                })

            progress.advance(task)

    _print_summary(results)
    return results


def _print_summary(results: list[dict]) -> None:
    table = Table(title="Ingestion Summary", show_header=True, header_style="bold cyan")
    table.add_column("Table", style="white")
    table.add_column("Expected Rows", justify="right")
    table.add_column("Loaded Rows", justify="right")
    table.add_column("Time (s)", justify="right")
    table.add_column("Status", justify="center")

    for r in results:
        table.add_row(
            r["table"],
            f"{r['rows_expected']:,}",
            f"{r['rows_loaded']:,}",
            str(r["elapsed_s"]),
            r["status"],
        )

    console.print(table)

    failed = [r for r in results if r["status"] == "❌"]
    if failed:
        console.print(f"\n[bold red]{len(failed)} table(s) failed. Check logs above.[/bold red]")
    else:
        total_rows = sum(r["rows_loaded"] for r in results)
        console.print(f"\n[bold green]✅ All tables loaded. Total rows: {total_rows:,}[/bold green]")


if __name__ == "__main__":
    run_ingestion(scale_factor=1.0)
