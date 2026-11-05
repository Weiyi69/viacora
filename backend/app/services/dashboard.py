from sqlalchemy.orm import Session

from app.models import User
from app.repositories import learning_activity as activity_repository
from app.repositories import subjects as subject_repository
from app.schemas.learning import DashboardResponse
from app.services.activity import list_recent_access


def get_dashboard(session: Session, *, user: User) -> DashboardResponse:
    subjects, _ = subject_repository.list_subjects(
        session,
        user_id=user.id,
        query=None,
        include_archived=False,
        page=1,
        page_size=5,
    )
    recent_activity, _ = activity_repository.list_activity(
        session, user_id=user.id, page=1, page_size=10
    )
    return DashboardResponse(
        display_name=user.display_name,
        subject_count=activity_repository.count_active_subjects(
            session, user_id=user.id
        ),
        study_space_count=activity_repository.count_active_study_spaces(
            session, user_id=user.id
        ),
        subjects=[
            {
                "id": subject.id,
                "name": subject.name,
                "description": subject.description,
                "color": subject.color,
                "icon": subject.icon,
                "created_at": subject.created_at,
                "updated_at": subject.updated_at,
                "archived_at": subject.archived_at,
                "study_space_count": study_space_count,
            }
            for subject, study_space_count in subjects
        ],
        recently_accessed=list_recent_access(session, user_id=user.id, page_size=5),
        recent_activity=recent_activity,
    )
