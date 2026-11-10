import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class StudyMaterialCard(Base):
    __tablename__ = "study_material_cards"
    __table_args__ = (
        CheckConstraint(
            "material_kind = 'flashcards'",
            name="ck_study_material_cards_material_kind",
        ),
        CheckConstraint("position >= 0", name="ck_study_material_cards_position"),
        CheckConstraint(
            "length(trim(front)) > 0 AND length(trim(back)) > 0",
            name="ck_study_material_cards_content_nonblank",
        ),
        CheckConstraint(
            "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
            name="ck_study_material_cards_difficulty",
        ),
        ForeignKeyConstraint(
            ["user_id", "material_id"],
            ["study_materials.user_id", "study_materials.id"],
            name="fk_study_material_cards_user_material",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["material_id", "material_kind"],
            ["study_materials.id", "study_materials.kind"],
            name="fk_study_material_cards_material_kind",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "material_id",
            "position",
            name="uq_study_material_cards_material_position",
        ),
        UniqueConstraint(
            "material_id",
            "id",
            name="uq_study_material_cards_material_id_id",
        ),
        UniqueConstraint(
            "user_id",
            "id",
            name="uq_study_material_cards_user_id_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    material_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    material_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="flashcards", server_default="flashcards"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    material: Mapped["StudyMaterial"] = relationship(
        back_populates="cards",
        foreign_keys=lambda: [StudyMaterialCard.user_id, StudyMaterialCard.material_id],
    )
    citations: Mapped[list["StudyMaterialCardCitation"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    review_events: Mapped[list["FlashcardReviewEvent"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StudyMaterialCardCitation(Base):
    __tablename__ = "study_material_card_citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["material_id", "card_id"],
            ["study_material_cards.material_id", "study_material_cards.id"],
            name="fk_study_material_card_citations_material_card",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["material_id", "citation_id"],
            ["study_material_citations.study_material_id", "study_material_citations.id"],
            name="fk_study_material_card_citations_material_citation",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "card_id",
            "citation_id",
            name="uq_study_material_card_citations_card_citation",
        ),
        Index(
            "ix_study_material_card_citations_material",
            "material_id",
            "citation_id",
        ),
    )

    material_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    card_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    citation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    card: Mapped[StudyMaterialCard] = relationship(back_populates="citations")
    citation: Mapped["StudyMaterialCitation"] = relationship(viewonly=True)


class FlashcardReviewEvent(Base):
    __tablename__ = "flashcard_review_events"
    __table_args__ = (
        CheckConstraint(
            "rating IN ('remembered', 'needs_review')",
            name="ck_flashcard_review_events_rating",
        ),
        ForeignKeyConstraint(
            ["user_id", "card_id"],
            ["study_material_cards.user_id", "study_material_cards.id"],
            name="fk_flashcard_review_events_user_card",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_flashcard_review_events_user_idempotency",
        ),
        Index(
            "ix_flashcard_review_events_user_card",
            "user_id",
            "card_id",
        ),
        Index(
            "ix_flashcard_review_events_card_reviewed",
            "card_id",
            "reviewed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    card_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    card: Mapped[StudyMaterialCard] = relationship(back_populates="review_events")
