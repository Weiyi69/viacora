import pytest

from app.services.rate_limit import RateLimit, RateLimitUnavailable, check_rate_limits


def test_rate_limit_counts_atomically_and_rejects_after_limit(auth_client) -> None:
    limit = RateLimit("test-limit", "isolated-identity", 2)

    assert check_rate_limits([limit]) is True
    assert check_rate_limits([limit]) is True
    assert check_rate_limits([limit]) is False


def test_rate_limit_fails_closed_when_storage_is_unavailable(monkeypatch) -> None:
    from app.services import rate_limit

    def unavailable_factory():
        raise OSError("database unavailable")

    monkeypatch.setattr(rate_limit, "get_session_factory", unavailable_factory)

    with pytest.raises(RateLimitUnavailable):
        check_rate_limits([RateLimit("test-limit", "database-error", 1)])
