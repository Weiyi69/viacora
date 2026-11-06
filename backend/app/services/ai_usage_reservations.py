from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.models import AIUsageEvent, AIUsageReservation, User

ACTIVE_STATUSES = ("reserved", "dispatching", "uncertain")
RECONCILIATION_GRACE = timedelta(minutes=15)
MONEY_QUANTUM = Decimal("0.00000001")


class UsageLimitExceeded(Exception):
    pass


class InvalidReservationTransition(Exception):
    pass


def _rates(settings: Settings, operation: str) -> tuple[Decimal, Decimal]:
    if operation in {"query_embedding", "document_embedding"}:
        input_rate = settings.ai_embedding_input_usd_per_million
        output_rate = Decimal(0)
    else:
        input_rate = settings.ai_structured_input_usd_per_million
        output_rate = settings.ai_structured_output_usd_per_million
    if input_rate is None or output_rate is None or not settings.ai_pricing_version:
        raise RuntimeError("AI pricing configuration is incomplete")
    return input_rate, output_rate


def _maximum_cost(
    input_tokens: int,
    output_tokens: int,
    operation: str,
    settings: Settings,
) -> Decimal:
    input_rate, output_rate = _rates(settings, operation)
    cost = (
        Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)
    return max(cost.quantize(MONEY_QUANTUM, rounding=ROUND_UP), MONEY_QUANTUM)


def _daily_totals(session: Session, user_id: UUID, since: datetime) -> tuple[int, Decimal]:
    settled = session.execute(
        select(
            func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.estimated_cost_usd), 0),
        ).where(AIUsageEvent.user_id == user_id, AIUsageEvent.occurred_at >= since)
    ).one()
    reserved = session.execute(
        select(
            func.coalesce(func.sum(AIUsageReservation.reserved_input_tokens), 0),
            func.coalesce(func.sum(AIUsageReservation.reserved_cost_usd), 0),
        ).where(
            AIUsageReservation.user_id == user_id,
            AIUsageReservation.status.in_(ACTIVE_STATUSES),
        )
    ).one()
    return int(settled[0]) + int(reserved[0]), Decimal(settled[1]) + Decimal(reserved[1])


