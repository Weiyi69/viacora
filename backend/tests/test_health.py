from fastapi.testclient import TestClient

from app.api.v1.routes import health
from app.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_health_returns_ok_when_database_is_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "check_database_connection", lambda: None)

    response = client.get("/api/v1/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_health_returns_service_unavailable_when_database_fails(
    monkeypatch,
) -> None:
    def fail_connection() -> None:
        raise RuntimeError("database configuration is missing")

    monkeypatch.setattr(health, "check_database_connection", fail_connection)

    response = client.get("/api/v1/health/database")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
