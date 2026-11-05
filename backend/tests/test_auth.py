from email import policy
from email.parser import BytesParser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models import EmailActionToken, User, UserSession
from app.security.tokens import token_digest
from app.services.database import get_session_factory

TRUSTED_ORIGIN = "http://localhost:5173"


def csrf_headers(client: TestClient) -> tuple[dict[str, str], str]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    return {"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": token}, token


def newest_email_link(outbox: Path, path: str) -> str:
    email_files = sorted(outbox.glob("*.eml"), key=lambda item: item.stat().st_mtime)
    assert email_files
    message = BytesParser(policy=policy.default).parsebytes(email_files[-1].read_bytes())
    body = message.get_body(preferencelist=("plain",)).get_content()
    return next(line.strip() for line in body.splitlines() if path in line)


def extract_token(link: str) -> str:
    return parse_qs(urlparse(link).fragment)["token"][0]


def register(client: TestClient, email: str = "learner@example.com") -> None:
    headers, _ = csrf_headers(client)
    response = client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={
            "email": email,
            "password": "a-secure-password-123",
            "display_name": "Learner",
        },
    )
    assert response.status_code == 201, response.text
    assert "token" not in response.text


def verify(client: TestClient, token: str) -> None:
    headers, _ = csrf_headers(client)
    response = client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=headers,
        json={"token": token},
    )
    assert response.status_code == 200


def test_registration_validates_csrf_commits_user_and_writes_private_outbox(
    auth_client: TestClient,
) -> None:
    response = auth_client.get("/api/v1/auth/csrf")
    token = response.json()["csrf_token"]
    origin_only = {"Origin": TRUSTED_ORIGIN}
    missing_csrf = auth_client.post(
        "/api/v1/auth/register",
        headers=origin_only,
        json={
            "email": "learner@example.com",
            "password": "a-secure-password-123",
            "display_name": "Learner",
        },
    )
    assert missing_csrf.status_code == 403

    untrusted = auth_client.post(
        "/api/v1/auth/register",
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": token},
        json={
            "email": "learner@example.com",
            "password": "a-secure-password-123",
            "display_name": "Learner",
        },
    )
    assert untrusted.status_code == 403

    register(auth_client)
    outbox = get_settings().email_outbox_path
    link = newest_email_link(outbox, "/verify-email")
    verification_token = extract_token(link)
    assert get_settings().frontend_base_url in link

    with get_session_factory()() as session:
        user = session.scalar(select(User).where(User.email == "learner@example.com"))
        assert user is not None
        assert user.email_verified_at is None
        assert user.password_hash.startswith("$argon2id$")
        assert verification_token not in user.password_hash


