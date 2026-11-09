from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.models import Document
from app.services.database import get_session_factory
from app.services import document_worker
from app.services.document_worker import (
    DocumentLease,
    _publish_result,
    _record_failure,
    _renew_lease,
    process_next_document,
)
from app.services.document_storage import storage_path
from app.services.pdf_extraction import PermanentPdfError, extract_pdf_pages

TRUSTED_ORIGIN = "http://localhost:5173"


def csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": TRUSTED_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def sign_in(client: TestClient, email: str) -> None:
    registered = client.post(
        "/api/v1/auth/register",
        headers=csrf_headers(client),
        json={
            "email": email,
            "password": "a-secure-password-123",
            "display_name": "Document Owner",
        },
    )
    assert registered.status_code == 201
    email_files = sorted(
        get_settings().email_outbox_path.glob("*.eml"),
        key=lambda item: item.stat().st_mtime,
    )
    message = BytesParser(policy=policy.default).parsebytes(email_files[-1].read_bytes())
    body = message.get_body(preferencelist=("plain",)).get_content()
    link = next(line.strip() for line in body.splitlines() if "/verify-email#" in line)
    token = parse_qs(urlparse(link).fragment)["token"][0]
    verified = client.post(
        "/api/v1/auth/email-verification/confirm",
        headers=csrf_headers(client),
        json={"token": token},
    )
    assert verified.status_code == 200
    logged_in = client.post(
        "/api/v1/auth/login",
        headers=csrf_headers(client),
        json={"email": email, "password": "a-secure-password-123"},
    )
    assert logged_in.status_code == 200


def create_learning_space(client: TestClient) -> tuple[str, str]:
    subject = client.post(
        "/api/v1/subjects",
        headers=csrf_headers(client),
        json={"name": "Physics"},
    )
    assert subject.status_code == 201
    study_space = client.post(
        f"/api/v1/subjects/{subject.json()['id']}/study-spaces",
        headers=csrf_headers(client),
        json={"name": "Mechanics"},
    )
    assert study_space.status_code == 201
    return subject.json()["id"], study_space.json()["id"]


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def text_pdf() -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (Hello Viacora) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, content in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + content + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def upload_pdf(
    client: TestClient,
    subject_id: str,
    study_space_id: str,
    *,
    topic_id: str | None = None,
    content: bytes | None = None,
):
    data = {"topic_id": topic_id} if topic_id else None
    return client.post(
        f"/api/v1/subjects/{subject_id}/study-spaces/{study_space_id}/documents",
        headers=csrf_headers(client),
        data=data,
        files={
            "file": (
                "lecture.pdf",
                content if content is not None else blank_pdf(),
                "application/pdf",
            )
        },
    )


def create_processing_lease(
    client: TestClient,
    email: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    lease_expires_at: datetime | None = None,
) -> tuple[sessionmaker[Session], DocumentLease, UUID]:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    monkeypatch.setattr(
        document_worker,
        "get_settings",
        lambda: Settings(_env_file=None, environment="test"),
    )
    sign_in(client, email)
    subject_id, study_space_id = create_learning_space(client)
    uploaded = upload_pdf(client, subject_id, study_space_id)
    assert uploaded.status_code == 201, uploaded.text

    document_id = UUID(uploaded.json()["id"])
    token = uuid4()
    expires_at = lease_expires_at or datetime.now(timezone.utc) + timedelta(minutes=5)
    factory = get_session_factory()
    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.status = "processing"
        document.attempts_used = 1
        document.lease_token = token
        document.lease_expires_at = expires_at
        session.commit()

    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.lease_expires_at is not None
        assert document.lease_expires_at.tzinfo is None
        lease = DocumentLease(
            document_id=document.id,
            user_id=document.user_id,
            subject_id=document.subject_id,
            study_space_id=document.study_space_id,
            storage_key=document.storage_key,
            token=token,
            attempts_used=document.attempts_used,
        )
    return factory, lease, document_id


