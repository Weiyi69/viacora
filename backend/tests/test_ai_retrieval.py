from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.schemas.ai import (
    AIRetrievalPassage,
    AIRetrievalRequest,
    AIRetrievalResponse,
    AISourceReference,
)


def test_retrieval_request_bounds_and_forbids_unknown_fields() -> None:
    request = AIRetrievalRequest(query="  cell biology  ")
    assert request.query == "cell biology"
    assert request.top_k == 5
    with pytest.raises(ValidationError):
        AIRetrievalRequest(query="x" * 2049)
    with pytest.raises(ValidationError):
        AIRetrievalRequest(query="query", generated_answer="not in phase 5")


def test_retrieval_source_response_contains_passages_and_page_references_only() -> None:
    response = AIRetrievalResponse(
        passages=[
            AIRetrievalPassage(
                text="Retrieved excerpt",
                similarity=0.82,
                source=AISourceReference(
                    document_id=uuid4(),
                    document_name="Biology notes.pdf",
                    page_start=3,
                    page_end=3,
                ),
            )
        ]
    )
    serialized = response.model_dump()
    assert serialized["passages"][0]["source"]["page_start"] == 3
    assert "answer" not in serialized
    assert "prompt" not in serialized


def test_pinned_retrieval_sql_uses_exact_document_generations() -> None:
    from app.services.ai_retrieval import retrieve_passages

    user_id = uuid4()
    first_document, second_document = uuid4(), uuid4()
    first_generation, second_generation = uuid4(), uuid4()
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    settings = Settings(
        _env_file=None,
        ai_embedding_profile_id="test-profile",
        ai_embedding_dimensions=1536,
        ai_embedding_model="test-embedding-model",
        ai_retrieval_max_top_k=8,
    )

    with (
        patch(
            "app.services.ai_retrieval.embed_query",
            return_value=tuple([0.0] * 1536),
        ),
        patch(
            "app.services.ai_retrieval._configured_profile",
            return_value=MagicMock(profile_id="test-profile"),
        ),
    ):
        retrieve_passages(
            session,
            user_id=user_id,
            query="nursing concepts",
            document_ids=[first_document, second_document],
            source_generations={
                first_document: first_generation,
                second_document: second_generation,
            },
            top_k=2,
            settings=settings,
        )

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert str(compiled).count("row_number()") == 1
    assert first_document in compiled.params.values()
    assert second_document in compiled.params.values()
    assert first_generation in compiled.params.values()
    assert second_generation in compiled.params.values()