def test_duplicate_registration_and_password_validation(auth_client: TestClient) -> None:
    register(auth_client)
    headers, _ = csrf_headers(auth_client)
    duplicate = auth_client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={
            "email": "LEARNER@example.com",
            "password": "a-secure-password-123",
            "display_name": "Another",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Unable to register with these details"

    short_password = auth_client.post(
        "/api/v1/auth/register",
        headers=headers,
        json={
            "email": "second@example.com",
            "password": "too-short",
            "display_name": "Second",
        },
    )
    assert short_password.status_code == 422


def test_login_rotates_session_and_csrf_logout_revokes_it(auth_client: TestClient) -> None:
    register(auth_client)
    verification_token = extract_token(
        newest_email_link(get_settings().email_outbox_path, "/verify-email")
    )
    verify(auth_client, verification_token)

    _, initial_csrf = csrf_headers(auth_client)
    auth_client.cookies.set(
        get_settings().session_cookie_name,
        "attacker-controlled-session",
        domain="testserver.local",
        path="/",
    )
    bound_headers, _ = csrf_headers(auth_client)
    login_response = auth_client.post(
        "/api/v1/auth/login",
        headers=bound_headers,
        json={"email": "learner@example.com", "password": "a-secure-password-123"},
    )
    assert login_response.status_code == 200
    cookie_headers = login_response.headers.get_list("set-cookie")
    session_header = next(value for value in cookie_headers if "viacora_session=" in value)
    csrf_header = next(value for value in cookie_headers if "viacora_csrf=" in value)
    assert "HttpOnly" in session_header
    assert "Max-Age=1209600" in session_header
    assert "SameSite=lax" in session_header
    assert "HttpOnly" not in csrf_header
    session_cookie = auth_client.cookies.get(get_settings().session_cookie_name)
    csrf_cookie = auth_client.cookies.get(get_settings().csrf_cookie_name)
    assert session_cookie and session_cookie != "attacker-controlled-session"
    assert csrf_cookie and csrf_cookie != initial_csrf
    assert login_response.headers["referrer-policy"] == "no-referrer"

    with get_session_factory()() as session:
        record = session.scalar(
            select(UserSession).where(UserSession.session_hash == token_digest(session_cookie))
        )
        assert record is not None
        initial_expiry = record.expires_at
        assert session_cookie not in record.session_hash

    refreshed_headers, _ = csrf_headers(auth_client)
    stale_csrf = auth_client.patch(
        "/api/v1/users/me",
        headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": initial_csrf},
        json={"display_name": "Stale request"},
    )
    assert stale_csrf.status_code == 403
    profile = auth_client.get("/api/v1/users/me")
    assert profile.status_code == 200
    assert profile.json()["email"] == "learner@example.com"
    with get_session_factory()() as session:
        refreshed_record = session.scalar(
            select(UserSession).where(UserSession.session_hash == token_digest(session_cookie))
        )
        assert refreshed_record is not None
        assert refreshed_record.expires_at == initial_expiry
    update = auth_client.patch(
        "/api/v1/users/me",
        headers=refreshed_headers,
        json={"display_name": "Updated learner"},
    )
    assert update.status_code == 200
    assert update.json()["display_name"] == "Updated learner"

    logout_headers, _ = csrf_headers(auth_client)
    logout = auth_client.post("/api/v1/auth/logout", headers=logout_headers)
    assert logout.status_code == 200
    logout_cookies = logout.headers.get_list("set-cookie")
    assert all(
        "Max-Age=0" in value and "Path=/" in value and "SameSite=lax" in value
        for value in logout_cookies
    )
    assert auth_client.get("/api/v1/auth/session").json() == {
        "authenticated": False,
        "user": None,
    }
    with get_session_factory()() as session:
        logged_out_record = session.scalar(
            select(UserSession).where(UserSession.session_hash == token_digest(session_cookie))
        )
        assert logged_out_record is not None
        assert logged_out_record.revoked_at is not None


def test_session_expiration_and_unverified_user_authorization(auth_client: TestClient) -> None:
    register(auth_client)
    headers, _ = csrf_headers(auth_client)
    login = auth_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "learner@example.com", "password": "a-secure-password-123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email_verified"] is False
    assert auth_client.get("/api/v1/users/me").status_code == 403

    raw_session = auth_client.cookies.get(get_settings().session_cookie_name)
    assert raw_session
    with get_session_factory()() as session:
        record = session.scalar(
            select(UserSession).where(UserSession.session_hash == token_digest(raw_session))
        )
        assert record is not None
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    assert auth_client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_password_recovery_is_enumeration_safe_and_revokes_sessions(
    auth_client: TestClient,
) -> None:
    register(auth_client)
    verification_token = extract_token(
        newest_email_link(get_settings().email_outbox_path, "/verify-email")
    )
    verify(auth_client, verification_token)
    login_headers, _ = csrf_headers(auth_client)
    login = auth_client.post(
        "/api/v1/auth/login",
        headers=login_headers,
        json={"email": "learner@example.com", "password": "a-secure-password-123"},
    )
    assert login.status_code == 200
    old_session = auth_client.cookies.get(get_settings().session_cookie_name)

    headers, _ = csrf_headers(auth_client)
    known = auth_client.post(
        "/api/v1/auth/password/forgot",
        headers=headers,
        json={"email": "learner@example.com"},
    )
    headers, _ = csrf_headers(auth_client)
    unknown = auth_client.post(
        "/api/v1/auth/password/forgot",
        headers=headers,
        json={"email": "missing@example.com"},
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()

    reset_token = extract_token(
        newest_email_link(get_settings().email_outbox_path, "/reset-password")
    )
    headers, _ = csrf_headers(auth_client)
    reset = auth_client.post(
        "/api/v1/auth/password/reset",
        headers=headers,
        json={"token": reset_token, "new_password": "an-even-better-password-456"},
    )
    assert reset.status_code == 200
    assert auth_client.get("/api/v1/auth/session").json()["authenticated"] is False
    with get_session_factory()() as session:
        old_record = session.scalar(
            select(UserSession).where(UserSession.session_hash == token_digest(old_session))
        )
        assert old_record is not None and old_record.revoked_at is not None
        assert session.scalar(
            select(EmailActionToken).where(EmailActionToken.token_hash == token_digest(reset_token))
        ).consumed_at is not None

    headers, _ = csrf_headers(auth_client)
    invalid_login = auth_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "learner@example.com", "password": "a-secure-password-123"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.json()["detail"] == "Email or password is incorrect"

    headers, _ = csrf_headers(auth_client)
    new_login = auth_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "learner@example.com", "password": "an-even-better-password-456"},
    )
    assert new_login.status_code == 200


