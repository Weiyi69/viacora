# Viacora

Viacora is a personalized learning platform for turning study materials into
summaries, flashcards, quizzes, and AI-assisted tutoring experiences.

## Phase 1: Foundation

This phase establishes a Vue 3 + TypeScript frontend, a FastAPI backend, a
PostgreSQL connection and health check, and Alembic migration configuration.
The frontend and backend are separate applications in a modular monolith.

## Phase 2: Authentication

The application includes Argon2-backed registration and login, fixed 14-day
database sessions, CSRF/origin protection, required email verification,
password recovery, and a private local email outbox. User, session, token, and
rate-limit tables are managed by Alembic; the application never creates its
schema at startup.

## Phase 3: Learning workspace

The authenticated workspace lets each user manage subjects, study spaces, and
ordered topics. Subjects are owned directly by the signed-in user; study spaces
and topics are authorized through their parent resources. Duplicate names are
allowed. Archiving is reversible and independent at each level, while a
confirmed permanent delete removes nested resources and their related activity.

The dashboard reports persisted subject and study-space counts and actual
creation/access activity. Opening a subject or study space records an explicit
access event with a five-minute per-resource deduplication window; read-only
GET requests do not write activity. API and database health checks remain
available in the authenticated **System status** page and at the existing
health endpoints.

Learning API routes are under `/api/v1`: `/dashboard`, `/subjects`,
`/subjects/{subject_id}/study-spaces`, `/study-spaces/{study_space_id}`,
`/study-spaces/{study_space_id}/topics`, `/topics/{topic_id}`, and `/activity`.
Mutating endpoints require the existing authenticated session and CSRF/Origin
protections. The frontend uses TanStack Vue Query for learning-resource data.

## Phase 4: Private PDF documents

Authenticated users can upload PDFs to an active study space, optionally
assign them to a topic, review extracted page text, retry eligible transient
failures, and download their original PDF. Documents are private and scoped to
the signed-in user and their subject/study-space hierarchy. The upload limit is
25 MiB, the multipart request cap is 26 MiB, and processing is limited to 500
pages and 10 MiB of extracted text. No OCR is performed. Storage is limited to
500 MiB per user and 10 upload attempts per rolling hour by default; the
backend `.env.example` lists the corresponding settings.

Local file storage defaults to the ignored `backend\.local-storage` directory.
Keep that path outside any public/static web root. The database stores metadata
and extracted text, never the PDF bytes or a public storage URL. The background
worker is a separate process and uses PostgreSQL for its durable queue and
leases; it does not require Redis or Docker. Run it from a second terminal:

```powershell
Set-Location backend
uv run python -m app.worker
```

In production, configure a private persistent storage location, enforce the
26 MiB request cap at the reverse proxy as well as in the API, and run the
worker with deployment-level CPU/memory limits and malware scanning. Inline
preview is intentionally disabled in production; enabling it requires a
single-use, owner-authorized bridge on a separately isolated preview origin.
Attachments remain owner-authorized. The selected pure-Python `pypdf` parser
uses a BSD-style license and publishes universal wheels supporting Python
3.12+ including the project's Windows Python 3.14 environment.

## Phase 5: AI indexing and retrieval

The backend AI foundation uses a server-side OpenAI key and the existing
PostgreSQL worker. `AI_ENABLED` defaults to `false`; enabling it requires a
valid provider key, the seeded 1536-dimensional embedding profile, a pricing
version, and positive configured USD rates. Never put the provider key in the
frontend or logs.

The index stores page-aware chunks and vectors in a versioned generation. A
replacement generation is staged separately and becomes active only after all
chunks and vectors validate. When AI is enabled, extraction completion and
initial indexing-job creation share one database transaction.
PostgreSQL/pgvector is required:
install the pgvector server package for the same PostgreSQL installation,
verify `vector` is available, then apply the Alembic migration during an
approved deployment. The migration enables the extension only if it is
available and never removes the shared extension on downgrade.

Chunk limits use a deliberately conservative UTF-8 byte upper bound rather
than claiming an exact tokenizer count: chunks are capped at 1,024 UTF-8 bytes
and embedding batches at 16 chunks / 16,384 UTF-8 bytes. Usage quotas reserve
that bound before each provider request and conservatively account for
requests with unknown outcomes. Defaults are 100,000 daily input tokens and
USD 0.50 per user. Query bodies are capped at 8 KiB, query text at 2,048
characters, and retrieval returns at most 8 passages (default 5).

`POST /api/v1/ai/retrieval` requires the existing authenticated session and
CSRF protections. It returns matching passage excerpts and source document/page
references only; it does not generate answers or add a frontend. SQL retrieval
filters by owner and active parent resources. Retrieved text is untrusted data,
not instructions or tool input. `GET /api/v1/ai/usage` reports only the current
user's recorded usage.

