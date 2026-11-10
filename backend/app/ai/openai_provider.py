import math
from typing import TypeVar

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

from app.ai.provider import (
    AIProviderError,
    EmbeddingResult,
    StructuredOutputResult,
)
from app.core.config import Settings

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

_RETRYABLE_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


class OpenAIProvider:
    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        if not settings.ai_enabled or not settings.openai_api_key:
            raise RuntimeError("AI provider is disabled or not configured")
        key = settings.openai_api_key.get_secret_value()
        if not key:
            raise RuntimeError("AI provider is disabled or not configured")
        self._settings = settings
        self._client = client or OpenAI(
            api_key=key,
            timeout=settings.ai_provider_timeout_seconds,
            max_retries=0,
        )

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text")
        try:
            response = self._client.embeddings.create(
                model=self._settings.ai_embedding_model,
                input=texts,
                dimensions=self._settings.ai_embedding_dimensions,
            )
        except _RETRYABLE_EXCEPTIONS:
            raise AIProviderError("provider_temporarily_unavailable", retryable=True) from None
        except APIStatusError as error:
            retryable = error.status_code == 429 or error.status_code >= 500
            code = "provider_temporarily_unavailable" if retryable else "provider_request_rejected"
            raise AIProviderError(code, retryable=retryable) from None
        except APIError:
            raise AIProviderError("provider_request_failed", retryable=False) from None

        data = sorted(response.data, key=lambda item: item.index)
        if len(data) != len(texts) or [item.index for item in data] != list(range(len(texts))):
            raise AIProviderError("provider_invalid_embedding_count", retryable=False)

        vectors = tuple(tuple(float(value) for value in item.embedding) for item in data)
        if any(
            len(vector) != self._settings.ai_embedding_dimensions
            or any(not math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise AIProviderError("provider_invalid_embedding_vector", retryable=False)

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
        if input_tokens is not None and (not isinstance(input_tokens, int) or input_tokens < 0):
            input_tokens = None
        return EmbeddingResult(
            vectors=vectors,
            input_tokens=input_tokens,
            provider_request_id=getattr(response, "_request_id", None),
        )

    def structured_output(
        self,
        instructions: str,
        input_text: str,
        output_schema: type[StructuredModel],
        *,
        max_output_tokens: int | None = None,
    ) -> StructuredOutputResult:
        try:
            if max_output_tokens is None:
                response = self._client.responses.parse(
                    model=self._settings.ai_structured_output_model,
                    instructions=instructions,
                    input=input_text,
                    text_format=output_schema,
                )
            else:
                response = self._client.responses.parse(
                    model=self._settings.ai_structured_output_model,
                    instructions=instructions,
                    input=input_text,
                    text_format=output_schema,
                    max_output_tokens=max_output_tokens,
                )
        except _RETRYABLE_EXCEPTIONS:
            raise AIProviderError("provider_temporarily_unavailable", retryable=True) from None
        except APIStatusError as error:
            retryable = error.status_code == 429 or error.status_code >= 500
            code = "provider_temporarily_unavailable" if retryable else "provider_request_rejected"
            raise AIProviderError(code, retryable=retryable) from None
        except APIError:
            raise AIProviderError("provider_request_failed", retryable=False) from None

        value = getattr(response, "output_parsed", None)
        if not isinstance(value, output_schema):
            raise AIProviderError("provider_invalid_structured_output", retryable=False)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        return StructuredOutputResult(
            value=value,
            input_tokens=input_tokens if isinstance(input_tokens, int) and input_tokens >= 0 else None,
            output_tokens=(
                output_tokens
                if isinstance(output_tokens, int) and output_tokens >= 0
                else None
            ),
            provider_request_id=getattr(response, "_request_id", None),
        )