def test_verification_tokens_are_single_use(auth_client: TestClient) -> None:
    register(auth_client)
    token = extract_token(
        newest_email_link(get_settings().email_outbox_path, "/verify-email")
    )
    verify(auth_client, token)

    headers, _ = csrf_headers(auth_client)
    replay = auth_client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=headers,
        json={"token": token},
    )
    assert replay.status_code == 400


def test_verification_resend_is_safe_and_invalidates_the_old_token(
    auth_client: TestClient,
) -> None:
    register(auth_client)
    outbox = get_settings().email_outbox_path
    old_token = extract_token(newest_email_link(outbox, "/verify-email"))
    headers, _ = csrf_headers(auth_client)
    existing = auth_client.post(
        "/api/v1/auth/email-verification/resend",
        headers=headers,
        json={"email": "learner@example.com"},
    )
    headers, _ = csrf_headers(auth_client)
    missing = auth_client.post(
        "/api/v1/auth/email-verification/resend",
        headers=headers,
        json={"email": "missing@example.com"},
    )
    assert existing.status_code == missing.status_code == 200
    assert existing.json() == missing.json()
    new_token = extract_token(newest_email_link(outbox, "/verify-email"))
    assert new_token != old_token

    headers, _ = csrf_headers(auth_client)
    old_result = auth_client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=headers,
        json={"token": old_token},
    )
    assert old_result.status_code == 400
    verify(auth_client, new_token)


def test_expired_verification_token_is_rejected(auth_client: TestClient) -> None:
    register(auth_client)
    token = extract_token(
        newest_email_link(get_settings().email_outbox_path, "/verify-email")
    )
    with get_session_factory()() as session:
        record = session.scalar(
            select(EmailActionToken).where(
                EmailActionToken.token_hash == token_digest(token)
            )
        )
        assert record is not None
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    headers, _ = csrf_headers(auth_client)
    response = auth_client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=headers,
        json={"token": token},
    )
    assert response.status_code == 400


def test_email_delivery_failure_does_not_undo_registration(
    auth_client: TestClient,
    monkeypatch,
) -> None:
    from app.services import email

    class FailedSender:
        def send(self, message) -> None:
            raise OSError("SMTP is unavailable")

    monkeypatch.setattr(email, "get_email_sender", lambda: FailedSender())
    register(auth_client)

    with get_session_factory()() as session:
        user = session.scalar(select(User).where(User.email == "learner@example.com"))
        assert user is not None
        token = session.scalar(
            select(EmailActionToken).where(
                EmailActionToken.user_id == user.id,
                EmailActionToken.purpose == "verification",
            )
        )
        assert token is not None


def test_unknown_account_uses_dummy_hash_and_login_failure_is_generic(
    auth_client: TestClient,
    monkeypatch,
) -> None:
    from app.services import auth

    verification_inputs: list[str | None] = []

    def fake_verify(password: str, password_hash: str | None) -> bool:
        verification_inputs.append(password_hash)
        return False

    monkeypatch.setattr(auth, "verify_password", fake_verify)
    headers, _ = csrf_headers(auth_client)
    response = auth_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "missing@example.com", "password": "guessed-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect"
    assert verification_inputs == [None]


def test_login_rate_limit_runs_before_password_verification(
    auth_client: TestClient,
    monkeypatch,
) -> None:
    from app.api.v1.routes import auth as auth_routes
    from app.services import auth

    verification_called = False

    def reject_rate_limit(limits) -> None:
        raise HTTPException(status_code=429, detail="Too many attempts")

    def verify_password(password: str, password_hash: str | None) -> bool:
        nonlocal verification_called
        verification_called = True
        return False

    monkeypatch.setattr(auth_routes, "enforce_rate_limits", reject_rate_limit)
    monkeypatch.setattr(auth, "verify_password", verify_password)
    headers, _ = csrf_headers(auth_client)
    response = auth_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "missing@example.com", "password": "guessed-password"},
    )

    assert response.status_code == 429
    assert not verification_called


def test_auth_migration_can_downgrade_and_upgrade_on_isolated_database(
    auth_client: TestClient,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    with get_session_factory()() as session:
        assert session.scalar(select(User.id).limit(1)) is None
