from decimal import Decimal

from app.core.config import Settings
from app.services.ai_usage_reservations import _maximum_cost


def test_embedding_cost_reservation_uses_configured_rate_and_rounds_up() -> None:
    settings = Settings(
        _env_file=None,
        ai_enabled=True,
        openai_api_key="test-key",
        ai_pricing_version="test-v1",
        ai_embedding_input_usd_per_million=Decimal("0.02"),
        ai_structured_input_usd_per_million=Decimal("1"),
        ai_structured_output_usd_per_million=Decimal("2"),
    )

    cost = _maximum_cost(16_384, 0, "document_embedding", settings)

    assert cost == Decimal("0.00032768")
