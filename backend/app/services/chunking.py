import hashlib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentChunk:
    chunk_index: int
    page_start: int
    page_end: int
    text: str
    utf8_byte_upper_bound: int
    content_sha256: str


def _split_page(
    text: str,
    *,
    target_bytes: int,
    max_bytes: int,
    overlap_bytes: int,
) -> list[tuple[str, int]]:
    text = unicodedata.normalize("NFC", text)
    if not text.strip():
        return []

    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = start
        byte_count = 0
        last_target_boundary = start
        while end < len(text):
            character_bytes = len(text[end].encode("utf-8"))
            if byte_count + character_bytes > max_bytes:
                break
            byte_count += character_bytes
            end += 1
            if text[end - 1].isspace() and byte_count >= target_bytes:
                last_target_boundary = end
        if end == start:
            raise ValueError("A text code point exceeds the configured chunk maximum")
        if end < len(text) and last_target_boundary > start:
            end = last_target_boundary

        chunk_text = text[start:end].strip()
        if chunk_text:
            normalized = unicodedata.normalize("NFC", chunk_text)
            byte_upper_bound = len(normalized.encode("utf-8"))
            if byte_upper_bound > max_bytes:
                raise ValueError("A text code point exceeds the configured chunk maximum")
            chunks.append((normalized, byte_upper_bound))

        if end >= len(text):
            break
        overlap_start = end
        remaining_overlap = overlap_bytes
        while overlap_start > start and remaining_overlap > 0:
            character_bytes = len(text[overlap_start - 1].encode("utf-8"))
            if character_bytes > remaining_overlap:
                break
            overlap_start -= 1
            remaining_overlap -= character_bytes
        while overlap_start < end and text[overlap_start].isspace():
            overlap_start += 1
        start = overlap_start if start < overlap_start < end else end

    return chunks


def chunk_document_pages(
    pages: Sequence[tuple[int, str]],
    *,
    target_bytes: int = 800,
    max_bytes: int = 1024,
    overlap_bytes: int = 100,
) -> list[DocumentChunk]:
    if target_bytes < 1 or max_bytes < 1 or target_bytes > max_bytes:
        raise ValueError("Chunk target and maximum byte sizes are invalid")
    if overlap_bytes < 0 or overlap_bytes >= max_bytes:
        raise ValueError("Chunk overlap must be smaller than the maximum byte size")

    page_numbers = [page_number for page_number, _ in pages]
    if any(page_number < 1 for page_number in page_numbers):
        raise ValueError("Page numbers must be positive")
    if page_numbers != sorted(set(page_numbers)):
        raise ValueError("Page numbers must be unique and in ascending order")

    chunks: list[DocumentChunk] = []
    for page_number, text in pages:
        for chunk_text, utf8_byte_upper_bound in _split_page(
            text,
            target_bytes=target_bytes,
            max_bytes=max_bytes,
            overlap_bytes=overlap_bytes,
        ):
            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            chunks.append(
                DocumentChunk(
                    chunk_index=len(chunks),
                    page_start=page_number,
                    page_end=page_number,
                    text=chunk_text,
                    utf8_byte_upper_bound=utf8_byte_upper_bound,
                    content_sha256=content_hash,
                )
            )
    return chunks
