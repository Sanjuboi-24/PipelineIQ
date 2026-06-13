"""
run_pipeline.py
---------------
Master entry point. Runs the full pipeline in order:
  1. Ingest TPC-H into Snowflake RAW
  2. Run dbt models (staging → marts)
  3. Validate schema
  4. Inject failures (optional)

Usage:
    python -m pipeline.run_pipeline                  # full run + inject failures
    python -m pipeline.run_pipeline --skip-ingest    # dbt only (data already loaded)
    python -m pipeline.run_pipeline --skip-failures  # no failure injection
    python -m pipeline.run_pipeline --validate-only  # just check what's there
"""

import argparse
import subprocess
import sys
import time

from rich.console import Console

from config import get_logger
from pipeline.ingest_tpch import run_ingestion
from pipeline.inject_failures import inject_all, list_status
from pipeline.schema_validator import validate_schema

logger = get_logger(__name__)
console = Console()


def run_dbt() -> bool:
    """Run dbt models. Returns True if successful."""
    console.print("\n[bold cyan]Running dbt models...[/bold cyan]")
    try:
        result = subprocess.run(
            ["dbt", "run", "--project-dir", "dbt", "--profiles-dir", "dbt"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            console.print("[green]✅ dbt run complete[/green]")
            logger.info("dbt run succeeded")
            return True
        else:
            console.print(f"[red]❌ dbt run failed:[/red]\n{result.stdout}\n{result.stderr}")
            logger.error("dbt run failed", stdout=result.stdout, stderr=result.stderr)
            return False
    except FileNotFoundError:
        console.print("[yellow]⚠ dbt not found in PATH. Skipping dbt run.[/yellow]")
        console.print("[dim]Install with: pip install dbt-snowflake[/dim]")
        return False


def run_dbt_test() -> bool:
    """Run dbt tests after models."""
    console.print("\n[bold cyan]Running dbt tests...[/bold cyan]")
    try:
        result = subprocess.run(
            ["dbt", "test", "--project-dir", "dbt", "--profiles-dir", "dbt"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            console.print("[green]✅ dbt tests passed[/green]")
            return True
        else:
            console.print(f"[yellow]⚠ Some dbt tests failed (expected after failure injection):[/yellow]")
            console.print(f"[dim]{result.stdout[-2000:]}[/dim]")
            return False
    except FileNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser(description="PipelineIQ — Run the full demo pipeline")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip TPC-H ingestion")
    parser.add_argument("--skip-dbt", action="store_true", help="Skip dbt run")
    parser.add_argument("--skip-failures", action="store_true", help="Skip failure injection")
    parser.add_argument("--validate-only", action="store_true", help="Only run schema validation")
    parser.add_argument("--scale-factor", type=float, default=1.0, help="TPC-H scale factor (default: 1.0)")
    args = parser.parse_args()

    start = time.time()

    console.print("\n[bold white]╔══════════════════════════════╗[/bold white]")
    console.print("[bold white]║   PipelineIQ Demo Pipeline   ║[/bold white]")
    console.print("[bold white]╚══════════════════════════════╝[/bold white]\n")

    if args.validate_only:
        validate_schema()
        list_status()
        sys.exit(0)

    # Step 1: Ingest
    if not args.skip_ingest:
        console.print("[bold]Step 1/4: Ingesting TPC-H data[/bold]")
        results = run_ingestion(scale_factor=args.scale_factor)
        failed = [r for r in results if r["status"] == "❌"]
        if failed:
            console.print("[red]Ingestion had failures. Aborting pipeline.[/red]")
            sys.exit(1)
    else:
        console.print("[dim]Step 1/4: Skipping ingestion[/dim]")

    # Step 2: dbt
    if not args.skip_dbt:
        console.print("\n[bold]Step 2/4: Running dbt transformations[/bold]")
        run_dbt()
    else:
        console.print("[dim]Step 2/4: Skipping dbt[/dim]")

    # Step 3: Validate
    console.print("\n[bold]Step 3/4: Validating schema[/bold]")
    validation_results = validate_schema()

    # Step 4: Inject failures
    if not args.skip_failures:
        console.print("\n[bold]Step 4/4: Injecting demo failures[/bold]")
        inject_all()
        run_dbt_test()
    else:
        console.print("[dim]Step 4/4: Skipping failure injection[/dim]")

    elapsed = round(time.time() - start, 1)
    console.print(f"\n[bold green]Pipeline complete in {elapsed}s[/bold green]")
    console.print("[bold]Your pipeline is ready. Run the agent to debug it.[/bold]\n")


if __name__ == "__main__":
    main()
