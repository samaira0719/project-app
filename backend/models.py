"""Database models - everything is scoped to a user (user-level database)."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # ---- Data-protection consent (see privacy.py) ----
    # The version of the notice the user actually agreed to, so a later
    # revision can re-prompt only the people who have not seen it.
    privacy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional, independently revocable purposes. Both default to OFF:
    # an unticked box is the only state that can honestly be called consent.
    consent_personalization: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    # Art. 8 self-declaration captured at sign-up.
    age_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    survey: Mapped["SurveyResponse | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["DecisionSnapshot"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    policy: Mapped["UserPolicy | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["DecisionFeedback"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    consents: Mapped[list["ConsentRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SurveyResponse(Base):
    """One personalization survey per user (retaking it updates the row)."""

    __tablename__ = "survey_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="survey")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Study only
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Travel only
    travel_from: Mapped[str | None] = mapped_column(String(120), nullable=True)
    travel_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    travel_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Eisenhower placement the user set by dragging the task to another
    # quadrant. NULL means "wherever the engine computes it". This overrides
    # *placement only* - the score and the rank stay exactly as scored, so the
    # engine remains the single source of numeric truth.
    quadrant_override: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Last computed priority (denormalised for history/insights)
    last_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped[User] = relationship(back_populates="tasks")
    decision: Mapped["TaskDecision | None"] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )


class TaskDecision(Base):
    """The option-level decision made inside one task (re-deciding updates it)."""

    __tablename__ = "task_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)   # options/criteria/ratings
    result: Mapped[dict] = mapped_column(JSON, nullable=False)    # scores/best/clarity/notes
    insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    insight_source: Mapped[str] = mapped_column(String(20), default="engine")
    # Confidence + predicted-satisfaction as they stood when the call was
    # made (see feedback.assess). Frozen rather than recomputed on read:
    # comparing what was predicted then against what the user reports now
    # is the whole calibration signal.
    assessment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    task: Mapped[Task] = relationship(back_populates="decision")
    feedback: Mapped["DecisionFeedback | None"] = relationship(
        back_populates="decision", uselist=False, cascade="all, delete-orphan"
    )


class DecisionSnapshot(Base):
    """A stored ranking run: which task the engine recommended and why."""

    __tablename__ = "decision_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ranking: Mapped[list] = mapped_column(JSON, nullable=False)
    clarity: Mapped[float] = mapped_column(Float, default=0.0)
    insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    insight_source: Mapped[str] = mapped_column(String(20), default="engine")  # gemini (= AVEX) | engine
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="decisions")


# ======================================================================
#  The feedback loop: what happened, what it was worth, and what the
#  engine learned from it. See feedback.py for the update rule.
# ======================================================================


class Interaction(Base):
    """Append-only event log - the training set for the learning engine.

    One row per thing the user did that carries information about whether the
    engine got it right: a question asked, a decision run, a recommendation
    followed or ignored, a rating corrected, a task finished or left to rot.
    `reward` is the scalar the learner consumes; `credit` records which
    factors or criterion tags were responsible, so the reward can be
    apportioned rather than smeared across every parameter equally.
    """

    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interactions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # chat_question | decision_made | recommendation_followed |
    # recommendation_rejected | satisfaction_rated | ratings_corrected |
    # decision_redone | task_completed | task_overdue | quadrant_override
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-form, kind-specific detail (the question text, the option chosen,
    # the quadrant moved from/to). Never contains another user's data.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # Scalar reward in [-1, 1]. NULL for events that only carry context.
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    # tag/factor -> share of the responsibility, summing to about 1.
    credit: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set once the learner has folded this row into the policy, so a replay
    # or a retry can never double-count the same event.
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    user: Mapped[User] = relationship(back_populates="interactions")


class DecisionFeedback(Base):
    """What the user reported back about one decision.

    `predicted_*` are copied from the assessment that was shown at the time,
    so the calibration error is a straight subtraction and stays correct even
    if the scoring code changes later.
    """

    __tablename__ = "decision_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("task_decisions.id"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # followed | modified | rejected - did they act on the recommendation?
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # Which option they actually went with (may differ from the winner).
    chosen_option: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 1-5 stars, mapped to a percentage for display.
    satisfaction: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    predicted_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_satisfaction: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="feedback")
    decision: Mapped[TaskDecision] = relationship(back_populates="feedback")


class UserPolicy(Base):
    """The learned parameters for one user - the "weights" of the loop.

    Deliberately small and inspectable. Every number here is a bounded
    multiplier on a parameter the rule-based engine already had, so the
    learner can tune the engine but never replace it or invent a factor the
    audit panel cannot explain.
    """

    __tablename__ = "user_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # criterion tag -> multiplier, applied on top of the survey's own.
    tag_multipliers: Mapped[dict] = mapped_column(JSON, default=dict)
    # ranking factor (urgency/importance/aging/effort) -> multiplier.
    factor_multipliers: Mapped[dict] = mapped_column(JSON, default=dict)

    # Running estimate of how satisfied this user tends to be (0-1), and how
    # far the predictions have been off, used to recalibrate the next one.
    satisfaction_mean: Mapped[float] = mapped_column(Float, default=0.6)
    calibration_bias: Mapped[float] = mapped_column(Float, default=0.0)
    follow_rate: Mapped[float] = mapped_column(Float, default=0.5)

    # How much evidence is behind the numbers above.
    events: Mapped[int] = mapped_column(Integer, default=0)
    rated_decisions: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_reward: Mapped[float] = mapped_column(Float, default=0.0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="policy")


class ConsentRecord(Base):
    """Append-only proof of every consent granted or withdrawn (GDPR Art. 7(1))."""

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # signup | settings | withdrawal
    source: Mapped[str] = mapped_column(String(20), default="signup")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="consents")
