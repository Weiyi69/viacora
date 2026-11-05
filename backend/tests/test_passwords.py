from app.security.passwords import hash_password, verify_password


def test_password_hashing_uses_argon2_and_dummy_hash_for_missing_account() -> None:
    password = "a-secure-password-123"
    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password, password_hash) is True
    assert verify_password("incorrect-password", password_hash) is False
    assert verify_password("incorrect-password", None) is False
