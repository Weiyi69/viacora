from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    FlashcardReviewEvent,
    StudyMaterial,
    StudyMaterialCard,
    StudyKit,
    StudySpace,
    Subject,
)


class ReviewIdempotencyConflict(ValueError):
    pass


def _owned_flashcard(
    session: Session,
    *,
    user_id: UUID,
    material_id: UUID,
    card_id: UUID | None = None,
    lock: bool = False,
) -> StudyMaterialCard | None:
    statement = (
        select(StudyMaterialCard)
        .join(StudyMaterial, StudyMaterial.id == StudyMaterialCard.material_id)
        .join(StudyKit, StudyKit.id == StudyMaterial.study_kit_id)
        .join(Subject, Subject.id == StudyKit.subject_id)
        .join(
            StudySpace,
            (StudySpace.id == StudyKit.study_space_id)
            & (StudySpace.subject_id == StudyKit.subject_id),
        )
        .where(
            StudyMaterial.id == material_id,
            StudyMaterial.kind == "flashcards",
            StudyMaterial.user_id == user_id,
            StudyMaterialCard.user_id == user_id,
            StudyMaterialCard.material_id == material_id,
            StudyKit.user_id == user_id,
            StudyKit.archived_at.is_(None),
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
        )
    )
    if card_id is not None:
        statement = statement.where(StudyMaterialCard.id == card_id)
    if lock:
        statement = statement.with_for_update(of=StudyMaterialCard)
    return session.scalar(statement)


def create_review_event(
    session: Session,
    *,
    user_id: UUID,
    material_id: UUID,
    card_id: UUID,
    rating: str,
    idempotency_key: str,
) -> FlashcardReviewEvent:
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("Idempotency key is invalid")
    if rating not in {"remembered", "needs_review"}:
        raise ValueError("Review rating is invalid")

    existing = session.scalar(
        select(FlashcardReviewEvent).where(
            FlashcardReviewEvent.user_id == user_id,
            FlashcardReviewEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.card_id != card_id or existing.rating != rating:
            raise ReviewIdempotencyConflict(
                "Idempotency key was already used for another review"
            )
        if _owned_flashcard(
            session,
            user_id=user_id,
            material_id=material_id,
            card_id=card_id,
        ) is None:
            raise LookupError("Flashcard not found")
        return existing

    card = _owned_flashcard(
        session,
        user_id=user_id,
        material_id=material_id,
        card_id=card_id,
        lock=True,
    )
    if card is None:
        raise LookupError("Flashcard not found")
    existing = session.scalar(
        select(FlashcardReviewEvent).where(
            FlashcardReviewEvent.user_id == user_id,
            FlashcardReviewEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.card_id != card_id or existing.rating != rating:
            raise ReviewIdempotencyConflict(
                "Idempotency key was already used for another review"
            )
        return existing

    latest_reviewed_at = session.scalar(
        select(func.max(FlashcardReviewEvent.reviewed_at)).where(
            FlashcardReviewEvent.user_id == user_id,
            FlashcardReviewEvent.card_id == card_id,
        )
    )
    reviewed_at = datetime.now(timezone.utc)
    if latest_reviewed_at is not None:
        if latest_reviewed_at.tzinfo is None:
            latest_reviewed_at = latest_reviewed_at.replace(tzinfo=timezone.utc)
        if reviewed_at <= latest_reviewed_at:
            reviewed_at = latest_reviewed_at + timedelta(microseconds=1)

    event = FlashcardReviewEvent(
        id=uuid4(),
        user_id=user_id,
        card_id=card_id,
        rating=rating,
        idempotency_key=idempotency_key,
        reviewed_at=reviewed_at,
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(FlashcardReviewEvent).where(
                FlashcardReviewEvent.user_id == user_id,
                FlashcardReviewEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        if existing.card_id != card_id or existing.rating != rating:
            raise ReviewIdempotencyConflict(
                "Idempotency key was already used for another review"
            ) from None
        event = existing
    session.commit()
    return event


def get_flashcard_progress(
    session: Session, *, user_id: UUID, material_id: UUID
) -> dict[str, object] | None:
    if (
        session.scalar(
            select(StudyMaterial.id)
            .join(StudyKit, StudyKit.id == StudyMaterial.study_kit_id)
            .join(Subject, Subject.id == StudyKit.subject_id)
            .join(
                StudySpace,
                (StudySpace.id == StudyKit.study_space_id)
                & (StudySpace.subject_id == StudyKit.subject_id),
            )
            .where(
                StudyMaterial.id == material_id,
                StudyMaterial.user_id == user_id,
                StudyMaterial.kind == "flashcards",
                StudyKit.user_id == user_id,
                StudyKit.archived_at.is_(None),
                Subject.user_id == user_id,
                Subject.archived_at.is_(None),
                StudySpace.archived_at.is_(None),
            )
        )
        is None
    ):
        return None

    event_counts = (
        select(
            FlashcardReviewEvent.card_id.label("card_id"),
            func.count(FlashcardReviewEvent.id).label("attempts"),
        )
        .where(FlashcardReviewEvent.user_id == user_id)
        .group_by(FlashcardReviewEvent.card_id)
        .subquery()
    )
    ranked_events = (
        select(
            FlashcardReviewEvent.card_id.label("card_id"),
            FlashcardReviewEvent.rating.label("rating"),
            FlashcardReviewEvent.reviewed_at.label("reviewed_at"),
            func.row_number()
            .over(
                partition_by=FlashcardReviewEvent.card_id,
                order_by=(
                    FlashcardReviewEvent.reviewed_at.desc(),
                    FlashcardReviewEvent.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(FlashcardReviewEvent.user_id == user_id)
        .subquery()
    )
    latest_events = (
        select(
            ranked_events.c.card_id,
            ranked_events.c.rating,
            ranked_events.c.reviewed_at,
        )
        .where(ranked_events.c.rank == 1)
        .subquery()
    )
    total_cards = int(
        session.scalar(
            select(func.count(StudyMaterialCard.id)).where(
                StudyMaterialCard.material_id == material_id,
                StudyMaterialCard.user_id == user_id,
            )
        )
        or 0
    )
    rows = session.execute(
        select(
            func.coalesce(event_counts.c.attempts, 0),
            latest_events.c.rating,
            latest_events.c.reviewed_at,
        )
        .select_from(StudyMaterialCard)
        .outerjoin(event_counts, event_counts.c.card_id == StudyMaterialCard.id)
        .outerjoin(latest_events, latest_events.c.card_id == StudyMaterialCard.id)
        .where(
            StudyMaterialCard.material_id == material_id,
            StudyMaterialCard.user_id == user_id,
        )
    ).all()
    reviewed = [row for row in rows if row[1] is not None]
    return {
        "material_id": material_id,
        "total_cards": total_cards,
        "new_cards": total_cards - len(reviewed),
        "reviewed_cards": len(reviewed),
        "remembered_cards": sum(row[1] == "remembered" for row in reviewed),
        "needs_review_cards": sum(row[1] == "needs_review" for row in reviewed),
        "total_attempts": sum(int(row[0]) for row in rows),
        "latest_reviewed_at": max(
            (row[2] for row in reviewed if row[2] is not None),
            default=None,
        ),
    }
