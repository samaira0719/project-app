"""Task CRUD plus the auto-prioritized ranking endpoint."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import behavioral, decision_engine
from .. import feedback as fb
from ..database import get_db
from ..gemini import generate_decision_insight, generate_option_ratings
from ..models import Task, TaskDecision, User
from ..schemas import (
    AutoDecideRequest,
    DecideRequest,
    DecisionOut,
    FactorBreakdown,
    PrioritizedResponse,
    QuadrantOut,
    QuadrantUpdate,
    StatsResponse,
    TaskIn,
    TaskOut,
)
from ..scoring import QUADRANT_KEYS, score_tasks
from ..security import get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _owned_task(task_id: int, user: User, db: Session) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = Task(user_id=user.id, **body.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/prioritized", response_model=PrioritizedResponse)
def prioritized(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PrioritizedResponse:
    """Pending tasks ranked by the scoring engine, with factor breakdowns."""
    pending = db.scalars(
        select(Task).where(Task.user_id == user.id, Task.status == "pending")
    ).all()
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))
    ranked, clarity = score_tasks(list(pending), survey, learned=state["factors"])

    out: list[TaskOut] = []
    buckets: dict[str, list[int]] = {key: [] for key in QUADRANT_KEYS}
    for rank, item in enumerate(ranked, start=1):
        item.task.last_score = item.score
        entry = TaskOut.model_validate(item.task)
        entry.score = item.score
        entry.rank = rank
        entry.reason = item.reason
        entry.factors = FactorBreakdown(**item.factors, weights=item.weights)
        entry.urgent = item.urgent
        entry.important = item.important
        entry.computed_quadrant = item.quadrant
        # A task the user dragged elsewhere sits where they put it. This moves
        # placement only - score, rank and factors above are untouched.
        override = item.task.quadrant_override
        entry.quadrant_overridden = override in QUADRANT_KEYS and override != item.quadrant
        entry.quadrant = override if override in QUADRANT_KEYS else item.quadrant
        if item.task.decision is not None:
            entry.decided_option = item.task.decision.result.get("best")
        buckets[entry.quadrant].append(entry.id)
        out.append(entry)
    db.commit()  # persist last_score

    # The matrix reads the same U and C factors as the ranking, so the grid
    # and the list can never tell the student two different stories.
    matrix = [
        QuadrantOut(
            key=key,
            task_ids=buckets[key],
            **behavioral.QUADRANT_SCIENCE[key],
        )
        for key in QUADRANT_KEYS
    ]

    return PrioritizedResponse(
        tasks=out,
        clarity=clarity,
        top_task_id=out[0].id if out else None,
        matrix=matrix,
        matrix_notes=behavioral.matrix_correlations(
            {key: len(ids) for key, ids in buckets.items()},
            overrides=sum(1 for t in out if t.quadrant_overridden),
        ),
    )


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status_filter: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    query = select(Task).where(Task.user_id == user.id).order_by(Task.created_at.desc())
    if status_filter in ("pending", "done"):
        query = query.where(Task.status == status_filter)
    out = []
    for task in db.scalars(query).all():
        entry = TaskOut.model_validate(task)
        if task.decision is not None:
            entry.decided_option = task.decision.result.get("best")
        out.append(entry)
    return out


# ---------- Per-task decisions ----------

@router.get("/decision-template/{category}")
def decision_template(
    category: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Suggested options prompt + criteria for this task category,
    plus how the user's survey will bend the weights."""
    template = decision_engine.get_template(category)
    survey = user.survey.answers if user.survey else None
    _, notes = decision_engine.survey_multipliers(survey)
    return {**template, "personalization_notes": notes}


def _decision_out(decision: TaskDecision, user: User) -> DecisionOut:
    """Serialise a decision, deriving the audit from what was stored.

    The audit is computed on read rather than saved, so decisions made before
    the Audit panel existed get one the moment they are re-opened. The
    assessment is the opposite: it is *frozen* at the moment the call was
    made, because the whole point of it is to be compared against what the
    user later reports. Only a decision that never had one gets it filled in
    here, and even then it is not written back.
    """
    survey = user.survey.answers if user.survey else None
    audit = decision_engine.build_audit(decision.payload, decision.result)
    assessment = decision.assessment
    if not assessment:
        state = fb.policy_state(user, getattr(user, "policy", None))
        assessment = fb.assess(decision.result, audit, survey, state)
        assessment["backfilled"] = True

    rated = decision.feedback
    return DecisionOut(
        task_id=decision.task_id,
        options=decision.payload["options"],
        ratings=decision.payload["ratings"],
        context=decision.payload.get("context"),
        insight=decision.insight,
        insight_source=decision.insight_source,
        updated_at=decision.updated_at,
        audit=audit,
        assessment=assessment,
        feedback=None if rated is None else {
            "outcome": rated.outcome,
            "satisfaction": rated.satisfaction,
            "satisfaction_pct": round((rated.satisfaction - 1) / 4 * 100, 1),
            "chosen_option": rated.chosen_option,
            "note": rated.note,
            "reward": rated.reward,
            "predicted_satisfaction": rated.predicted_satisfaction,
            "predicted_confidence": rated.predicted_confidence,
            "created_at": rated.created_at,
        },
        behavioral=behavioral.decision_correlations(
            decision.task, decision.payload, decision.result, survey
        ),
        **decision.result,
    )


