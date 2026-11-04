# Viacora Project Instructions

## Project Overview
Viacora is an AI-powered personalized learning platform that helps users transform learning materials into summaries, flashcards, quizzes, and AI-assisted tutoring experiences.

## Technology Stack
Frontend: Vue 3, TypeScript, Vite, Tailwind CSS, Vue Router, Pinia, TanStack Vue Query, Lucide Vue Next.
Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, Pytest.
Database: PostgreSQL, pgvector.
AI: OpenAI API, embeddings, Retrieval-Augmented Generation.

## Architecture Rules
- Use a modular monolith.
- Keep frontend and backend separated.
- Use REST APIs with /api/v1 versioning.
- Separate routes, services, schemas, models, and repositories.
- Keep AI integration in a dedicated backend service layer.
- Use SQLAlchemy for database access.
- Use Alembic for database migrations.
- Never use create_all() as a replacement for production migrations.

## Coding Standards
- Use TypeScript strict mode.
- Use Python type hints.
- Write reusable Vue components.
- Use Pydantic for API validation.
- Avoid duplicated business logic.
- Avoid unnecessarily large files.
- Use clear naming conventions.
- Handle loading, empty, and error states.
- Write tests for important functionality.

## Security
- Keep API keys on the backend.
- Use Argon2 for password hashing.
- Use secure HTTP-only cookies.
- Implement CSRF protection.
- Validate uploaded files.
- Enforce user ownership checks.
- Never expose another user's documents.
- Never commit secrets or .env files.

## Development Workflow
- Inspect existing code before modifying it.
- Preserve existing working functionality.
- Implement one task at a time.
- Do not skip roadmap phases.
- Run relevant tests after implementation.
- Fix errors before continuing.
- Update documentation when necessary.
- Explain files created or modified.
- Never claim unverified functionality works.

## Development Environment
- Windows 10/11
- VS Code
- PowerShell
- Local PostgreSQL and pgAdmin 4
- Python virtual environment
- pnpm
- Docker is not required for local development.