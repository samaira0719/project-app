"""AVEX insight generation and decision history."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import Query

from .. import behavioral, decision_tempo
from .. import feedback as fb
from ..database import get_db
from ..gemini import generate_insight
from ..models import DecisionSnapshot, Interaction, Task, User
from ..schemas import (
    BehavioralResponse,
    HistoryEntry,
    InsightResponse,
    TempoResponse,
)
from ..scoring import score_tasks
from ..security import get_current_user

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.post("/explain", response_model=InsightResponse)
async def explain_ranking(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InsightResponse:
    """Rank pending tasks, ask AVEX WHY, and store the decision snapshot."""
    pending = db.scalars(
        select(Task).where(Task.user_id == user.id, Task.status == "pending")
    ).all()
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))
    ranked, clarity = score_tasks(list(pending), survey, learned=state["factors"])

    insight, source = await generate_insight(
        ranked, survey, clarity, user.name, ai_allowed=user.consent_ai
    )

    if ranked:
        snapshot = DecisionSnapshot(
            user_id=user.id,
            ranking=[
                {
                    "rank": rank,
                    "task_id": item.task.id,
                    "title": item.task.title,
                    "category": item.task.category,
                    "score": item.score,
                    "factors": item.factors,
                }
                for rank, item in enumerate(ranked, start=1)
            ],
            clarity=clarity,
            insight=insight,
            insight_source=source,
        )
        db.add(snapshot)
        db.commit()

    return InsightResponse(
        insight=insight, source=source, generated_at=datetime.now(timezone.utc)
    )


PARAMETER_LABELS: dict[str, tuple[str, str]] = {
    "urgency": ("Urgency weight", "How much deadline pressure counts, wU"),
    "importance": ("Importance weight", "How much category importance counts, wC"),
    "aging": ("Aging weight", "How fast a waiting task climbs, wA"),
    "effort_bonus": ("Effort bonus", "How loudly a tight time budget alarms"),
    "quick_win": ("Quick-win floor", "How far short tasks are lifted for momentum"),
}


@router.get("/behavioral", response_model=BehavioralResponse)
def behavioral_profile(
    user: User = Depends(get_current_user),
) -> BehavioralResponse:
    """Where this student's survey answers meet the research literature.

    Returns the correlations themselves plus the engine parameters they moved,
    so every claim can be traced to a number that actually changed.
    """
    survey = user.survey.answers if user.survey else None
    deltas = behavioral.engine_deltas(survey)
    parameters = [
        {
            "key": key,
            "label": label,
            "meaning": meaning,
            "base": round(values["base"], 3),
            # What the survey rules set, before the core weights are rescaled...
            "tuned": round(values["raw"], 3),
            # ...and the slice of the score it ends up carrying.
            "share": round(values["share"], 3),
            "moved": abs(values["raw"] - values["base"]) > 5e-3,
            "change_pct": (
                round((values["raw"] - values["base"]) / values["base"] * 100, 0)
                if values["base"] else 0.0
            ),
        }
        for key, (label, meaning) in PARAMETER_LABELS.items()
        for values in [deltas[key]]
    ]
    return BehavioralResponse(
        correlations=behavioral.profile_correlations(survey),
        parameters=parameters,
    )


#: The interaction kinds that represent "a decision was settled". A redo is
#: a decision in its own right for this purpose - it was deliberated over and
#: it took time, so leaving it out would quietly flatter anyone who re-thinks.
_DECISION_KINDS = ("decision_made", "decision_redone", "ratings_corrected")


@router.get("/tempo", response_model=TempoResponse)
def tempo(
    granularity: str | None = Query(
        default=None,
        pattern="^(day|week)$",
        description="Bucket size. Omit to let the span decide.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TempoResponse:
    """Is this student getting faster, and are they deciding more often?

    Reads the append-only interaction log rather than the decision rows,
    because a task holds only its *latest* decision - the log holds every one
    of them, with the timestamp that makes a trend possible at all.

    Nothing is logged without the personalization consent, so a user who has
    not opted in simply has an empty series and gets the "collecting" branch.
    That is the honest answer: there is no data, because they asked for there
    to be none.
    """
    rows = db.scalars(
        select(Interaction)
        .where(
            Interaction.user_id == user.id,
            Interaction.kind.in_(_DECISION_KINDS),
        )
        .order_by(Interaction.created_at)
    ).all()

    events = [
        decision_tempo.DecisionEvent(
            at=row.created_at,
            seconds=float(payload["deliberation_seconds"]),
            options=int(payload.get("options") or 2),
        )
        for row in rows
        for payload in [row.payload or {}]
        # Decisions made before the timer existed carry no seconds. They are
        # skipped rather than guessed at - an imputed latency would be
        # indistinguishable from a real one in the trend.
        if isinstance(payload.get("deliberation_seconds"), (int, float))
    ]

    return TempoResponse(**decision_tempo.compute_tempo(events, granularity))


@router.get("/history", response_model=list[HistoryEntry])
def history(
    limit: int = 25,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DecisionSnapshot]:
    return list(
        db.scalars(
            select(DecisionSnapshot)
            .where(DecisionSnapshot.user_id == user.id)
            .order_by(DecisionSnapshot.created_at.desc())
            .limit(min(max(limit, 1), 100))
        ).all()
    )
