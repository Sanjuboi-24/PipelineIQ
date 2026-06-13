import time
from contextlib import contextmanager
from typing import Any, Generator

import pandas as pd
import snowflake.connector
from snowflake.connector import DictCursor
from snowflake.connector.pandas_tools import write_pandas
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_logger, get_settings

logger = get_logger(__name__)


class SnowflakeClient:
    """
    Thin wrapper around the Snowflake connector.
    Handles connection lifecycle, retries, and schema introspection.
    Used by both the pipeline and the AI agent tools.
    """

    def __init__(self):
        self.settings = get_settings().snowflake
        self._conn: snowflake.connector.SnowflakeConnection | None = None

    def connect(self) -> None:
        logger.info(
            "Connecting to Snowflake",
            account=self.settings.account,
            database=self.settings.database,
            warehouse=self.settings.warehouse,
        )
        self._conn = snowflake.connector.connect(
            account=self.settings.account,
            user=self.settings.user,
            password=self.settings.password,
            warehouse=self.settings.warehouse,
            database=self.settings.database,
            schema=self.settings.schema_name,
            role=self.settings.role,
            session_parameters={"QUERY_TAG": "pipelineiq"},
        )
        logger.info("Snowflake connection established")

    def disconnect(self) -> None:
        if self._conn and not self._conn.is_closed():
            self._conn.close()
            logger.info("Snowflake connection closed")

    @property
    def conn(self) -> snowflake.connector.SnowflakeConnection:
        if self._conn is None or self._conn.is_closed():
            self.connect()
        return self._conn

    @contextmanager
    def cursor(self, dict_cursor: bool = True) -> Generator:
        cur = self.conn.cursor(DictCursor if dict_cursor else snowflake.connector.cursor.SnowflakeCursor)
        try:
            yield cur
        finally:
            cur.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        """Execute SQL and return results as list of dicts."""
        start = time.perf_counter()
        with self.cursor() as cur:
            cur.execute(sql, params or {})
            results = cur.fetchall()
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug("Query executed", elapsed_ms=round(elapsed, 2), rows=len(results))
        return results

    def execute_df(self, sql: str) -> pd.DataFrame:
        """Execute SQL and return a pandas DataFrame."""
        with self.cursor(dict_cursor=False) as cur:
            cur.execute(sql)
            df = cur.fetch_pandas_all()
        return df

    def load_dataframe(
        self,
        df: pd.DataFrame,
        table_name: str,
        schema: str | None = None,
        overwrite: bool = True,
    ) -> None:
        """Bulk-load a DataFrame into Snowflake using write_pandas."""
        target_schema = schema or self.settings.schema_name
        logger.info(
            "Loading DataFrame",
            table=table_name,
            schema=target_schema,
            rows=len(df),
        )
        success, nchunks, nrows, _ = write_pandas(
            conn=self.conn,
            df=df,
            table_name=table_name.upper(),
            database=self.settings.database,
            schema=target_schema,
            overwrite=overwrite,
            auto_create_table=True,
            quote_identifiers=False,
        )
        if not success:
            raise RuntimeError(f"write_pandas failed for {table_name}")
        logger.info("DataFrame loaded", table=table_name, rows_loaded=nrows, chunks=nchunks)

    # ── Schema introspection (used by agent tools) ──────────────────────────

    def get_tables(self, schema: str | None = None) -> list[dict]:
        """List all tables in a schema with row counts and last refresh time."""
        target_schema = schema or self.settings.schema_name
        sql = f"""
            SELECT
                t.TABLE_NAME,
                t.ROW_COUNT,
                t.BYTES,
                t.LAST_ALTERED,
                t.TABLE_TYPE,
                t.COMMENT
            FROM {self.settings.database}.INFORMATION_SCHEMA.TABLES t
            WHERE t.TABLE_SCHEMA = '{target_schema.upper()}'
            ORDER BY t.TABLE_NAME
        """
        return self.execute(sql)

    def get_columns(self, table_name: str, schema: str | None = None) -> list[dict]:
        """Get column definitions for a table."""
        target_schema = schema or self.settings.schema_name
        sql = f"""
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                IS_NULLABLE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COLUMN_DEFAULT,
                ORDINAL_POSITION
            FROM {self.settings.database}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{target_schema.upper()}'
              AND TABLE_NAME   = '{table_name.upper()}'
            ORDER BY ORDINAL_POSITION
        """
        return self.execute(sql)

    def get_null_stats(self, table_name: str, schema: str | None = None) -> list[dict]:
        """Return null percentage for every column in a table."""
        target_schema = schema or self.settings.schema_name
        cols = self.get_columns(table_name, schema)
        if not cols:
            return []

        col_names = [c["COLUMN_NAME"] for c in cols]
        null_exprs = ", ".join(
            f"ROUND(SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS {c}_NULL_PCT"
            for c in col_names
        )
        sql = f"SELECT {null_exprs} FROM {self.settings.database}.{target_schema}.{table_name}"
        row = self.execute(sql)
        if not row:
            return []

        return [
            {"column": c, "null_pct": row[0].get(f"{c}_NULL_PCT", 0.0)}
            for c in col_names
        ]

    def get_row_count(self, table_name: str, schema: str | None = None) -> int:
        target_schema = schema or self.settings.schema_name
        result = self.execute(
            f"SELECT COUNT(*) AS CNT FROM {self.settings.database}.{target_schema}.{table_name}"
        )
        return result[0]["CNT"] if result else 0

    def get_freshness(self, table_name: str, schema: str | None = None) -> dict:
        """Return table last_altered and how many hours stale it is."""
        target_schema = schema or self.settings.schema_name
        result = self.execute(f"""
            SELECT
                LAST_ALTERED,
                DATEDIFF('hour', LAST_ALTERED, CURRENT_TIMESTAMP()) AS HOURS_STALE
            FROM {self.settings.database}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{target_schema.upper()}'
              AND TABLE_NAME   = '{table_name.upper()}'
        """)
        return result[0] if result else {}

    def setup_database(self) -> None:
        """Create database, schemas, and warehouse if they don't exist."""
        db = self.settings.database
        stmts = [
            f"CREATE DATABASE IF NOT EXISTS {db}",
            f"USE DATABASE {db}",
            f"CREATE SCHEMA IF NOT EXISTS {db}.RAW",
            f"CREATE SCHEMA IF NOT EXISTS {db}.STAGING",
            f"CREATE SCHEMA IF NOT EXISTS {db}.MARTS",
            f"CREATE WAREHOUSE IF NOT EXISTS {self.settings.warehouse} "
            f"WAREHOUSE_SIZE='X-SMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE",
        ]
        for stmt in stmts:
            self.execute(stmt)
        logger.info("Database setup complete", database=db)


# Module-level singleton
_client: SnowflakeClient | None = None


def get_client() -> SnowflakeClient:
    global _client
    if _client is None:
        _client = SnowflakeClient()
        _client.connect()
    return _client
