from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import Base


def test_ai_models_are_registered_without_using_create_all() -> None:
    expected_tables = {
        "ai_embedding_profiles",
        "document_index_generations",
        "document_index_heads",
        "document_index_jobs",
        "document_chunks",
        "document_chunk_embeddings",
        "ai_usage_events",
        "ai_usage_reservations",
    }

    assert expected_tables <= set(Base.metadata.tables)


def test_model_metadata_compiles_for_postgresql_without_connecting() -> None:
    compiled = {
        table.name: str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in Base.metadata.sorted_tables
    }

    assert "VECTOR(1536)" in compiled["document_chunk_embeddings"]


def test_migration_creates_extension_before_vector_objects_and_keeps_it_on_downgrade() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261106_04_ai_foundation.py"
    )
    migration = migration_path.read_text(encoding="utf-8")
    upgrade = migration.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = migration.split("def downgrade() -> None:", 1)[1]

    assert upgrade.index("_create_extension_if_available()") < upgrade.index(
        '"document_chunk_embeddings"'
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "Vector(1536)" in migration
    assert "USING hnsw (embedding vector_cosine_ops)" in migration
    assert "DROP EXTENSION" not in downgrade.upper()
