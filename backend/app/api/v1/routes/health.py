import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.services.database import check_database_connection

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse()


@router.get("/health/database", response_model=HealthResponse)
def database_health_check() -> HealthResponse:
    try:
        check_database_connection()
    except (RuntimeError, SQLAlchemyError) as error:
        logger.exception("PostgreSQL health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from error

    return HealthResponse()
