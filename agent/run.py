"""
agent/run.py
------------
CLI entry point for running the agent directly from the terminal.
Useful for testing and demoing without spinning up the full API.

Usage:
    python -m agent.run
    python -m agent.run --question "What is wrong with the ORDERS table?"
    python -m agent.run --question "Find all pipeline failures" --json
"""

import argparse
import json
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agent.graph import run_agent
from config import get_logger

logger = get_logger(__name__)
console = Console()


def print_result(result: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return

    # Header
    console.print(Panel(
        f"[bold green]Agent run complete[/bold green]\n"
        f"Tool calls: [cyan]{result['tool_calls_made']}[/cyan]  |  "
        f"Time: [cyan]{result['elapsed_seconds']}s[/cyan]  |  "
        f"Findings: [cyan]{len(result['findings'])}[/cyan]",
        title="PipelineIQ",
        border_style="cyan",
    ))

    # Main answer
    console.print("\n[bold]Diagnosis:[/bold]\n")
    console.print(Markdown(result["answer"]))

    # Findings summary table
    if result["findings"]:
        console.print("\n[bold]Structured Findings:[/bold]\n")
        table = Table(header_style="bold cyan", show_lines=True)
        table.add_column("Tool")
        table.add_column("Anomaly")
        table.add_column("Severity")

        for finding in result["findings"]:
            r = finding.get("result", {})
            table.add_row(
                finding.get("tool", ""),
                r.get("anomaly_type", r.get("type", "unknown")),
                r.get("severity", "—"),
            )
        console.print(table)


def main():
    parser = argparse.ArgumentParser(description="PipelineIQ — AI Pipeline Debugger")
    parser.add_argument(
        "--question",
        type=str,
        default="Investigate this data pipeline. Find all anomalies, diagnose root causes, and generate fixes.",
        help="Question to ask the agent",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of rich formatted output",
    )
    args = parser.parse_args()

    console.print(f"\n[bold cyan]Question:[/bold cyan] {args.question}\n")
    console.print("[dim]Running agent...[/dim]\n")

    try:
        result = run_agent(args.question)
        print_result(result, as_json=args.json)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        logger.error("Agent run failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
