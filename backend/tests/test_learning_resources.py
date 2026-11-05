from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models import LearningActivity, StudySpace, Subject, Topic
from app.schemas.learning import SubjectCreate
from app.services.subjects import create_subject as create_subject_service
from app.services.database import get_session_factory

TRUSTED_ORIGIN = "http://localhost:5173"


def csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": TRUSTED_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def newest_verification_token(outbox: Path) -> str:
    email_files = sorted(outbox.glob("*.eml"), key=lambda item: item.stat().st_mtime)
    message = BytesParser(policy=policy.default).parsebytes(email_files[-1].read_bytes())
    body = message.get_body(preferencelist=("plain",)).get_content()
    link = next(line.strip() for line in body.splitlines() if "/verify-email#" in line)
    return parse_qs(urlparse(link).fragment)["token"][0]


def sign_in(client: TestClient, email: str = "learner@example.com") -> None:
    headers = csrf_headers(client)
    registered = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={
            "email": email,
            "password": "a-secure-password-123",
            "display_name": "Learner",
        },
    )
    assert registered.status_code == 201

    headers = csrf_headers(client)
    verified = client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=headers,
        json={"token": newest_verification_token(get_settings().email_outbox_path)},
    )
    assert verified.status_code == 200

    response = client.post(
        "/api/v1/auth/login",
        headers=csrf_headers(client),
        json={"email": email, "password": "a-secure-password-123"},
    )
    assert response.status_code == 200


