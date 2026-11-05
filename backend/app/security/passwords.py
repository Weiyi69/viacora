import secrets
from threading import BoundedSemaphore

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings

_hasher = PasswordHash.recommended()
_argon2_slots = BoundedSemaphore(get_settings().argon2_max_concurrent)
_dummy_password_hash = _hasher.hash(secrets.token_urlsafe(32))


class PasswordHashCapacityError(RuntimeError):
    pass


def _acquire_slot() -> None:
    if not _argon2_slots.acquire(timeout=3):
        raise PasswordHashCapacityError("Password hashing capacity is busy")


def hash_password(password: str) -> str:
    _acquire_slot()
    try:
        return _hasher.hash(password)
    finally:
        _argon2_slots.release()


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _dummy_password_hash
    _acquire_slot()
    try:
        try:
            return _hasher.verify(password, candidate_hash)
        except (UnknownHashError, ValueError, TypeError):
            return False
    finally:
        _argon2_slots.release()
