from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PermanentPdfError(Exception):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code


class TransientPdfError(Exception):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code


class StaleDocumentLease(Exception):
    pass


def extract_pdf_pages(
    path: Path,
    *,
    max_pages: int,
    max_text_bytes: int,
    heartbeat: Callable[[], bool],
) -> tuple[list[str], bool]:
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise PermanentPdfError("encrypted_pdf")
        page_count = len(reader.pages)
        if page_count > max_pages:
            raise PermanentPdfError("page_limit_exceeded")
        pages: list[str] = []
        total_bytes = 0
        has_text = False
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            encoded_size = len(text.encode("utf-8"))
            total_bytes += encoded_size
            if total_bytes > max_text_bytes:
                raise PermanentPdfError("text_limit_exceeded")
            has_text = has_text or bool(text.strip())
            pages.append(text)
            if (index + 1) % 10 == 0 and not heartbeat():
                raise StaleDocumentLease
        if not heartbeat():
            raise StaleDocumentLease
        return pages, has_text
    except (PdfReadError, ValueError, IndexError, KeyError, EOFError) as error:
        raise PermanentPdfError("invalid_pdf") from error
    except OSError as error:
        raise TransientPdfError("storage_unavailable") from error
