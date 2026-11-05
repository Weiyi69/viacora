from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session
from app.models import User
from app.schemas.learning import DashboardResponse
from app.services.dashboard import get_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def dashboard(
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DashboardResponse:
    return get_dashboard(session, user=user)
