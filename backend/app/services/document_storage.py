import hashlib
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings

CHUNK_SIZE = 1024 * 1024
_STORAGE_KEY_PATTERN = re.compile(r"[a-f0-9]{32}\.pdf\Z")
logger = logging.getLogger(__name__)


class UploadTooLarge(Exception):
    pass


class InvalidPdfUpload(Exception):
    pass


def _storage_root() -> Path:
    root = get_settings().document_storage_path
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def storage_path(storage_key: str) -> Path:
    if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise ValueError("Invalid document storage key")
    return _storage_root() / storage_key


def safe_original_filename(filename: str | None) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    normalized = "".join(
        char for char in basename if char.isprintable() and char not in {'"', "/", "\\"}
    ).strip()
    if not normalized.lower().endswith(".pdf"):
        raise InvalidPdfUpload
    if len(normalized) > 255:
        normalized = f"{normalized[:-4][:251]}{normalized[-4:]}"
    return normalized or "document.pdf"


async def store_upload(upload: UploadFile, *, max_bytes: int) -> tuple[str, int, str]:
    root = _storage_root()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=root)
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    total_size = 0
    header = bytearray()
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await upload.read(CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise UploadTooLarge
                if len(header) < 5:
                    header.extend(chunk[: 5 - len(header)])
                if total_size == len(chunk) and not chunk.startswith(b"%PDF-"):
                    raise InvalidPdfUpload
                digest.update(chunk)
                target.write(chunk)
            if total_size == 0 or not header.startswith(b"%PDF-"):
                raise InvalidPdfUpload
            target.flush()
            os.fsync(target.fileno())

        storage_key = f"{uuid.uuid4().hex}.pdf"
        temporary_path.replace(storage_path(storage_key))
        return storage_key, total_size, digest.hexdigest()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def remove_storage_file(storage_key: str) -> None:
    storage_path(storage_key).unlink(missing_ok=True)


def remove_storage_files(storage_keys: list[str]) -> list[str]:
    failed_keys: list[str] = []
    for storage_key in storage_keys:
        try:
            remove_storage_file(storage_key)
        except OSError:
            logger.exception("Could not remove private document file")
            failed_keys.append(storage_key)
    return failed_keys
