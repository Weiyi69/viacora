from uuid import uuid4

import pytest
from pydantic import ValidationError

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
