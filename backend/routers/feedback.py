"""The feedback loop's endpoints: report an outcome, inspect what was learned.

Nothing here decides anything. It records what happened, hands it to
feedback.py to be turned into a reward, and exposes the resulting policy so
the user can see exactly what the app now believes about them - and delete it
if they disagree (see routers/privacy.py).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import feedback as fb
from ..database import get_db
from ..decision_engine import TAGS
from ..models import Interaction, Task, TaskDecision, User
from ..schemas import (
    DecisionFeedbackIn,
    DecisionFeedbackOut,
    LearningEvent,
    LearningProfile,
)
from ..security import get_current_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

FACTOR_LABELS = {
    "urgency": "Deadline pressure",
    "importance": "Category importance",
    "aging": "How fast a waiting task climbs",
    "effort": "Alarm at a tight time budget",
}

METHOD_BLURB = (
    "Every outcome you report becomes a reward between -1 and +1. That reward "
    "is split across the criteria that actually produced the recommendation - "
    "a criterion that supplied 40% of the winner's points takes 40% of the "
    "credit - and each one's multiplier is then moved by "
    "exp(learning rate x reward x share). The learning rate shrinks as your "
    "history grows, and the whole policy is applied at only as much strength "
    "as your history justifies, which is what the level below measures."
)


def _owned_decision(task_id: int, user: User, db: Session) -> TaskDecision:
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id or task.decision is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No decision on this task to rate yet"
        )
    return task.decision


@router.post("/decision/{task_id}", response_model=DecisionFeedbackOut)
def rate_decision(
    task_id: int,
    body: DecisionFeedbackIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DecisionFeedbackOut:
    """Tell the engine how the call actually went.

    Re-submitting overwrites the earlier rating for the same decision, so
    changing your mind corrects the record rather than double-counting it.
    """
    decision = _owned_decision(task_id, user, db)
    outcome = fb.record_decision_feedback(
        db, user, decision,
        outcome=body.outcome,
        satisfaction=body.satisfaction,
        chosen_option=body.chosen_option,
        note=body.note,
    )
    db.commit()

    row = decision.feedback
    return DecisionFeedbackOut(
        outcome=row.outcome,
        satisfaction=row.satisfaction,
        satisfaction_pct=round((row.satisfaction - 1) / 4 * 100, 1),
        chosen_option=row.chosen_option,
        note=row.note,
        reward=row.reward,
        predicted_satisfaction=row.predicted_satisfaction,
        predicted_confidence=row.predicted_confidence,
        calibration_error_pts=outcome.get("calibration_error_pts"),
        learned=outcome["learned"],
        changes=outcome["changes"],
        created_at=row.created_at,
    )


@router.get("/profile", response_model=LearningProfile)
def learning_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LearningProfile:
    """What the loop has learned, in full, with nothing rounded away."""
    policy = fb.get_policy(db, user)
    state = fb.policy_state(user, policy)

    def rows(space: str, keys, labels) -> list[dict]:
        raw = state["raw_tags"] if space == "tags" else state["raw_factors"]
        applied = state["tags"] if space == "tags" else state["factors"]
        out = []
        for key in keys:
            raw_value = float(raw.get(key, 1.0))
            applied_value = float(applied.get(key, 1.0))
            out.append({
                "key": key,
                "label": labels.get(key, key.title()),
                # What the loop believes...
                "raw_pct": round((raw_value - 1) * 100, 1),
                # ...and how much of that belief is switched on right now.
                "applied_pct": round((applied_value - 1) * 100, 1),
                "direction": (
                    "up" if applied_value > 1.001
                    else "down" if applied_value < 0.999 else "flat"
                ),
                "moved": abs(applied_value - 1) > 0.005,
            })
        return sorted(out, key=lambda r: abs(r["applied_pct"]), reverse=True)

    recent = db.scalars(
        select(Interaction)
        .where(Interaction.user_id == user.id)
        .order_by(Interaction.created_at.desc())
        .limit(20)
    ).all()

    return LearningProfile(
        enabled=state["enabled"],
        level=state["level"],
        max_level=state["max_level"],
        label=state["label"],
        blurb=state["blurb"],
        events=state["events"],
        rated_decisions=state["rated_decisions"],
        trust_pct=state["trust_pct"],
        progress_pct=state["progress_pct"],
        to_next=state["to_next"],
        avg_satisfaction_pct=(
            round(state["satisfaction_mean"] * 100, 1)
            if state["rated_decisions"] else None
        ),
        follow_rate_pct=(
            round(state["follow_rate"] * 100, 1)
            if state["rated_decisions"] else None
        ),
        calibration_bias_pts=round(state["calibration_bias"] * 100, 1),
        cumulative_reward=round(policy.cumulative_reward, 3) if policy else 0.0,
        tags=rows("tags", TAGS, fb.TAG_LABELS),
        factors=rows("factors", fb.FACTORS, FACTOR_LABELS),
        recent=[LearningEvent.model_validate(event) for event in recent],
        method=METHOD_BLURB,
    )


@router.get("/events", response_model=list[LearningEvent])
def events(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Interaction]:
    """The raw event log - the training set, readable by the person in it."""
    return list(db.scalars(
        select(Interaction)
        .where(Interaction.user_id == user.id)
        .order_by(Interaction.created_at.desc())
        .limit(min(max(limit, 1), 200))
    ).all())


@router.get("/pending")
def pending_feedback(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Decisions made but never rated - what the loop is still waiting on.

    Only surfaced for accounts that opted into learning; asking someone who
    declined to feed a model they switched off would be a dark pattern.
    """
    if not fb.learning_enabled(user):
        return {"enabled": False, "items": []}

    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(TaskDecision)
        .where(TaskDecision.user_id == user.id)
        .order_by(TaskDecision.updated_at.desc())
        .limit(50)
    ).all()

    items = []
    for decision in rows:
        if decision.feedback is not None:
            continue
        updated = decision.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        items.append({
            "task_id": decision.task_id,
            "title": decision.task.title if decision.task else "",
            "best": (decision.result or {}).get("best"),
            "decided_at": decision.updated_at,
            "hours_ago": round((now - updated).total_seconds() / 3600, 1),
            "predicted_satisfaction": (
                (decision.assessment or {}).get("satisfaction", {}).get("score")
            ),
        })
    return {"enabled": True, "items": items[:10]}
