from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class AIProviderError(Exception):
    def __init__(self, error_code: str, *, retryable: bool) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int | None
    provider_request_id: str | None


@dataclass(frozen=True)
class StructuredOutputResult:
    value: BaseModel
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None


class AIProvider(Protocol):
    def embed(self, texts: list[str]) -> EmbeddingResult: ...

    def structured_output(
        self,
        instructions: str,
        input_text: str,
        output_schema: type[StructuredModel],
    ) -> StructuredOutputResult: ...