def _store_decision(
    task: Task,
    user: User,
    payload: dict,
    result: dict,
    insight: str,
    source: str,
    db: Session,
    assessment: dict | None = None,
    deliberation_seconds: float | None = None,
) -> TaskDecision:
    """Write the decision, then log what the loop can learn from writing it.

    Re-deciding a task carries information: the first answer did not settle
    it. Correcting the AI's ratings carries more - it names the criteria it
    got wrong. Both are recorded here rather than at the call sites, so no
    decision path can quietly skip the loop.
    """
    previous = task.decision
    redo = previous is not None
    previous_payload = dict(previous.payload) if previous else {}
    previous_source = (previous.result or {}).get("ratings_source") if previous else None

    if previous is None:
        task.decision = TaskDecision(
            user_id=user.id, payload=payload, result=result,
            insight=insight, insight_source=source, assessment=assessment,
        )
    else:
        previous.payload = payload
        previous.result = result
        previous.insight = insight
        previous.insight_source = source
        previous.assessment = assessment

    credit = (assessment or {}).get("credit") or {}

    # What the tempo trend reads back (decision_tempo.py): how long the user
    # deliberated, and how many alternatives they were weighing while doing
    # it - the second is needed to divide the first by Hick-Hyman difficulty.
    # Both ride on the event rather than the decision row, so re-deciding
    # adds a point to the series instead of overwriting the previous one.
    timing = {
        "deliberation_seconds": deliberation_seconds,
        "options": len(payload.get("options") or []),
    }

    if not redo:
        fb.record_event(
            db, user, "decision_made", task_id=task.id,
            payload={
                "best": result.get("best"),
                "ratings_source": result.get("ratings_source"),
                **timing,
            },
            reward=None, credit=credit,
        )
    elif previous_source == "gemini" and result.get("ratings_source") == "manual":
        # The user overrode AVEX's ratings by hand. That is a correction, and
        # the criteria whose numbers moved most are the ones it got wrong.
        fb.record_event(
            db, user, "ratings_corrected", task_id=task.id,
            payload={"best": result.get("best"), **timing},
            credit=fb.credit_from_ratings_change(
                previous_payload.get("ratings") or {},
                payload.get("ratings") or {},
                payload.get("criteria") or [],
            ),
        )
    else:
        fb.record_event(
            db, user, "decision_redone", task_id=task.id,
            payload={"best": result.get("best"), **timing}, credit=credit,
        )

    db.commit()
    db.refresh(task)
    return task.decision


@router.get("/{task_id}/decision", response_model=DecisionOut)
def get_decision(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DecisionOut:
    task = _owned_task(task_id, user, db)
    if task.decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No decision made for this task yet")
    return _decision_out(task.decision, user)


@router.post("/{task_id}/decide", response_model=DecisionOut)
async def decide_task(
    task_id: int,
    body: DecideRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Run the option-level decision engine on this task and store the result."""
    task = _owned_task(task_id, user, db)
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))

    criteria = [c.model_dump() for c in body.criteria]
    result = decision_engine.decide(
        body.options, criteria, body.ratings, survey,
        learned=state["tags"], learned_notes=fb.policy_notes(state),
    )
    result["ratings_source"] = "manual"
    result["rating_reasons"] = {}
    payload = {"options": body.options, "criteria": criteria, "ratings": body.ratings}
    insight, source = await generate_decision_insight(
        task, payload, result, survey, user.name, ai_allowed=user.consent_ai
    )
    assessment = fb.assess(
        result, decision_engine.build_audit(payload, result), survey, state
    )
    return _decision_out(
        _store_decision(
            task, user, payload, result, insight, source, db, assessment,
            deliberation_seconds=body.deliberation_seconds,
        ),
        user,
    )


@router.post("/{task_id}/decide-auto", response_model=DecisionOut)
async def decide_task_auto(
    task_id: int,
    body: AutoDecideRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Full auto: AVEX rates every option on every criterion using the
    student's survey profile, the engine scores them, and the decision is made
    in one step - the user never fills the rating grid."""
    task = _owned_task(task_id, user, db)
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))

    criteria = [c.model_dump() for c in body.criteria]
    # Pass the template hints along so the rater knows what a 5 means.
    hints = {
        c["name"]: c.get("hint")
        for c in decision_engine.get_template(task.category)["criteria"]
    }
    for criterion in criteria:
        if hints.get(criterion["name"]):
            criterion["hint"] = hints[criterion["name"]]

    ratings, reasons, ratings_source = await generate_option_ratings(
        task, body.options, criteria, survey, body.context, user.name,
        ai_allowed=user.consent_ai,
    )
    criteria = [{k: v for k, v in c.items() if k != "hint"} for c in criteria]

    result = decision_engine.decide(
        body.options, criteria, ratings, survey,
        learned=state["tags"], learned_notes=fb.policy_notes(state),
    )
    result["ratings_source"] = ratings_source
    result["rating_reasons"] = reasons
    payload = {
        "options": body.options, "criteria": criteria,
        "ratings": ratings, "context": body.context,
    }
    insight, source = await generate_decision_insight(
        task, payload, result, survey, user.name, ai_allowed=user.consent_ai
    )
    assessment = fb.assess(
        result, decision_engine.build_audit(payload, result), survey, state
    )
    return _decision_out(
        _store_decision(
            task, user, payload, result, insight, source, db, assessment,
            deliberation_seconds=body.deliberation_seconds,
        ),
        user,
    )


