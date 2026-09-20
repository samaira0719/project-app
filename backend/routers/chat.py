"""The assistant bubble: one endpoint, grounded in the caller's own account.

Every turn rebuilds the student's snapshot from the database rather than
trusting anything the client sends, so the assistant can never be talked into
citing data that is not theirs. The client only replays the conversation text.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import chat_fallback
from .. import feedback as fb
from ..database import get_db
from ..gemini import chat_reply
from ..models import DecisionSnapshot, Task, TaskDecision, User
from ..Prompts import build_user_context
from ..schemas import ChatRequest, ChatResponse
from ..scoring import score_tasks
from ..security import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

# How much history to put in front of the model. Enough to answer "what about
# the second one?", short enough that the prompt stays mostly account data.
DONE_LIMIT = 12
DECISION_LIMIT = 10
SNAPSHOT_LIMIT = 5


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _stats(tasks: list[Task]) -> dict:
    now = datetime.now(timezone.utc)
    week_ahead = now + timedelta(days=7)
    pending = [t for t in tasks if t.status == "pending"]
    completed = [t for t in tasks if t.status == "done"]
    by_category: dict[str, int] = {}
    for task in tasks:
        by_category[task.category] = by_category.get(task.category, 0) + 1
    return {
        "total": len(tasks),
        "pending": len(pending),
        "completed": len(completed),
        "completion_rate": len(completed) / len(tasks) if tasks else 0.0,
        "by_category": by_category,
        "overdue": sum(1 for t in pending if _aware(t.due_date) < now),
        "due_this_week": sum(
            1 for t in pending if now <= _aware(t.due_date) <= week_ahead
        ),
    }


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Answer one message about this student's tasks, or about the app."""
    tasks = list(db.scalars(select(Task).where(Task.user_id == user.id)).all())
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))

    pending = [t for t in tasks if t.status == "pending"]
    ranked, clarity = score_tasks(pending, survey, learned=state["factors"])

    done = sorted(
        (t for t in tasks if t.status == "done"),
        key=lambda t: _aware(t.completed_at or t.created_at),
        reverse=True,
    )[:DONE_LIMIT]

    decisions = list(
        db.scalars(
            select(TaskDecision)
            .where(TaskDecision.user_id == user.id)
            .order_by(TaskDecision.updated_at.desc())
            .limit(DECISION_LIMIT)
        ).all()
    )
    snapshots = list(
        db.scalars(
            select(DecisionSnapshot)
            .where(DecisionSnapshot.user_id == user.id)
            .order_by(DecisionSnapshot.created_at.desc())
            .limit(SNAPSHOT_LIMIT)
        ).all()
    )
    stats = _stats(tasks)

    context = build_user_context(
        name=user.name,
        email=user.email,
        survey=survey,
        ranked=ranked,
        clarity=clarity,
        done_tasks=done,
        decisions=decisions,
        stats=stats,
        snapshots=snapshots,
    )

    # No AI-processing consent means the message never leaves this server.
    # The rule-based matcher below answers from the same numbers, so the
    # bubble still works - it just does not phone Google.
    if user.consent_ai:
        reply, source = await chat_reply(
            context,
            [turn.model_dump() for turn in body.history],
            body.message,
        )
    else:
        reply, source = "", "engine"

    if source != "gemini" or not reply:
        # No key, or the model was unreachable. The deterministic matcher
        # answers from the same numbers, so the bubble never dead-ends.
        reply = chat_fallback.answer(
            message=body.message,
            name=user.name,
            ranked=ranked,
            clarity=clarity,
            stats=stats,
            decisions=decisions,
            has_survey=survey is not None,
        )
        source = "engine"

    # The questions a student asks are part of the feedback data: they say
    # what the engine failed to make obvious on screen. Stored only with the
    # personalization consent, and only ever read back to this same account.
    fb.record_event(
        db, user, "chat_question",
        payload={
            "message": body.message[:500],
            "source": source,
            "pending_tasks": len(pending),
        },
    )
    db.commit()

    return ChatResponse(
        reply=reply, source=source, generated_at=datetime.now(timezone.utc)
    )
