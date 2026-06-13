from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_logger
from pipeline.snowflake_client import get_client

router = APIRouter()
logger = get_logger(__name__)


class TableSummary(BaseModel):
    table_name: str
    schema: str
    row_count: int
    hours_stale: float | None
    last_altered: str | None


@router.get("/tables")
async def list_tables(schema: str = "RAW"):
    """List all tables in a schema with row counts and freshness."""
    try:
        client = get_client()
        tables = client.get_tables(schema=schema)
        return {"schema": schema, "tables": tables}
    except Exception as e:
        logger.error("Failed to list tables", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/columns")
async def get_columns(table_name: str, schema: str = "RAW"):
    """Get column definitions for a table."""
    try:
        client = get_client()
        columns = client.get_columns(table_name, schema=schema)
        return {"table": table_name, "schema": schema, "columns": columns}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/nulls")
async def get_null_stats(table_name: str, schema: str = "RAW"):
    """Get null percentage per column."""
    try:
        client = get_client()
        stats = client.get_null_stats(table_name, schema=schema)
        return {"table": table_name, "schema": schema, "null_stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/freshness")
async def get_freshness(table_name: str, schema: str = "RAW"):
    """Check how stale a table is."""
    try:
        client = get_client()
        freshness = client.get_freshness(table_name, schema=schema)
        return {"table": table_name, "schema": schema, "freshness": freshness}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
