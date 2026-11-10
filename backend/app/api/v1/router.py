from fastapi import APIRouter

from app.api.v1.routes import (
    activity,
    ai,
    auth,
    dashboard,
    documents,
    health,
    study_spaces,
    study_kits,
    study_materials,
    subjects,
    topics,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(dashboard.router)
api_router.include_router(subjects.router)
api_router.include_router(study_spaces.router)
api_router.include_router(study_kits.router)
api_router.include_router(study_materials.router)
api_router.include_router(topics.router)
api_router.include_router(activity.router)
api_router.include_router(ai.router)
api_router.include_router(documents.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
