"""
api/main.py
-----------
FastAPI application. Mounts all routers and configures middleware.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health, pipeline, debug
from config import get_logger, get_settings

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PipelineIQ API starting", environment=settings.environment)
    yield
    logger.info("PipelineIQ API shutting down")


app = FastAPI(
    title="PipelineIQ",
    description="AI-powered data pipeline debugger",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
app.include_router(debug.router, prefix="/debug", tags=["debug"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
