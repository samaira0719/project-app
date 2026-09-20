"""The privacy endpoints - the notice, consent, and the user's data rights.

A consent screen that promises access, portability, withdrawal and erasure
and then implements none of them is worse than no screen at all, so the
rights listed in privacy.YOUR_RIGHTS each have a working endpoint here.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import privacy as privacy_notice
from ..database import get_db
from ..models import (
    ConsentRecord,
    DecisionFeedback,
    DecisionSnapshot,
    Interaction,
    Task,
    TaskDecision,
    User,
    UserPolicy,
)
from ..schemas import ConsentState, ConsentUpdate
from ..security import get_current_user
from .auth import consent_state, log_consent

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


@router.get("/notice")
def notice() -> dict:
    """The full data-protection notice. Public - it has to be readable
    *before* an account exists, which is the whole point of it."""
    return privacy_notice.notice()


def _history(db: Session, user: User, limit: int = 40) -> list[dict]:
    rows = db.scalars(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user.id)
        .order_by(ConsentRecord.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "scope": row.scope,
            "granted": row.granted,
            "policy_version": row.policy_version,
            "source": row.source,
            "at": row.created_at,
        }
        for row in rows
    ]


@router.get("/consent", response_model=ConsentState)
def get_consent(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsentState:
    return ConsentState(**consent_state(user), history=_history(db, user))


@router.patch("/consent", response_model=ConsentState)
def update_consent(
    body: ConsentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsentState:
    """Grant or withdraw an optional purpose.

    Withdrawal has to be as easy as granting was (GDPR Art. 7(3)), so this is
    the same one-call shape as the sign-up checkbox and takes effect on the
    very next request - there is no confirmation gauntlet on the way out.
    """
    changes = {
        "personalization": body.personalization,
        "ai_processing": body.ai_processing,
    }
    for scope, value in changes.items():
        if value is None:
            continue
        column = "consent_personalization" if scope == "personalization" else "consent_ai"
        if bool(getattr(user, column)) == bool(value):
            continue
        setattr(user, column, bool(value))
        log_consent(db, user, scope, bool(value), "settings" if value else "withdrawal")

    if body.policy_version and body.policy_version == privacy_notice.POLICY_VERSION:
        user.privacy_version = privacy_notice.POLICY_VERSION
        user.privacy_accepted_at = datetime.now(timezone.utc)
        log_consent(db, user, "essential", True, "settings")

    db.commit()
    db.refresh(user)
    return ConsentState(**consent_state(user), history=_history(db, user))


@router.get("/export")
def export_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Everything held about this account, as portable JSON (Art. 15 & 20).

    Deliberately built from the tables rather than from a curated summary -
    if a row exists with this user's id on it, it appears here.
    """
    tasks = db.scalars(select(Task).where(Task.user_id == user.id)).all()
    decisions = db.scalars(
        select(TaskDecision).where(TaskDecision.user_id == user.id)
    ).all()
    snapshots = db.scalars(
        select(DecisionSnapshot).where(DecisionSnapshot.user_id == user.id)
    ).all()
    feedback = db.scalars(
        select(DecisionFeedback).where(DecisionFeedback.user_id == user.id)
    ).all()
    events = db.scalars(
        select(Interaction)
        .where(Interaction.user_id == user.id)
        .order_by(Interaction.created_at)
    ).all()
    policy = db.scalar(select(UserPolicy).where(UserPolicy.user_id == user.id))

    return {
        "exported_at": datetime.now(timezone.utc),
        "policy_version": privacy_notice.POLICY_VERSION,
        "account": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "created_at": user.created_at,
            # The password hash is deliberately not exported: it is a
            # credential, and shipping it in a downloadable file would be a
            # security hole dressed up as transparency.
        },
        "consent": consent_state(user),
        "consent_history": _history(db, user, limit=500),
        "survey": user.survey.answers if user.survey else None,
        "tasks": [
            {
                "id": t.id, "title": t.title, "category": t.category,
                "due_date": t.due_date, "status": t.status,
                "estimated_minutes": t.estimated_minutes,
                "quadrant_override": t.quadrant_override,
                "last_score": t.last_score,
                "created_at": t.created_at, "completed_at": t.completed_at,
            }
            for t in tasks
        ],
        "decisions": [
            {
                "task_id": d.task_id, "payload": d.payload, "result": d.result,
                "assessment": d.assessment, "insight": d.insight,
                "insight_source": d.insight_source, "updated_at": d.updated_at,
            }
            for d in decisions
        ],
        "ranking_snapshots": [
            {
                "id": s.id, "ranking": s.ranking, "clarity": s.clarity,
                "insight": s.insight, "created_at": s.created_at,
            }
            for s in snapshots
        ],
        "decision_feedback": [
            {
                "decision_id": f.decision_id, "outcome": f.outcome,
                "satisfaction": f.satisfaction, "chosen_option": f.chosen_option,
                "note": f.note, "reward": f.reward,
                "predicted_confidence": f.predicted_confidence,
                "predicted_satisfaction": f.predicted_satisfaction,
                "created_at": f.created_at,
            }
            for f in feedback
        ],
        "learning_events": [
            {
                "kind": e.kind, "task_id": e.task_id, "payload": e.payload,
                "reward": e.reward, "credit": e.credit, "created_at": e.created_at,
            }
            for e in events
        ],
        "learned_policy": None if policy is None else {
            "tag_multipliers": policy.tag_multipliers,
            "factor_multipliers": policy.factor_multipliers,
            "satisfaction_mean": policy.satisfaction_mean,
            "calibration_bias": policy.calibration_bias,
            "follow_rate": policy.follow_rate,
            "events": policy.events,
            "rated_decisions": policy.rated_decisions,
            "cumulative_reward": policy.cumulative_reward,
            "updated_at": policy.updated_at,
        },
    }


@router.delete("/learning-data", status_code=status.HTTP_200_OK)
def erase_learning_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Erase the feedback history and reset the learned policy (Art. 17/18).

    The account, the tasks and the decisions survive; only what the engine
    inferred about the user is destroyed. Restriction of processing without
    losing the service.
    """
    events = db.execute(
        delete(Interaction).where(Interaction.user_id == user.id)
    ).rowcount or 0
    ratings = db.execute(
        delete(DecisionFeedback).where(DecisionFeedback.user_id == user.id)
    ).rowcount or 0
    db.execute(delete(UserPolicy).where(UserPolicy.user_id == user.id))
    db.commit()
    return {
        "erased_events": events,
        "erased_feedback": ratings,
        "policy_reset": True,
        "detail": (
            "The learning history is gone and the engine is back to your "
            "survey profile alone."
        ),
    }


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Erase the account and everything attached to it (Art. 17).

    Every child relationship on User is declared `cascade="all, delete-orphan"`,
    so deleting the row takes the survey, tasks, decisions, snapshots,
    feedback, learning events, consent ledger and learned policy with it.
    There is no soft-delete and no recovery window.
    """
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