## Prerequisites

- Node.js 24 and pnpm 10
- Python 3.12 or newer (tested with Python 3.14)
- PostgreSQL 18 running locally
- pgvector installed for the PostgreSQL 18 server (Phase 5)

Docker is not required.

## Local setup (PowerShell)

1. Copy `.env.example` to `backend\.env` and set `DATABASE_URL` to the local
   `viacora_dev` database. Keep `.env` private and never commit it. The
   development defaults use `http://localhost:5173`, a local HTTP cookie, and
   local outbox delivery, so no SMTP credentials are needed.
2. Install backend dependencies and apply the migration:

   ```powershell
   Set-Location backend
   uv sync --python 3.14
   uv run alembic upgrade head
   ```

3. Run the API:

   ```powershell
   Set-Location backend
   uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

4. In a second terminal, install frontend dependencies and start Vite:

   ```powershell
   Set-Location frontend
   pnpm install
   pnpm dev
   ```

5. Open the Vite URL shown in the terminal. Registration sends verification
   messages to `backend\.local-outbox\`; that directory is private, ignored by
   Git, and is not exposed by the API or frontend.

### Optional Gmail delivery for local development

- In your Google Account, enable 2-Step Verification and create an App Password.
  Use that App Password, not your normal Google Account password.
- Copy `backend\.env.example` to `backend\.env`, then set
  `SMTP_USERNAME` and `SMTP_FROM_ADDRESS` to your Gmail address and
  `SMTP_PASSWORD` to the App Password. Keep these values only in
  `backend\.env`; it is ignored by Git.
- The template uses `smtp.gmail.com:587` with certificate-verified STARTTLS.
  Restart the backend after changing the environment file. If SMTP settings
  are absent, email continues to use the local `.local-outbox` fallback.
- Delivery errors do not undo a successful registration or password-reset
  operation; they are reported through a generic server log message.

### Production configuration

- Set `ENVIRONMENT=production`, `DATABASE_URL`, a cryptographically random
  `CSRF_SECRET_KEY` (at least 32 bytes), an HTTPS `FRONTEND_BASE_URL`, and the
  exact `FRONTEND_ORIGIN`. Production requires HTTPS and Secure cookies.
- Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and
  `SMTP_FROM_ADDRESS` together. SMTP always uses certificate-verified
  STARTTLS; plaintext SMTP is not supported. Do not put credentials in the
  frontend or commit them.
- By default, cookies use `SameSite=Lax` and are host-only. For a genuinely
  same-origin deployment, reverse-proxy `/api` to the backend. For direct
  cross-site frontend/API requests, copy `frontend/.env.example` to
  `frontend/.env`, set the public `VITE_API_BASE_URL` before building, use
  HTTPS, and configure `COOKIE_SAMESITE=none` with Secure cookies and exact
  credentialed CORS origins. Browser third-party-cookie restrictions may
  still block cookies; prefer same-site hosting or a reverse proxy when
  possible.
- Email links are built only from `FRONTEND_BASE_URL`, never request headers.
  Token pages use a no-referrer policy and remove tokens from browser history.

## Verification

Run backend unit tests:

```powershell
Set-Location backend
uv run pytest
```

Run the PostgreSQL integration test after configuring `.env`:

```powershell
Set-Location backend
uv run pytest -m integration
```

Build and type-check the frontend:

```powershell
Set-Location frontend
pnpm build
pnpm test
```

The database health endpoint is `http://127.0.0.1:8000/api/v1/health/database`.
Alembic is configured in `backend/alembic.ini`; database schema changes belong
in migrations, not `Base.metadata.create_all()`.

The marked PostgreSQL authentication concurrency test is opt-in. Set
`AUTH_TEST_DATABASE_URL` to a separate disposable PostgreSQL database whose
name ends in `_test` (never the configured development/production database),
then run:

```powershell
Set-Location backend
uv run pytest -m integration
```

The learning schema's PostgreSQL constraint and cascade integration test is
also opt-in. Configure `LEARNING_TEST_DATABASE_URL` for a separate disposable
PostgreSQL database ending in `_test`, distinct from `DATABASE_URL`, then run:

```powershell
Set-Location backend
uv run pytest -m integration tests/test_learning_postgresql_integration.py
```

Do not point either integration-test URL at `viacora_dev` or a production
database. The normal backend test suite applies migrations only to temporary
SQLite databases.

The Phase 5 PostgreSQL/pgvector integration tests are opt-in. Set
`AI_TEST_DATABASE_URL` to a separate disposable PostgreSQL database ending in
`_test`, with pgvector available, and never use `viacora_dev` or a production
database. The tests apply Alembic migrations to that dedicated test database
and validate vector behavior; SQLite does not validate pgvector types,
operators, or indexes.
