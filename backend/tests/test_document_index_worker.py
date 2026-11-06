from decimal import Decimal

from app.core.config import Settings
from app.services.chunking import chunk_document_pages
from app.services.document_indexing import _batch_chunks


def test_embedding_batches_respect_chunk_count_and_utf8_byte_upper_bounds() -> None:
    settings = Settings(
        _env_file=None,
        ai_embedding_batch_max_chunks=2,
        ai_embedding_batch_max_utf8_bytes=12,
    )
    chunks = chunk_document_pages(
        [(1, "alpha beta gamma delta")],
        target_bytes=8,
        max_bytes=8,
        overlap_bytes=0,
    )

    batches = _batch_chunks(chunks, settings)

    assert all(len(batch) <= 2 for batch in batches)
    assert all(
        sum(chunk.utf8_byte_upper_bound for chunk in batch) <= 12 for batch in batches
    )


def test_chunk_token_bound_is_explicitly_a_utf8_byte_bound() -> None:
    chunk = chunk_document_pages([(1, "study material")])[0]

    assert chunk.utf8_byte_upper_bound == len(chunk.text.encode("utf-8"))