@router.patch("/{task_id}/quadrant", response_model=TaskOut)
def set_quadrant(
    task_id: int,
    body: QuadrantUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskOut:
    """Pin a task to an Eisenhower quadrant (drag-and-drop), or release it.

    Placement only: the priority score, the rank and the factor breakdown are
    all still computed by the engine. Sending `{"quadrant": null}` clears the
    pin and hands the task back to whatever the engine works out.
    """
    task = _owned_task(task_id, user, db)
    survey = user.survey.answers if user.survey else None
    state = fb.policy_state(user, fb.get_policy(db, user))

    # Score it *before* storing the override, so we can see which axis the
    # engine got wrong. Moving a task across the urgent/not-urgent line is a
    # complaint about the urgency factor; across the other line, about
    # importance. That is the credit assignment for this signal.
    before, _ = score_tasks([task], survey, learned=state["factors"])
    if body.quadrant and before and before[0].quadrant != body.quadrant:
        was_urgent = before[0].quadrant in ("do", "delegate")
        now_urgent = body.quadrant in ("do", "delegate")
        was_important = before[0].quadrant in ("do", "schedule")
        now_important = body.quadrant in ("do", "schedule")
        credit = {}
        if was_urgent != now_urgent:
            credit["urgency"] = 1.0
        if was_important != now_important:
            credit["importance"] = 1.0
        total = sum(credit.values()) or 1.0
        fb.record_event(
            db, user, "quadrant_override", task_id=task.id,
            payload={"from": before[0].quadrant, "to": body.quadrant},
            credit={k: v / total for k, v in credit.items()},
            space="factors",
        )

    task.quadrant_override = body.quadrant
    db.commit()
    db.refresh(task)

    entry = TaskOut.model_validate(task)
    scored, _ = score_tasks([task], survey, learned=state["factors"])
    if scored:
        item = scored[0]
        entry.score = item.score
        entry.reason = item.reason
        entry.factors = FactorBreakdown(**item.factors, weights=item.weights)
        entry.urgent, entry.important = item.urgent, item.important
        entry.computed_quadrant = item.quadrant
        entry.quadrant = body.quadrant or item.quadrant
        entry.quadrant_overridden = bool(body.quadrant) and body.quadrant != item.quadrant
    if task.decision is not None:
        entry.decided_option = task.decision.result.get("best")
    return entry


@router.patch("/{task_id}/complete", response_model=TaskOut)
def complete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = _owned_task(task_id, user, db)
    task.status = "done"
    task.completed_at = datetime.now(timezone.utc)

    # Finishing a task you had run a decision on is the loop's strongest
    # implicit positive: the recommendation was actionable enough to act on.
    # A task finished without one says nothing about the criteria, so it is
    # logged for the record but carries no credit and moves no weight.
    decision = task.decision
    fb.record_event(
        db, user, "task_completed", task_id=task.id,
        payload={"category": task.category, "decided": decision is not None},
        credit=((decision.assessment or {}).get("credit") or {}) if decision else {},
    )

    db.commit()
    db.refresh(task)
    return task


@router.patch("/{task_id}/reopen", response_model=TaskOut)
def reopen_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = _owned_task(task_id, user, db)
    task.status = "pending"
    task.completed_at = None
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    task = _owned_task(task_id, user, db)
    db.delete(task)
    db.commit()


@router.get("/stats", response_model=StatsResponse)
def stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StatsResponse:
    tasks = list(db.scalars(select(Task).where(Task.user_id == user.id)).all())
    now = datetime.now(timezone.utc)
    week_ahead = now + timedelta(days=7)

    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    pending = [t for t in tasks if t.status == "pending"]
    completed = [t for t in tasks if t.status == "done"]
    by_category: dict[str, int] = {}
    for task in tasks:
        by_category[task.category] = by_category.get(task.category, 0) + 1
    scores = [t.last_score for t in pending if t.last_score is not None]

    return StatsResponse(
        total_tasks=len(tasks),
        pending=len(pending),
        completed=len(completed),
        completion_rate=round(len(completed) / len(tasks), 4) if tasks else 0.0,
        by_category=by_category,
        avg_score=round(sum(scores) / len(scores), 1) if scores else 0.0,
        overdue=sum(1 for t in pending if _aware(t.due_date) < now),
        due_this_week=sum(1 for t in pending if now <= _aware(t.due_date) <= week_ahead),
    )
