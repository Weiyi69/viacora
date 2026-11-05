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

## Prerequisites

- Node.js 24 and pnpm 10
- Python 3.12 or newer (tested with Python 3.14)
- PostgreSQL 18 running locally

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
   Git, and is not exposed by the API or frontend. The home dashboard continues
   to report API and database health independently.

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