def create_subject(client: TestClient, name: str = "Biology") -> dict:
    response = client.post(
        "/api/v1/subjects",
        headers=csrf_headers(client),
        json={"name": name, "description": "Life sciences", "color": "teal"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_learning_endpoints_require_authentication(auth_client: TestClient) -> None:
    assert auth_client.get("/api/v1/dashboard").status_code == 401
    assert auth_client.get("/api/v1/subjects").status_code == 401
    assert auth_client.post(
        "/api/v1/subjects",
        headers=csrf_headers(auth_client),
        json={"name": "Private"},
    ).status_code == 401


def test_dashboard_empty_state_uses_real_zero_counts(auth_client: TestClient) -> None:
    sign_in(auth_client)
    dashboard = auth_client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["subject_count"] == 0
    assert dashboard.json()["study_space_count"] == 0
    assert dashboard.json()["subjects"] == []
    assert dashboard.json()["recent_activity"] == []


def test_subject_space_topic_crud_and_duplicate_names(auth_client: TestClient) -> None:
    sign_in(auth_client)
    first_subject = create_subject(auth_client)
    duplicate_subject = create_subject(auth_client)
    assert duplicate_subject["name"] == first_subject["name"]

    page = auth_client.get("/api/v1/subjects?q=biology&page=1&page_size=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["study_space_count"] == 0

    updated = auth_client.patch(
        f"/api/v1/subjects/{first_subject['id']}",
        headers=csrf_headers(auth_client),
        json={"name": "Cell Biology", "description": None},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Cell Biology"
    assert updated.json()["description"] is None

    space = auth_client.post(
        f"/api/v1/subjects/{first_subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "Cells"},
    )
    assert space.status_code == 201, space.text
    duplicate_space = auth_client.post(
        f"/api/v1/subjects/{first_subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "Cells"},
    )
    assert duplicate_space.status_code == 201
    spaces_page = auth_client.get(
        f"/api/v1/subjects/{first_subject['id']}/study-spaces?sort_by=name"
    )
    assert spaces_page.status_code == 200
    assert spaces_page.json()["total"] == 2

    first_topic = auth_client.post(
        f"/api/v1/study-spaces/{space.json()['id']}/topics",
        headers=csrf_headers(auth_client),
        json={"name": "Organelles"},
    )
    second_topic = auth_client.post(
        f"/api/v1/study-spaces/{space.json()['id']}/topics",
        headers=csrf_headers(auth_client),
        json={"name": "Cell membrane"},
    )
    assert first_topic.status_code == second_topic.status_code == 201
    assert first_topic.json()["position"] == 0
    assert second_topic.json()["position"] == 1

    moved = auth_client.patch(
        f"/api/v1/topics/{second_topic.json()['id']}",
        headers=csrf_headers(auth_client),
        json={"position": 0, "name": "Membrane"},
    )
    assert moved.status_code == 200
    assert moved.json()["position"] == 0
    ordered = auth_client.get(
        f"/api/v1/study-spaces/{space.json()['id']}/topics"
    )
    assert [topic["name"] for topic in ordered.json()["items"]] == [
        "Membrane",
        "Organelles",
    ]

    empty_patch = auth_client.patch(
        f"/api/v1/subjects/{first_subject['id']}",
        headers=csrf_headers(auth_client),
        json={},
    )
    blank_name = auth_client.post(
        "/api/v1/subjects",
        headers=csrf_headers(auth_client),
        json={"name": "   "},
    )
    invalid_color = auth_client.post(
        "/api/v1/subjects",
        headers=csrf_headers(auth_client),
        json={"name": "Physics", "color": "url(javascript:alert(1))"},
    )
    assert empty_patch.status_code == blank_name.status_code == invalid_color.status_code == 422


def test_archives_hide_descendants_and_restore_independently(
    auth_client: TestClient,
) -> None:
    sign_in(auth_client)
    subject = create_subject(auth_client)
    space = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "Genetics"},
    ).json()
    topic = auth_client.post(
        f"/api/v1/study-spaces/{space['id']}/topics",
        headers=csrf_headers(auth_client),
        json={"name": "DNA"},
    ).json()

    archived = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/archive",
        headers=csrf_headers(auth_client),
    )
    assert archived.status_code == 200
    assert auth_client.get(f"/api/v1/study-spaces/{space['id']}").status_code == 404
    assert auth_client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=csrf_headers(auth_client),
        json={"name": "Blocked"},
    ).status_code == 404

    restored = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/restore",
        headers=csrf_headers(auth_client),
    )
    assert restored.status_code == 200
    assert auth_client.get(f"/api/v1/study-spaces/{space['id']}").status_code == 200

    assert auth_client.post(
        f"/api/v1/study-spaces/{space['id']}/archive",
        headers=csrf_headers(auth_client),
    ).status_code == 200
    assert auth_client.post(
        f"/api/v1/subjects/{subject['id']}/archive",
        headers=csrf_headers(auth_client),
    ).status_code == 200
    assert auth_client.post(
        f"/api/v1/subjects/{subject['id']}/restore",
        headers=csrf_headers(auth_client),
    ).status_code == 200
    assert auth_client.get(f"/api/v1/study-spaces/{space['id']}").status_code == 404
    assert auth_client.post(
        f"/api/v1/study-spaces/{space['id']}/restore",
        headers=csrf_headers(auth_client),
    ).status_code == 200
    assert auth_client.get(f"/api/v1/topics/{topic['id']}").status_code == 200


def test_cross_user_resource_access_is_hidden(auth_client: TestClient) -> None:
    sign_in(auth_client, "first@example.com")
    subject = create_subject(auth_client, "Private subject")
    space = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "Private space"},
    ).json()
    topic = auth_client.post(
        f"/api/v1/study-spaces/{space['id']}/topics",
        headers=csrf_headers(auth_client),
        json={"name": "Private topic"},
    ).json()
    auth_client.post("/api/v1/auth/logout", headers=csrf_headers(auth_client))

    sign_in(auth_client, "second@example.com")
    assert auth_client.get(f"/api/v1/subjects/{subject['id']}").status_code == 404
    assert auth_client.get(f"/api/v1/study-spaces/{space['id']}").status_code == 404
    assert auth_client.get(f"/api/v1/topics/{topic['id']}").status_code == 404
    assert auth_client.patch(
        f"/api/v1/subjects/{subject['id']}",
        headers=csrf_headers(auth_client),
        json={"name": "Taken over"},
    ).status_code == 404
    assert auth_client.delete(
        f"/api/v1/study-spaces/{space['id']}",
        headers=csrf_headers(auth_client),
    ).status_code == 404


