# Viacora

Viacora is a personalized learning platform for turning study materials into
summaries, flashcards, quizzes, and AI-assisted tutoring experiences.

## Phase 1: Foundation

This phase establishes a Vue 3 + TypeScript frontend, a FastAPI backend, a
PostgreSQL connection and health check, and Alembic migration configuration.
The frontend and backend are separate applications in a modular monolith.

## Prerequisites

- Node.js 24 and pnpm 10
- Python 3.12 or newer (tested with Python 3.14)
- PostgreSQL 18 running locally

Docker is not required.

## Local setup (PowerShell)

1. Copy `.env.example` to `backend\.env` and set `DATABASE_URL` to the local
   `viacora_dev` database. Keep `.env` private and never commit it.
2. Install backend dependencies and run the API:

   ```powershell
   Set-Location backend
   uv sync --python 3.14
   uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

3. In a second terminal, install frontend dependencies and start Vite:

   ```powershell
   Set-Location frontend
   pnpm install
   pnpm dev
   ```

4. Open the Vite URL shown in the terminal. The dashboard reports API and
   database health independently.

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
```

The database health endpoint is `http://127.0.0.1:8000/api/v1/health/database`.
Alembic is configured in `backend/alembic.ini`; database schema changes belong
in migrations, not `Base.metadata.create_all()`.