def reserve_usage(
    session: Session,
    *,
    user_id: UUID,
    operation: str,
    idempotency_key: str,
    input_token_upper_bound: int,
    output_token_upper_bound: int = 0,
    document_id: UUID | None = None,
    settings: Settings | None = None,
) -> AIUsageReservation:
    settings = settings or get_settings()
    if not settings.ai_enabled:
        raise RuntimeError("AI provider is disabled")
    if input_token_upper_bound < 1 or output_token_upper_bound < 0:
        raise ValueError("Reservation token bounds are invalid")
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("Reservation idempotency key is invalid")
    cost = _maximum_cost(
        input_token_upper_bound,
        output_token_upper_bound,
        operation,
        settings,
    )
    if (
        input_token_upper_bound > settings.ai_daily_input_token_limit
        or cost > settings.ai_daily_cost_limit_usd
    ):
        raise UsageLimitExceeded

    now = datetime.now(timezone.utc)
    existing = session.scalar(
        select(AIUsageReservation).where(
            AIUsageReservation.user_id == user_id,
            AIUsageReservation.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if (
            existing.operation != operation
            or existing.reserved_input_tokens != input_token_upper_bound
            or existing.reserved_output_tokens != output_token_upper_bound
            or existing.document_id != document_id
        ):
            raise ValueError("Reservation idempotency key was reused with different bounds")
        return existing

    if session.scalar(select(User.id).where(User.id == user_id).with_for_update()) is None:
        raise LookupError("User does not exist")
    existing = session.scalar(
        select(AIUsageReservation)
        .where(
            AIUsageReservation.user_id == user_id,
            AIUsageReservation.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.operation != operation
            or existing.reserved_input_tokens != input_token_upper_bound
            or existing.reserved_output_tokens != output_token_upper_bound
            or existing.document_id != document_id
        ):
            raise ValueError("Reservation idempotency key was reused with different bounds")
        return existing

    session.execute(
        update(AIUsageReservation)
        .where(
            AIUsageReservation.user_id == user_id,
            AIUsageReservation.status == "reserved",
            AIUsageReservation.expires_at <= now,
        )
        .values(status="released", finalized_at=now)
    )
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used_tokens, used_cost = _daily_totals(session, user_id, window_start)
    if (
        used_tokens + input_token_upper_bound > settings.ai_daily_input_token_limit
        or used_cost + cost > settings.ai_daily_cost_limit_usd
    ):
        session.rollback()
        raise UsageLimitExceeded

    reservation = AIUsageReservation(
        id=uuid4(),
        user_id=user_id,
        document_id=document_id,
        operation=operation,
        idempotency_key=idempotency_key,
        reserved_input_tokens=input_token_upper_bound,
        reserved_output_tokens=output_token_upper_bound,
        reserved_cost_usd=cost,
        pricing_version=settings.ai_pricing_version or "",
        model_name=(
            settings.ai_embedding_model
            if operation in {"query_embedding", "document_embedding"}
            else settings.ai_structured_output_model
        ),
        profile_id=settings.ai_embedding_profile_id,
        status="reserved",
        created_at=now,
        expires_at=now + timedelta(seconds=settings.ai_provider_timeout_seconds)
        + RECONCILIATION_GRACE,
    )
    session.add(reservation)
    session.commit()
    session.refresh(reservation)
    return reservation


def mark_dispatching(session: Session, reservation_id: UUID) -> AIUsageReservation:
    reservation = session.scalar(
        select(AIUsageReservation)
        .where(AIUsageReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None or reservation.status != "reserved":
        raise InvalidReservationTransition
    now = datetime.now(timezone.utc)
    reservation.status = "dispatching"
    reservation.dispatch_started_at = now
    reservation.expires_at = now + timedelta(seconds=120) + RECONCILIATION_GRACE
    session.commit()
    session.refresh(reservation)
    return reservation


def release_undispatched(session: Session, reservation_id: UUID) -> bool:
    result = session.execute(
        update(AIUsageReservation)
        .where(
            AIUsageReservation.id == reservation_id,
            AIUsageReservation.status == "reserved",
        )
        .values(status="released", finalized_at=datetime.now(timezone.utc))
    )
    session.commit()
    return result.rowcount == 1


def release_known_nonbillable(session: Session, reservation_id: UUID) -> None:
    reservation = session.scalar(
        select(AIUsageReservation)
        .where(AIUsageReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None or reservation.status != "dispatching":
        raise InvalidReservationTransition
    reservation.status = "released"
    reservation.finalized_at = datetime.now(timezone.utc)
    session.commit()


def mark_uncertain(
    session: Session,
    reservation_id: UUID,
    *,
    provider_request_id: str | None = None,
) -> None:
    reservation = session.scalar(
        select(AIUsageReservation)
        .where(AIUsageReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise LookupError("Reservation does not exist")
    if reservation.status == "uncertain":
        return
    if reservation.status != "dispatching":
        raise InvalidReservationTransition
    now = datetime.now(timezone.utc)
    reservation.status = "uncertain"
    reservation.provider_request_id = provider_request_id
    reservation.expires_at = now + RECONCILIATION_GRACE
    session.commit()


def settle_usage(
    session: Session,
    reservation_id: UUID,
    *,
    input_tokens: int | None,
    output_tokens: int | None = None,
    provider_request_id: str | None = None,
    conservative_estimate: bool = False,
    settings: Settings | None = None,
) -> AIUsageEvent:
    settings = settings or get_settings()
    reservation = session.scalar(
        select(AIUsageReservation)
        .where(AIUsageReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise LookupError("Reservation does not exist")
    existing = session.scalar(
        select(AIUsageEvent).where(AIUsageEvent.reservation_id == reservation_id)
    )
    if existing is not None:
        if reservation.status not in {"reconciled", "estimated"}:
            reservation.status = "reconciled"
            reservation.finalized_at = datetime.now(timezone.utc)
            session.commit()
        return existing
    if reservation.status not in {"dispatching", "uncertain"}:
        raise InvalidReservationTransition

    if conservative_estimate:
        settled_input = reservation.reserved_input_tokens
        settled_output = reservation.reserved_output_tokens
        cost = reservation.reserved_cost_usd
        basis = "conservative_estimate"
    else:
        if input_tokens is None or input_tokens < 0:
            raise ValueError("Provider-reported input usage is required")
        settled_input = input_tokens
        settled_output = output_tokens or 0
        if settled_output < 0:
            raise ValueError("Provider-reported output usage cannot be negative")
        cost = _maximum_cost(
            settled_input,
            settled_output,
            reservation.operation,
            settings,
        )
        basis = "provider_reported"

    event = AIUsageEvent(
        id=uuid4(),
        user_id=reservation.user_id,
        document_id=reservation.document_id,
        reservation_id=reservation.id,
        operation=reservation.operation,
        model_name=reservation.model_name,
        profile_id=reservation.profile_id,
        input_tokens=settled_input,
        output_tokens=settled_output,
        estimated_cost_usd=cost,
        pricing_version=reservation.pricing_version,
        accounting_basis=basis,
        provider_request_id=provider_request_id or reservation.provider_request_id,
        idempotency_key=f"reservation:{reservation.id}",
    )
    session.add(event)
    reservation.status = "estimated" if conservative_estimate else "reconciled"
    reservation.finalized_at = datetime.now(timezone.utc)
    if provider_request_id:
        reservation.provider_request_id = provider_request_id
    session.commit()
    session.refresh(event)
    return event


def recover_expired_reservations(session_factory: sessionmaker) -> int:
    now = datetime.now(timezone.utc)
    recovered = 0
    with session_factory() as session:
        undispatched_ids = list(
            session.scalars(
                select(AIUsageReservation.id)
                .where(
                    AIUsageReservation.status == "reserved",
                    AIUsageReservation.expires_at <= now,
                )
                .order_by(AIUsageReservation.expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        if undispatched_ids:
            session.execute(
                update(AIUsageReservation)
                .where(
                    AIUsageReservation.id.in_(undispatched_ids),
                    AIUsageReservation.status == "reserved",
                )
                .values(status="released", finalized_at=now)
            )
            recovered += len(undispatched_ids)
        dispatching_ids = list(
            session.scalars(
                select(AIUsageReservation.id)
                .where(
                    AIUsageReservation.status == "dispatching",
                    AIUsageReservation.expires_at <= now,
                )
                .order_by(AIUsageReservation.expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        if dispatching_ids:
            session.execute(
                update(AIUsageReservation)
                .where(
                    AIUsageReservation.id.in_(dispatching_ids),
                    AIUsageReservation.status == "dispatching",
                )
                .values(
                    status="uncertain",
                    expires_at=now + RECONCILIATION_GRACE,
                )
            )
            recovered += len(dispatching_ids)
        uncertain_ids = list(
            session.scalars(
                select(AIUsageReservation.id)
                .where(
                    AIUsageReservation.status == "uncertain",
                    AIUsageReservation.expires_at <= now,
                )
                .order_by(AIUsageReservation.expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        session.commit()
        for reservation_id in uncertain_ids:
            settle_usage(
                session,
                reservation_id,
                input_tokens=None,
                conservative_estimate=True,
            )
            recovered += 1
    return recovered
