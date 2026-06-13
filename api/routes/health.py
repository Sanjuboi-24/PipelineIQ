from fastapi import APIRouter
from pydantic import BaseModel
import time

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    timestamp: float
    version: str


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", timestamp=time.time(), version="0.1.0")