def test_access_events_are_explicit_deduplicated_and_deleted_with_resources(
    auth_client: TestClient,
) -> None:
    sign_in(auth_client)
    subject = create_subject(auth_client)
    space = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "Ecology"},
    ).json()

    before_get = auth_client.get("/api/v1/activity").json()["total"]
    assert auth_client.get(f"/api/v1/subjects/{subject['id']}").status_code == 200
    assert auth_client.get(f"/api/v1/study-spaces/{space['id']}").status_code == 200
    assert auth_client.get("/api/v1/activity").json()["total"] == before_get

    first = auth_client.post(
        "/api/v1/activity/access",
        headers=csrf_headers(auth_client),
        json={"resource_type": "subject", "resource_id": subject["id"]},
    )
    repeated = auth_client.post(
        "/api/v1/activity/access",
        headers=csrf_headers(auth_client),
        json={"resource_type": "subject", "resource_id": subject["id"]},
    )
    assert first.status_code == repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]

    dashboard = auth_client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["subject_count"] == 1
    assert dashboard.json()["study_space_count"] == 1
    assert dashboard.json()["recently_accessed"][0]["resource_name"] == "Biology"

    assert auth_client.delete(
        f"/api/v1/subjects/{subject['id']}",
        headers=csrf_headers(auth_client),
    ).status_code == 204
    with get_session_factory()() as session:
        assert session.get(Subject, UUID(subject["id"])) is None
        assert session.get(StudySpace, UUID(space["id"])) is None
        assert session.scalar(
            select(LearningActivity).where(
                LearningActivity.subject_id == UUID(subject["id"])
            )
        ) is None
        assert session.scalar(
            select(LearningActivity).where(
                LearningActivity.study_space_id == UUID(space["id"])
            )
        ) is None
        assert session.scalar(
            select(Topic).where(Topic.study_space_id == UUID(space["id"]))
        ) is None


def test_access_rejects_foreign_or_archived_resources(auth_client: TestClient) -> None:
    sign_in(auth_client)
    subject = create_subject(auth_client)
    space = auth_client.post(
        f"/api/v1/subjects/{subject['id']}/study-spaces",
        headers=csrf_headers(auth_client),
        json={"name": "History"},
    ).json()
    auth_client.post(
        f"/api/v1/subjects/{subject['id']}/archive",
        headers=csrf_headers(auth_client),
    )
    response = auth_client.post(
        "/api/v1/activity/access",
        headers=csrf_headers(auth_client),
        json={"resource_type": "study_space", "resource_id": space["id"]},
    )
    assert response.status_code == 404


def test_creation_and_mutation_require_csrf(auth_client: TestClient) -> None:
    sign_in(auth_client)
    assert auth_client.post(
        "/api/v1/subjects",
        headers={"Origin": TRUSTED_ORIGIN},
        json={"name": "No CSRF"},
    ).status_code == 403
    preflight = auth_client.options(
        "/api/v1/subjects/resource-id",
        headers={
            "Origin": TRUSTED_ORIGIN,
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers["access-control-allow-methods"]


def test_subject_and_creation_activity_roll_back_together(
    auth_client: TestClient,
) -> None:
    sign_in(auth_client)
    user_id = UUID(auth_client.get("/api/v1/auth/session").json()["user"]["id"])

    with get_session_factory()() as session:
        @event.listens_for(session, "before_flush")
        def fail_activity_insert(_session, _flush_context, _instances) -> None:
            if any(isinstance(item, LearningActivity) for item in _session.new):
                raise IntegrityError("forced activity failure", {}, Exception())

        with pytest.raises(IntegrityError, match="forced activity failure"):
            create_subject_service(
                session,
                user_id=user_id,
                payload=SubjectCreate(name="Atomic subject"),
            )

    with get_session_factory()() as session:
        assert session.scalar(
            select(Subject).where(
                Subject.user_id == user_id,
                Subject.name == "Atomic subject",
            )
        ) is None
        assert session.scalar(
            select(LearningActivity).where(
                LearningActivity.user_id == user_id,
                LearningActivity.event_type == "subject_created",
            )
        ) is None