def test_renew_lease_accepts_naive_sqlite_expiration(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    factory, lease, document_id = create_processing_lease(
        auth_client, "renew-naive-lease@example.com", monkeypatch, tmp_path
    )

    assert _renew_lease(factory, lease) is True
    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.lease_expires_at is not None
        assert document.lease_expires_at.tzinfo is None
        assert document.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_publish_result_accepts_naive_sqlite_expiration(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    factory, lease, document_id = create_processing_lease(
        auth_client, "publish-naive-lease@example.com", monkeypatch, tmp_path
    )

    _publish_result(factory, lease, ["published from SQLite"], has_text=False)
    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == "completed"
        assert document.lease_token is None
        assert [(page.page_number, page.extracted_text) for page in document.pages] == [
            (1, "published from SQLite")
        ]


def test_record_failure_accepts_naive_sqlite_expiration_and_schedules_retry(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    factory, lease, document_id = create_processing_lease(
        auth_client, "retry-naive-lease@example.com", monkeypatch, tmp_path
    )

    _record_failure(factory, lease, error_code="temporary_error", retryable=True)
    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == "queued"
        assert document.retryable is True
        assert document.error_code == "temporary_error"
        assert document.next_attempt_at is not None
        assert document.next_attempt_at.tzinfo is None
        assert document.lease_token is None


def test_expired_and_boundary_equal_sqlite_leases_are_rejected(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is timezone.utc else now.replace(tzinfo=None)

    monkeypatch.setattr(document_worker, "datetime", FrozenDateTime)
    factory, lease, document_id = create_processing_lease(
        auth_client,
        "expired-naive-lease@example.com",
        monkeypatch,
        tmp_path,
        lease_expires_at=now,
    )

    for expiration in (now - timedelta(seconds=1), now):
        with factory() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.status = "processing"
            document.attempts_used = 1
            document.lease_token = lease.token
            document.lease_expires_at = expiration
            session.commit()

        assert _renew_lease(factory, lease) is False
        _publish_result(factory, lease, ["must not publish"], has_text=False)
        _record_failure(factory, lease, error_code="stale", retryable=True)
        with factory() as session:
            document = session.get(Document, document_id)
            assert document is not None
            assert document.status == "processing"
            assert document.lease_token == lease.token


def test_upload_archive_restore_and_worker_processes_private_pdf(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    sign_in(auth_client, "documents@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    topic = auth_client.post(
        f"/api/v1/study-spaces/{study_space_id}/topics",
        headers=csrf_headers(auth_client),
        json={"name": "Forces"},
    )
    assert topic.status_code == 201

    uploaded = upload_pdf(
        auth_client,
        subject_id,
        study_space_id,
        topic_id=topic.json()["id"],
    )
    assert uploaded.status_code == 201, uploaded.text
    payload = uploaded.json()
    assert payload["status"] == "queued"
    assert payload["attempts_used"] == 0
    assert payload["topic_id"] == topic.json()["id"]
    assert "storage_key" not in payload
    assert "content_sha256" not in payload

    with get_session_factory()() as session:
        document = session.get(Document, UUID(payload["id"]))
        assert document is not None
        storage_key = document.storage_key
        assert (tmp_path / "private-documents" / storage_key).is_file()

    library = auth_client.get(
        f"/api/v1/documents?subject_id={subject_id}&study_space_id={study_space_id}"
    )
    assert library.status_code == 200
    assert [item["id"] for item in library.json()["items"]] == [payload["id"]]
    assert library.json()["storage_used_bytes"] == payload["file_size_bytes"]
    assert auth_client.get(f"/api/v1/documents/{payload['id']}/pages").status_code == 409

    archived = auth_client.post(
        f"/api/v1/subjects/{subject_id}/archive",
        headers=csrf_headers(auth_client),
    )
    assert archived.status_code == 200
    assert auth_client.get(f"/api/v1/documents/{payload['id']}").status_code == 404
    assert process_next_document(get_session_factory()) is False

    restored = auth_client.post(
        f"/api/v1/subjects/{subject_id}/restore",
        headers=csrf_headers(auth_client),
    )
    assert restored.status_code == 200
    assert process_next_document(get_session_factory()) is True

    detail = auth_client.get(f"/api/v1/documents/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["attempts_used"] == 1
    assert detail.json()["has_extractable_text"] is False
    pages = auth_client.get(f"/api/v1/documents/{payload['id']}/pages")
    assert pages.status_code == 200
    assert pages.json()["items"] == []
    assert pages.json()["total"] == 0

    archived_space = auth_client.post(
        f"/api/v1/study-spaces/{study_space_id}/archive",
        headers=csrf_headers(auth_client),
    )
    assert archived_space.status_code == 200
    assert auth_client.get(f"/api/v1/documents/{payload['id']}").status_code == 404
    restored_space = auth_client.post(
        f"/api/v1/study-spaces/{study_space_id}/restore",
        headers=csrf_headers(auth_client),
    )
    assert restored_space.status_code == 200
    assert auth_client.get(f"/api/v1/documents/{payload['id']}").status_code == 200

    deleted_topic = auth_client.delete(
        f"/api/v1/topics/{topic.json()['id']}",
        headers=csrf_headers(auth_client),
    )
    assert deleted_topic.status_code == 204
    assert auth_client.get(f"/api/v1/documents/{payload['id']}").json()["topic_id"] is None

    downloaded = auth_client.get(
        f"/api/v1/documents/{payload['id']}/file?disposition=attachment"
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert "storage_key" not in downloaded.headers["content-disposition"]
    assert downloaded.content.startswith(b"%PDF-")
    deleted = auth_client.delete(
        f"/api/v1/documents/{payload['id']}",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 204
    assert not (tmp_path / "private-documents" / storage_key).exists()


def test_document_routes_enforce_owner_and_csrf(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    assert auth_client.get("/api/v1/documents").status_code == 401
    sign_in(auth_client, "first-owner@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    uploaded = upload_pdf(auth_client, subject_id, study_space_id)
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    other_client = TestClient(auth_client.app)
    with other_client:
        sign_in(other_client, "second-owner@example.com")
        assert other_client.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert other_client.get(
            f"/api/v1/documents/{document_id}/file?disposition=attachment"
        ).status_code == 404

    no_csrf = auth_client.delete(
        f"/api/v1/documents/{document_id}",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert no_csrf.status_code == 403


def test_document_upload_limits_and_rate_limit(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    monkeypatch.setenv("DOCUMENT_STORAGE_QUOTA_BYTES", "1")
    sign_in(auth_client, "upload-limits@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)

    quota_exceeded = upload_pdf(auth_client, subject_id, study_space_id)
    assert quota_exceeded.status_code == 409

    monkeypatch.setenv("DOCUMENT_STORAGE_QUOTA_BYTES", "524288000")
    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "10")
    too_large = upload_pdf(auth_client, subject_id, study_space_id)
    assert too_large.status_code == 413

    monkeypatch.setenv("DOCUMENT_MAX_UPLOAD_BYTES", "26214400")
    for _ in range(8):
        response = auth_client.post(
            f"/api/v1/subjects/{subject_id}/study-spaces/{study_space_id}/documents",
            headers=csrf_headers(auth_client),
            files={"file": ("not-a-pdf.txt", b"no", "text/plain")},
        )
        assert response.status_code == 415

    limited = auth_client.post(
        f"/api/v1/subjects/{subject_id}/study-spaces/{study_space_id}/documents",
        headers=csrf_headers(auth_client),
        files={"file": ("not-a-pdf.txt", b"no", "text/plain")},
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_upload_body_limit_rejects_before_multipart_parsing(
    auth_client: TestClient,
) -> None:
    limit = get_settings().document_max_request_bytes
    response = auth_client.post(
        f"/api/v1/subjects/{uuid4()}/study-spaces/{uuid4()}/documents",
        content=b"x" * (limit + 1),
        headers={
            "Origin": TRUSTED_ORIGIN,
            "Content-Type": "multipart/form-data; boundary=limited",
        },
    )
    assert response.status_code == 413


def test_archiving_during_processing_revokes_lease_and_refunds_attempt(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    import app.services.document_worker as worker

    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    sign_in(auth_client, "archive-during-work@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    uploaded = upload_pdf(auth_client, subject_id, study_space_id)
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    original_extractor = worker.extract_pdf_pages

    def archive_during_extract(*args, **kwargs):
        response = auth_client.post(
            f"/api/v1/subjects/{subject_id}/archive",
            headers=csrf_headers(auth_client),
        )
        assert response.status_code == 200
        return ["should not publish"], True

    monkeypatch.setattr(worker, "extract_pdf_pages", archive_during_extract)
    assert process_next_document(get_session_factory()) is True
    archived = auth_client.get(f"/api/v1/documents/{document_id}")
    assert archived.status_code == 404

    with get_session_factory()() as session:
        document = session.get(Document, UUID(document_id))
        assert document is not None
        assert document.status == "queued"
        assert document.attempts_used == 0
        assert document.lease_token is None

    monkeypatch.setattr(worker, "extract_pdf_pages", original_extractor)
    restored = auth_client.post(
        f"/api/v1/subjects/{subject_id}/restore",
        headers=csrf_headers(auth_client),
    )
    assert restored.status_code == 200
    assert process_next_document(get_session_factory()) is True
    completed = auth_client.get(f"/api/v1/documents/{document_id}")
    assert completed.json()["status"] == "completed"
    assert completed.json()["attempts_used"] == 1


def test_permanent_pdf_error_and_transient_retries_obey_three_attempt_cap(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    sign_in(auth_client, "retry-lifecycle@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    permanent = auth_client.post(
        f"/api/v1/subjects/{subject_id}/study-spaces/{study_space_id}/documents",
        headers=csrf_headers(auth_client),
        files={"file": ("bad.pdf", b"%PDF-not-a-real-file", "application/pdf")},
    )
    assert permanent.status_code == 201
    permanent_id = permanent.json()["id"]
    assert process_next_document(get_session_factory()) is True
    permanent_status = auth_client.get(f"/api/v1/documents/{permanent_id}").json()
    assert permanent_status["status"] == "failed"
    assert permanent_status["retryable"] is False
    assert auth_client.post(
        f"/api/v1/documents/{permanent_id}/retry",
        headers=csrf_headers(auth_client),
    ).status_code == 409

    transient = upload_pdf(auth_client, subject_id, study_space_id)
    assert transient.status_code == 201
    transient_id = transient.json()["id"]
    with get_session_factory()() as session:
        document = session.get(Document, UUID(transient_id))
        assert document is not None
        storage_key = document.storage_key
    storage_path(storage_key).unlink()

    for expected_attempts in (1, 2):
        assert process_next_document(get_session_factory()) is True
        pending = auth_client.get(f"/api/v1/documents/{transient_id}").json()
        assert pending["status"] == "queued"
        assert pending["retryable"] is True
        assert pending["attempts_used"] == expected_attempts
        retry = auth_client.post(
            f"/api/v1/documents/{transient_id}/retry",
            headers=csrf_headers(auth_client),
        )
        assert retry.status_code == 200

    assert process_next_document(get_session_factory()) is True
    exhausted = auth_client.get(f"/api/v1/documents/{transient_id}").json()
    assert exhausted["status"] == "failed"
    assert exhausted["attempts_used"] == 3
    assert exhausted["retryable"] is False
    assert auth_client.post(
        f"/api/v1/documents/{transient_id}/retry",
        headers=csrf_headers(auth_client),
    ).status_code == 409


def test_pdf_extraction_reads_page_text_and_enforces_page_limit(tmp_path: Path) -> None:
    path = tmp_path / "with-text.pdf"
    path.write_bytes(text_pdf())
    pages, has_text = extract_pdf_pages(
        path,
        max_pages=500,
        max_text_bytes=1024,
        heartbeat=lambda: True,
    )
    assert has_text is True
    assert len(pages) == 1
    assert "Hello Viacora" in pages[0]

    try:
        extract_pdf_pages(
            path,
            max_pages=0,
            max_text_bytes=1024,
            heartbeat=lambda: True,
        )
    except PermanentPdfError as error:
        assert error.error_code == "page_limit_exceeded"
    else:
        raise AssertionError("PDF page limit was not enforced")


def test_worker_persists_extracted_text_for_owner(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    sign_in(auth_client, "text-extraction@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    uploaded = upload_pdf(
        auth_client,
        subject_id,
        study_space_id,
        content=text_pdf(),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]

    assert process_next_document(get_session_factory()) is True
    pages = auth_client.get(f"/api/v1/documents/{document_id}/pages")
    assert pages.status_code == 200
    assert pages.json()["total"] == 1
    assert "Hello Viacora" in pages.json()["items"][0]["extracted_text"]


def test_parent_deletion_cascades_metadata_and_removes_private_files(
    auth_client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "private-documents"))
    sign_in(auth_client, "parent-delete@example.com")
    subject_id, study_space_id = create_learning_space(auth_client)
    uploaded = upload_pdf(auth_client, subject_id, study_space_id)
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    with get_session_factory()() as session:
        document = session.get(Document, UUID(document_id))
        assert document is not None
        storage_key = document.storage_key

    deleted = auth_client.delete(
        f"/api/v1/subjects/{subject_id}",
        headers=csrf_headers(auth_client),
    )
    assert deleted.status_code == 204
    assert auth_client.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert not (tmp_path / "private-documents" / storage_key).exists()
    with get_session_factory()() as session:
        assert session.get(Document, UUID(document_id)) is None
