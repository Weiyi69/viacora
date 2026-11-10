from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIConnectionError, OpenAI
from pydantic import BaseModel

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import AIProviderError
from app.core.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        ai_enabled=True,
        openai_api_key="not-a-real-key",
        ai_pricing_version="test-v1",
        ai_embedding_input_usd_per_million=Decimal("0.02"),
        ai_structured_input_usd_per_million=Decimal("1"),
        ai_structured_output_usd_per_million=Decimal("2"),
    )


def _client() -> MagicMock:
    return MagicMock(spec=OpenAI)


def test_embedding_response_is_ordered_and_usage_is_returned() -> None:
    client = _client()
    vector = [0.25] * 1536
    client.embeddings.create.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=vector),
            SimpleNamespace(index=0, embedding=vector),
        ],
        usage=SimpleNamespace(prompt_tokens=5),
        _request_id="request-id",
    )

    result = OpenAIProvider(_settings(), client).embed(["first", "second"])

    assert result.input_tokens == 5
    assert result.provider_request_id == "request-id"
    assert result.vectors == (tuple(vector), tuple(vector))
    client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small",
        input=["first", "second"],
        dimensions=1536,
    )


@pytest.mark.parametrize(
    "data",
    [
        [SimpleNamespace(index=0, embedding=[0.1] * 1535)],
        [SimpleNamespace(index=0, embedding=[float("nan")] * 1536)],
    ],
)
def test_invalid_embedding_vectors_are_rejected(data: list[object]) -> None:
    client = _client()
    client.embeddings.create.return_value = SimpleNamespace(data=data, usage=None)

    with pytest.raises(AIProviderError, match="provider_invalid_embedding_vector"):
        OpenAIProvider(_settings(), client).embed(["text"])


def test_transient_provider_error_is_safe_and_retryable() -> None:
    client = _client()
    client.embeddings.create.side_effect = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    )

    with pytest.raises(AIProviderError) as error:
        OpenAIProvider(_settings(), client).embed(["text"])

    assert error.value.retryable is True
    assert str(error.value) == "provider_temporarily_unavailable"
    assert "not-a-real-key" not in str(error.value)


def test_structured_output_is_schema_validated_by_provider() -> None:
    class Result(BaseModel):
        label: str

    client = _client()
    parsed = Result(label="validated")
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        _request_id="request-id",
    )

    response = OpenAIProvider(_settings(), client).structured_output(
        "Return a label.",
        "Input",
        Result,
    )

    assert response.value == parsed
    assert response.input_tokens == 3
    assert response.output_tokens == 2
    client.responses.parse.assert_called_once_with(
        model="gpt-4o-mini",
        instructions="Return a label.",
        input="Input",
        text_format=Result,
    )


def test_structured_output_passes_output_token_bound() -> None:
    class Result(BaseModel):
        label: str

    client = _client()
    parsed = Result(label="validated")
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        _request_id="request-id",
    )

    OpenAIProvider(_settings(), client).structured_output(
        "Return a label.",
        "Input",
        Result,
        max_output_tokens=250,
    )

    client.responses.parse.assert_called_once_with(
        model="gpt-4o-mini",
        instructions="Return a label.",
        input="Input",
        text_format=Result,
        max_output_tokens=250,
    )


def test_provider_construction_fails_closed_when_disabled() -> None:
    with pytest.raises(RuntimeError, match="disabled or not configured"):
        OpenAIProvider(Settings(_env_file=None))
