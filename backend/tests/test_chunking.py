import pytest

from app.services.chunking import chunk_document_pages


def test_chunking_is_deterministic_and_preserves_page_sources() -> None:
    pages = [(1, "First page content."), (2, "Second page content.")]

    chunks = chunk_document_pages(pages, target_bytes=100, max_bytes=120, overlap_bytes=10)

    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (2, 2)]
    assert chunk_document_pages(
        pages, target_bytes=100, max_bytes=120, overlap_bytes=10
    ) == chunks
    assert all(len(chunk.content_sha256) == 64 for chunk in chunks)


def test_long_unicode_page_is_split_with_bounded_conservative_token_estimates() -> None:
    text = ("Study notes include Unicode: café, 東京, and 😀. " * 20).strip()

    chunks = chunk_document_pages(
        [(4, text)],
        target_bytes=80,
        max_bytes=100,
        overlap_bytes=12,
    )

    assert len(chunks) > 1
    assert all(chunk.page_start == chunk.page_end == 4 for chunk in chunks)
    assert all(chunk.utf8_byte_upper_bound <= 100 for chunk in chunks)
    assert all(chunk.utf8_byte_upper_bound == len(chunk.text.encode("utf-8")) for chunk in chunks)
    assert all("\ufffd" not in chunk.text for chunk in chunks)


def test_empty_pages_are_skipped_and_page_numbers_are_validated() -> None:
    assert chunk_document_pages([(1, " \n "), (2, "")]) == []

    with pytest.raises(ValueError, match="positive"):
        chunk_document_pages([(0, "text")])
    with pytest.raises(ValueError, match="ascending"):
        chunk_document_pages([(2, "text"), (1, "text")])


def test_chunk_overlap_repeats_a_bounded_tail() -> None:
    chunks = chunk_document_pages(
        [(1, "alpha beta gamma delta epsilon zeta eta theta")],
        target_bytes=12,
        max_bytes=18,
        overlap_bytes=6,
    )

    assert len(chunks) > 1
    assert any(
        set(left.text.split()) & set(right.text.split())
        for left, right in zip(chunks, chunks[1:])
    )
