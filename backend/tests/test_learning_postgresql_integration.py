import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.models import LearningActivity, StudySpace, Subject, Topic, User
from app.services import database

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_postgresql_learning_constraints_and_cascades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_database_url = os.getenv("LEARNING_TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip(
            "Set LEARNING_TEST_DATABASE_URL to a disposable PostgreSQL *_test database"
        )
    configured_database_url = get_settings().database_url
    test_url = make_url(test_database_url)
    configured_url = make_url(configured_database_url) if configured_database_url else None
    if (
        not test_database_url.startswith("postgresql")
        or not (test_url.database or "").endswith("_test")
        or (
            configured_url is not None
            and test_url.host == configured_url.host
            and test_url.port == configured_url.port
            and test_url.database == configured_url.database
        )
    ):
        pytest.fail(
            "LEARNING_TEST_DATABASE_URL must be a PostgreSQL *_test database "
            "different from DATABASE_URL"
        )

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")

    user_id = uuid4()
    with database.get_session_factory()() as session:
        session.begin()
        user = User(
            id=user_id,
            email=f"learning-{user_id}@example.test",
            display_name="Integration learner",
            password_hash="integration-only",
            status="active",
        )
        session.add(user)
        session.flush()
        subject = Subject(user_id=user.id, name="Duplicate name")
        session.add(subject)
        session.flush()
        duplicate_subject = Subject(user_id=user.id, name="Duplicate name")
        session.add(duplicate_subject)
        session.flush()
        study_space = StudySpace(subject_id=subject.id, name="Space")
        session.add(study_space)
        session.flush()
        topic = Topic(study_space_id=study_space.id, name="Topic", position=0)
        session.add(topic)
        session.flush()
        activity = LearningActivity(
            user_id=user.id,
            event_type="topic_created",
            subject_id=None,
            study_space_id=None,
            topic_id=topic.id,
            resource_name=topic.name,
        )
        session.add(activity)
        session.flush()

        session.delete(subject)
        session.flush()
        assert session.get(StudySpace, study_space.id) is None
        assert session.get(Topic, topic.id) is None
        assert session.get(LearningActivity, activity.id) is None
        assert session.scalar(
            select(Subject).where(Subject.id == duplicate_subject.id)
        ) is not None
        session.rollback()

    database.get_database_engine().dispose()
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()
