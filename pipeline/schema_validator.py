"""
schema_validator.py
-------------------
Pre-flight validation that all expected tables exist and have data.
Run before the agent to confirm the pipeline is set up correctly.
"""

from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.table import Table

from config import get_logger
from pipeline.snowflake_client import get_client

logger = get_logger(__name__)
console = Console()


@dataclass
class ValidationResult:
    table: str
    schema: str
    exists: bool
    row_count: int
    min_expected_rows: int
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.exists and self.row_count >= self.min_expected_rows and self.error is None


EXPECTED_TABLES = {
    "RAW": {
        "ORDERS":   1_000_000,
        "LINEITEM": 4_000_000,
        "CUSTOMER": 100_000,
        "SUPPLIER": 8_000,
        "PART":     150_000,
        "PARTSUPP": 600_000,
        "NATION":   5,
        "REGION":   3,
    },
    "STAGING": {},   # populated by dbt — validated separately
    "MARTS":   {},
}


def validate_schema() -> list[ValidationResult]:
    client = get_client()
    results = []

    for schema, tables in EXPECTED_TABLES.items():
        for table_name, min_rows in tables.items():
            try:
                row_count = client.get_row_count(table_name, schema=schema)
                results.append(ValidationResult(
                    table=table_name,
                    schema=schema,
                    exists=True,
                    row_count=row_count,
                    min_expected_rows=min_rows,
                ))
            except Exception as e:
                results.append(ValidationResult(
                    table=table_name,
                    schema=schema,
                    exists=False,
                    row_count=0,
                    min_expected_rows=min_rows,
                    error=str(e),
                ))

    _print_results(results)
    return results


def _print_results(results: list[ValidationResult]) -> None:
    table = Table(title="Schema Validation", header_style="bold cyan")
    table.add_column("Schema")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    table.add_column("Min Expected", justify="right")
    table.add_column("Status", justify="center")

    for r in results:
        status = "[green]✅ PASS[/green]" if r.passed else "[red]❌ FAIL[/red]"
        table.add_row(
            r.schema,
            r.table,
            f"{r.row_count:,}",
            f"{r.min_expected_rows:,}",
            status,
        )

    console.print(table)

    failed = [r for r in results if not r.passed]
    if failed:
        console.print(f"\n[red]{len(failed)} validation(s) failed.[/red]")
        for r in failed:
            if r.error:
                console.print(f"  [dim]{r.schema}.{r.table}: {r.error}[/dim]")
    else:
        console.print("[bold green]All validations passed.[/bold green]")


if __name__ == "__main__":
    validate_schema()
