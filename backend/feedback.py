"""The feedback loop: reward, credit, policy update, confidence, satisfaction.

WHAT THIS IS
------------
A contextual bandit sitting on top of the rule-based engine, not a black box
replacing it. The engine (scoring.py, decision_engine.py) still computes every
number the user sees. All this module can do is nudge a handful of *bounded
multipliers* on parameters the engine already had, so anything the learner
changes still shows up in the Audit panel as a weight the user can read.

THE LOOP
--------
    1. The user acts               -> an Interaction row is written
    2. The action is scored        -> reward r in [-1, 1]
    3. The reward is apportioned   -> credit c_k over the tags/factors that
                                      actually produced the recommendation
    4. The policy is updated       -> theta_k <- theta_k * exp(alpha * r * c_k)
    5. The next decision uses it   -> blended in by trust, which grows with
                                      the user's experience

Step 4 is the exponentiated-gradient (multiplicative weights / Hedge) update
of Kivinen & Warmuth (1997) and Freund & Schapire (1997). Multiplicative
rather than additive for two reasons: the parameters are multipliers, so they
must stay strictly positive, and the update is scale-free - a 10% correction
means the same thing to a weight of 0.7 as to one of 1.4.

The learning rate decays as

    alpha = ALPHA0 / (1 + events / ALPHA_HALFLIFE)

which is the Robbins-Monro condition in its usual practical form: early
feedback moves the model a lot, later feedback refines it. Combined with the
trust ramp below, a brand-new account is never yanked around by one bad day.

TRUST - "it improves by time / user level"
------------------------------------------
A learned multiplier is never applied at full strength. It is shrunk toward
the neutral 1.0 by

    trust = events / (events + TRUST_K)

so the effective multiplier is  1 + (theta - 1) * trust.  This is the
empirical-Bayes shrinkage you would want anyway - with two data points the
posterior should stay near the prior - and it is what makes the user-visible
"level" honest rather than decorative: the level *is* how much of the learned
policy is switched on.

CONSENT
-------
Everything here is gated on the personalization scope (privacy.py). With that
consent off, no Interaction is written, no policy is updated, and `assess`
falls back to the population prior. The decision engine itself is unaffected -
the user still gets the survey-tuned result and the full confidence score.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import privacy
from .decision_engine import TAGS
from .models import DecisionFeedback, Interaction, TaskDecision, User, UserPolicy

# ---------------------------------------------------------------- constants

ALPHA0 = 0.22            # learning rate for the very first events
ALPHA_HALFLIFE = 25.0    # events after which alpha has halved
TRUST_K = 12.0           # events at which the policy is applied at 50%

# A multiplier may never leave this band. The learner tunes the engine; it
# cannot overrule it, and it cannot drive a factor to zero however much the
# user complains about one bad week.
THETA_MIN, THETA_MAX = 0.65, 1.55

FACTORS = ("urgency", "importance", "aging", "effort")

# Reward for the explicit verdict on a recommendation, before the star rating
# is folded in. followed + 5 stars = +1.00 exactly; rejected + 1 star = -1.00.
OUTCOME_REWARD = {"followed": 0.35, "modified": 0.0, "rejected": -0.35}
SATISFACTION_WEIGHT = 0.65

# Implicit signals - things the user does that carry an opinion without one
# being asked for. Smaller magnitudes than an explicit rating, because the
# inference is weaker.
IMPLICIT_REWARD = {
    "task_completed": 0.25,        # they acted on the call
    "ratings_corrected": -0.30,    # the AI's ratings were wrong enough to fix
    "decision_redone": -0.15,      # the first answer did not settle it
    "quadrant_override": -0.25,    # the matrix put it in the wrong box
    "chat_question": None,         # context only, never scored
}

LEVELS = [
    (0, "Calibrating", "Running on the survey profile alone - no history yet."),
    (5, "Learning", "First patterns spotted; the policy is being nudged gently."),
    (15, "Adapting", "Your feedback is now shaping how criteria are weighted."),
    (35, "Tuned", "The engine is meaningfully personalised to your outcomes."),
    (75, "Dialled in", "Long history - corrections are fine-grained from here."),
]


# ---------------------------------------------------------------- helpers

def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def learning_enabled(user: User) -> bool:
    """Is the user opted in to the loop at all?"""
    return bool(getattr(user, "consent_personalization", False))


def get_policy(db: Session, user: User, *, create: bool = False) -> UserPolicy | None:
    """The user's learned policy row, created on demand when learning is on."""
    policy = db.scalar(select(UserPolicy).where(UserPolicy.user_id == user.id))
    if policy is None and create:
        policy = UserPolicy(
            user_id=user.id,
            tag_multipliers={tag: 1.0 for tag in TAGS},
            factor_multipliers={factor: 1.0 for factor in FACTORS},
        )
        db.add(policy)
        db.flush()
    return policy


def trust_of(events: int) -> float:
    """How much of the learned policy is switched on, in [0, 1)."""
    return events / (events + TRUST_K) if events > 0 else 0.0


def alpha_of(events: int) -> float:
    """Decaying learning rate."""
    return ALPHA0 / (1.0 + events / ALPHA_HALFLIFE)


def level_of(events: int) -> dict[str, Any]:
    """The user-facing level, and how far it is to the next one."""
    index = 0
    for i, (threshold, _, _) in enumerate(LEVELS):
        if events >= threshold:
            index = i
    threshold, label, blurb = LEVELS[index]
    next_at = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
    return {
        "level": index + 1,
        "max_level": len(LEVELS),
        "label": label,
        "blurb": blurb,
        "events": events,
        "next_at": next_at,
        "to_next": max(0, next_at - events) if next_at else 0,
        "progress_pct": (
            round(100.0 * (events - threshold) / (next_at - threshold), 0)
            if next_at else 100.0
        ),
        "trust_pct": round(trust_of(events) * 100, 0),
    }


def policy_state(user: User, policy: UserPolicy | None) -> dict[str, Any]:
    """Everything the engine and the UI need to know about the learned model.

    Returns neutral multipliers (all 1.0) whenever learning is off or unused,
    so every caller can use the same shape without branching.
    """
    enabled = learning_enabled(user)
    events = policy.events if (policy and enabled) else 0
    trust = trust_of(events)

    def blend(stored: dict | None, keys: tuple[str, ...]) -> dict[str, float]:
        stored = stored or {}
        return {
            key: round(1.0 + (float(stored.get(key, 1.0)) - 1.0) * trust, 4)
            for key in keys
        }

    return {
        "enabled": enabled,
        "trust": trust,
        "events": events,
        "rated_decisions": policy.rated_decisions if (policy and enabled) else 0,
        "satisfaction_mean": policy.satisfaction_mean if (policy and enabled) else 0.60,
        "calibration_bias": policy.calibration_bias if (policy and enabled) else 0.0,
        "follow_rate": policy.follow_rate if (policy and enabled) else 0.5,
        "tags": blend(policy.tag_multipliers if policy else None, TAGS),
        "factors": blend(policy.factor_multipliers if policy else None, FACTORS),
        # Unshrunk, for the "what I have learned" panel - the raw belief.
        "raw_tags": dict(policy.tag_multipliers or {}) if (policy and enabled) else {},
        "raw_factors": dict(policy.factor_multipliers or {}) if (policy and enabled) else {},
        **level_of(events),
    }


TAG_LABELS = {
    "urgency": "urgency", "importance": "importance", "interest": "interest",
    "effort": "ease of doing", "cost": "cost", "quality": "quality",
    "other": "other criteria",
}


def policy_notes(state: dict[str, Any]) -> list[str]:
    """Human-readable lines for whatever the learner has actually changed."""
    if not state["enabled"] or state["events"] < 1:
        return []
    notes: list[str] = []
    for tag, multiplier in sorted(
        state["tags"].items(), key=lambda kv: abs(kv[1] - 1.0), reverse=True
    ):
        if abs(multiplier - 1.0) < 0.02:
            continue
        direction = "up" if multiplier > 1 else "down"
        notes.append(
            f"Learned from your feedback: {TAG_LABELS.get(tag, tag)} criteria "
            f"weighted {direction} {abs(multiplier - 1) * 100:.0f}% "
            f"(level {state['level']}, applied at {state['trust_pct']:.0f}% strength)"
        )
    return notes[:3]


# ---------------------------------------------------------------- reward

def reward_for_feedback(outcome: str, satisfaction: int) -> float:
    """Scalar reward for an explicit verdict plus a 1-5 star rating."""
    stars = _clip((satisfaction - 3) / 2.0, -1.0, 1.0)
    base = OUTCOME_REWARD.get(outcome, 0.0)
    return round(_clip(SATISFACTION_WEIGHT * stars + base, -1.0, 1.0), 4)


def credit_from_audit(audit: dict | None) -> dict[str, float]:
    """Apportion responsibility over criterion *tags* - the eligibility trace.

    A criterion that supplied 40% of the winner's points takes 40% of the
    blame or the praise. Without this the reward would smear evenly over every
    parameter and the learner would converge on nothing in particular.
    """
    if not audit:
        return {}
    weights = {w["name"]: w.get("tag", "other") for w in audit.get("weights", [])}
    contributions = audit.get("contributions") or []
    if not contributions:
        return {}

    credit: dict[str, float] = {}
    for part in contributions[0].get("parts", []):
        tag = weights.get(part["criterion"], "other")
        credit[tag] = credit.get(tag, 0.0) + max(0.0, part.get("share_pct", 0.0)) / 100.0
    total = sum(credit.values())
    if total <= 0:
        return {}
    return {tag: round(share / total, 4) for tag, share in credit.items()}


def credit_from_ratings_change(
    before: dict[str, dict[str, int]],
    after: dict[str, dict[str, int]],
    criteria: list[dict[str, Any]],
) -> dict[str, float]:
    """Blame the tags whose ratings the user actually corrected."""
    tag_of = {c["name"]: c.get("tag", "other") for c in criteria}
    moved: dict[str, float] = {}
    for option, rows in (after or {}).items():
        for name, value in (rows or {}).items():
            previous = (before or {}).get(option, {}).get(name)
            if previous is None:
                continue
            delta = abs(int(value) - int(previous))
            if delta:
                tag = tag_of.get(name, "other")
                moved[tag] = moved.get(tag, 0.0) + delta
    total = sum(moved.values())
    if total <= 0:
        return {}
    return {tag: round(amount / total, 4) for tag, amount in moved.items()}


# ---------------------------------------------------------------- the update

def apply_reward(
    policy: UserPolicy,
    reward: float,
    credit: dict[str, float],
    *,
    space: str = "tags",
) -> list[dict[str, Any]]:
    """One exponentiated-gradient step. Returns what moved, for the audit.

    theta_k <- clip( theta_k * exp(alpha * r * c_k) )

    `space` selects which parameter block the credit addresses: "tags" for
    decision criteria, "factors" for the task-ranking weights.
    """
    if not credit or reward == 0.0:
        return []

    alpha = alpha_of(policy.events)
    store = dict(
        (policy.tag_multipliers if space == "tags" else policy.factor_multipliers) or {}
    )
    keys = TAGS if space == "tags" else FACTORS
    changes: list[dict[str, Any]] = []

    for key in keys:
        share = credit.get(key, 0.0)
        if share <= 0:
            continue
        before = float(store.get(key, 1.0))
        after = _clip(before * math.exp(alpha * reward * share), THETA_MIN, THETA_MAX)
        store[key] = round(after, 5)
        if abs(after - before) > 1e-4:
            changes.append({
                "key": key,
                "space": space,
                "before": round(before, 4),
                "after": round(after, 4),
                "credit_pct": round(share * 100, 1),
                "alpha": round(alpha, 4),
            })

    # Reassign rather than mutate: SQLAlchemy does not track in-place edits of
    # a plain JSON dict, so an in-place update would silently never persist.
    if space == "tags":
        policy.tag_multipliers = store
    else:
        policy.factor_multipliers = store
    return changes


def _ema(current: float, observation: float, count: int, floor: float = 0.12) -> float:
    """Running mean that turns into an EMA once there is enough history."""
    rate = max(floor, 1.0 / (count + 1))
    return round(current + rate * (observation - current), 5)


def record_event(
    db: Session,
    user: User,
    kind: str,
    *,
    task_id: int | None = None,
    payload: dict | None = None,
    reward: float | None = None,
    credit: dict[str, float] | None = None,
    space: str = "tags",
) -> Interaction | None:
    """Log one interaction and, if it carries a reward, learn from it.

    Silently does nothing without the personalization consent - that is the
    whole point of the scope, so it is enforced here rather than trusted to
    every call site.
    """
    if not learning_enabled(user):
        return None

    if reward is None:
        reward = IMPLICIT_REWARD.get(kind)

    event = Interaction(
        user_id=user.id,
        kind=kind,
        task_id=task_id,
        payload=payload or {},
        reward=reward,
        credit=credit or {},
        applied=False,
    )
    db.add(event)

    policy = get_policy(db, user, create=True)
    if reward is not None and credit:
        apply_reward(policy, reward, credit, space=space)
    if reward is not None:
        policy.cumulative_reward = round(policy.cumulative_reward + reward, 4)
    policy.events += 1
    event.applied = True

    _prune(db, user)
    return event


def _prune(db: Session, user: User) -> None:
    """Honour the published 24-month retention limit on learning events."""
    cutoff = _utcnow() - timedelta(days=privacy.LEARNING_RETENTION_DAYS)
    db.execute(
        delete(Interaction).where(
            Interaction.user_id == user.id, Interaction.created_at < cutoff
        )
    )


def record_decision_feedback(
    db: Session,
    user: User,
    decision: TaskDecision,
    *,
    outcome: str,
    satisfaction: int,
    chosen_option: str | None,
    note: str | None,
) -> dict[str, Any]:
    """The explicit feedback path: store it, reward it, recalibrate on it."""
    assessment = decision.assessment or {}
    predicted_satisfaction = (assessment.get("satisfaction") or {}).get("score")
    predicted_confidence = (assessment.get("confidence") or {}).get("score")
    reward = reward_for_feedback(outcome, satisfaction)

    row = db.scalar(
        select(DecisionFeedback).where(DecisionFeedback.decision_id == decision.id)
    )
    if row is None:
        row = DecisionFeedback(decision_id=decision.id, user_id=user.id)
        db.add(row)
    row.outcome = outcome
    row.satisfaction = satisfaction
    row.chosen_option = (chosen_option or "")[:120] or None
    row.note = (note or "").strip()[:1000] or None
    row.predicted_confidence = predicted_confidence
    row.predicted_satisfaction = predicted_satisfaction
    row.reward = reward

    if not learning_enabled(user):
        # The rating is still stored - it is the user's own record of how the
        # call went - but nothing is learned from it.
        return {
            "reward": reward,
            "learned": False,
            "changes": [],
            "reason": "Learning is off for this account, so nothing was updated.",
        }

    policy = get_policy(db, user, create=True)
    # The credit trace frozen with the decision, so the reward lands on the
    # criteria that actually produced *that* recommendation.
    credit = dict(assessment.get("credit") or {})
    changes = apply_reward(policy, reward, credit, space="tags")

    actual = (satisfaction - 1) / 4.0  # 1-5 stars -> 0-1
    policy.satisfaction_mean = _ema(
        policy.satisfaction_mean, actual, policy.rated_decisions
    )
    policy.follow_rate = _ema(
        policy.follow_rate, 1.0 if outcome == "followed" else 0.0,
        policy.rated_decisions,
    )
    if predicted_satisfaction is not None:
        error = actual - predicted_satisfaction / 100.0
        policy.calibration_bias = _clip(
            _ema(policy.calibration_bias, error, policy.rated_decisions), -0.25, 0.25
        )
    policy.rated_decisions += 1
    policy.events += 1
    policy.cumulative_reward = round(policy.cumulative_reward + reward, 4)

    db.add(Interaction(
        user_id=user.id,
        kind="satisfaction_rated",
        task_id=decision.task_id,
        payload={
            "outcome": outcome,
            "satisfaction": satisfaction,
            "chosen_option": row.chosen_option,
            "predicted_satisfaction": predicted_satisfaction,
            "predicted_confidence": predicted_confidence,
        },
        reward=reward,
        credit=credit,
        applied=True,
    ))
    _prune(db, user)

    return {
        "reward": reward,
        "learned": True,
        "changes": changes,
        "calibration_error_pts": (
            round(actual * 100 - predicted_satisfaction, 1)
            if predicted_satisfaction is not None else None
        ),
    }


# ======================================================================
#  CONFIDENCE and SATISFACTION
#
#  Two different questions, deliberately kept apart:
#
#    Confidence  - how much should this recommendation be trusted? A
#                  property of the decision's own structure: how far ahead
#                  the winner is, how much a wrong rating would matter, how
#                  good the inputs were. Computed the same way for everyone;
#                  no history required, so it works on day one.
#
#    Satisfaction- how happy is *this* user likely to be with it? A
#                  prediction, learned from their own rated history and
#                  recalibrated every time they report back.
#
#  Both return a 0-100 score plus an itemised audit: every component names
#  the points it contributed, the points available, and why.
# ======================================================================

CONFIDENCE_COMPONENTS = (
    ("separation", 30.0, "Lead over the runner-up"),
    ("robustness", 25.0, "Survives being wrong"),
    ("evidence", 20.0, "Quality of the inputs"),
    ("coverage", 15.0, "Breadth of the criteria"),
    ("fit", 10.0, "Profile & history behind it"),
)

BANDS = (
    (85, "Very high", "Act on it."),
    (70, "High", "Solid - go, and spot-check the top criterion."),
    (55, "Moderate", "Reasonable, but the margin is thin enough to re-read."),
    (40, "Low", "Treat as a tie-break, not an answer."),
    (0, "Very low", "Not enough separation or input quality to rely on."),
)


def _band(score: float) -> dict[str, str]:
    for cutoff, label, advice in BANDS:
        if score >= cutoff:
            return {"label": label, "advice": advice}
    return {"label": "Very low", "advice": ""}


def _separation(result: dict) -> tuple[float, str]:
    clarity = float(result.get("clarity") or 0.0)
    # 25% clarity is treated as a decisive lead; the Decision Clarity Index
    # the ranking engine uses draws its "clear winner" line at 20%.
    sub = _clip(clarity / 0.25, 0.0, 1.0)
    scores = result.get("scores") or []
    gap = (scores[0]["score"] - scores[1]["score"]) if len(scores) > 1 else 100.0
    return sub, (
        f"{result.get('best')} leads by {gap:.1f} points "
        f"({clarity * 100:.0f}% clarity). Full marks at a 25% lead."
    )


def _robustness(audit: dict | None) -> tuple[float, str]:
    if not audit:
        return 0.5, "No audit available for this decision, so assumed average."
    robustness = audit.get("robustness") or {}
    sensitivity = audit.get("sensitivity") or {}
    verdict = robustness.get("verdict")
    minimum = sensitivity.get("min_rating_change")

    if verdict == "tie":
        sub, why = 0.05, "A dead heat - the order between the top two is arbitrary."
    elif robustness.get("dominates"):
        sub, why = 1.0, (
            "The winner is at least as good on every criterion, so no set of "
            "weights could reverse it."
        )
    elif minimum is None:
        sub, why = 0.9, "No single rating, moved anywhere on the 1-5 scale, flips it."
    else:
        sub = _clip(minimum / 2.0, 0.0, 1.0)
        why = (
            f"One rating would have to be wrong by {minimum:.1f} points to flip "
            "the result (2.0 points scores full marks)."
        )

    if robustness.get("weight_dependent"):
        sub *= 0.75
        why += " Scaled down 25%: ignoring the weights entirely picks a different winner."
    return sub, why


EVIDENCE_QUALITY = {
    "manual": (1.00, "You rated every option yourself - first-hand input."),
    "gemini": (0.80, "AVEX rated the options with a written reason for each; "
                     "good, but second-hand."),
    "engine": (0.15, "AVEX was unavailable, so every rating defaulted to a "
                     "neutral 3. The comparison is a placeholder."),
}


def _evidence(result: dict) -> tuple[float, str]:
    source = result.get("ratings_source", "manual")
    return EVIDENCE_QUALITY.get(source, EVIDENCE_QUALITY["manual"])


def _coverage(audit: dict | None, result: dict) -> tuple[float, str]:
    weights = (audit or {}).get("weights") or result.get("criteria") or []
    count = len(weights)
    if not count:
        return 0.0, "No criteria recorded."
    # Breadth: 4+ criteria is a properly framed comparison.
    breadth = _clip((count - 1) / 3.0, 0.0, 1.0)
    # Balance: 1 - normalised Herfindahl index. One criterion carrying
    # everything makes the whole decision a single judgement call.
    shares = [w.get("normalized", 1.0 / count) for w in weights]
    hhi = sum(s * s for s in shares)
    balance = 1.0 if count == 1 else _clip(
        (1.0 - hhi) / (1.0 - 1.0 / count), 0.0, 1.0
    )
    sub = 0.6 * breadth + 0.4 * balance
    return sub, (
        f"{count} criteria, heaviest carrying {max(shares) * 100:.0f}% of the "
        f"weight (concentration index {hhi:.2f}). Breadth and balance count 60/40."
    )


def _fit(survey: dict | None, state: dict) -> tuple[float, str]:
    survey_part = 0.5 if survey else 0.1
    history_part = 0.5 * state["trust"]
    sub = survey_part + history_part
    if not survey:
        why = ("No decision-making survey on file, so the weighting is generic. "
               "Taking it is the single biggest upgrade available here.")
    elif state["events"] == 0:
        why = ("Tuned by your survey profile. No outcome history yet - rate a "
               "few decisions and this climbs.")
    else:
        why = (
            f"Survey profile plus {state['events']} learning events "
            f"(level {state['level']}/{state['max_level']}, "
            f"{state['trust_pct']:.0f}% of the learned policy applied)."
        )
    return sub, why


def confidence(
    result: dict, audit: dict | None, survey: dict | None, state: dict
) -> dict[str, Any]:
    """How much this specific recommendation deserves to be trusted, 0-100."""
    subs = {
        "separation": _separation(result),
        "robustness": _robustness(audit),
        "evidence": _evidence(result),
        "coverage": _coverage(audit, result),
        "fit": _fit(survey, state),
    }

    notes: list[dict[str, Any]] = []
    total = 0.0
    for key, maximum, label in CONFIDENCE_COMPONENTS:
        sub, why = subs[key]
        points = round(sub * maximum, 1)
        total += points
        notes.append({
            "key": key,
            "label": label,
            "points": points,
            "max": maximum,
            "pct": round(sub * 100, 0),
            "why": why,
        })

    score = round(_clip(total, 0.0, 100.0), 1)
    weakest = min(notes, key=lambda n: n["points"] / n["max"])
    return {
        "score": score,
        **_band(score),
        "notes": notes,
        "headline": (
            f"{score:.0f}% confidence - {_band(score)['label'].lower()}. "
            f"Weakest link: {weakest['label'].lower()} "
            f"({weakest['points']:.0f} of {weakest['max']:.0f} points)."
        ),
        "weakest": weakest["key"],
        "method": (
            "Weighted sum of five independent checks on the decision itself, "
            "not on how it feels. Same formula for every user, so two "
            "confidence scores are directly comparable."
        ),
    }


SATISFACTION_PRIOR = 0.60


def satisfaction(
    result: dict,
    audit: dict | None,
    state: dict,
    credit: dict[str, float],
) -> dict[str, Any]:
    """Predicted satisfaction with this call, 0-100, learned per user."""
    notes: list[dict[str, Any]] = []

    def add(label: str, delta: float, why: str) -> None:
        notes.append({
            "label": label, "points": round(delta * 100, 1), "why": why,
        })

    # --- prior ---
    if state["enabled"] and state["rated_decisions"] > 0:
        value = state["satisfaction_mean"]
        add("Your baseline", value, (
            f"Across {state['rated_decisions']} decisions you have rated, your "
            f"average satisfaction is {value * 100:.0f}%."
        ))
    else:
        value = SATISFACTION_PRIOR
        add("Starting baseline", value, (
            "No rated decisions yet, so this starts at the 60% population "
            "baseline and moves to your own average once you rate a few."
        ))

    # --- how clear-cut the call is: close calls breed second-guessing ---
    separation_sub, _ = _separation(result)
    delta = 0.12 * (2 * separation_sub - 1)
    value += delta
    add("Clarity of the win", delta, (
        "A decisive lead removes the lingering 'but what about the other one'; "
        "a near-tie invites it. ±12 points at the extremes."
    ))

    # --- does the win come from the criteria this user actually cares about ---
    alignment = sum(
        (state["tags"].get(tag, 1.0) - 1.0) * share for tag, share in (credit or {}).items()
    )
    if state["enabled"] and abs(alignment) > 1e-6:
        delta = _clip(alignment * 0.8, -0.15, 0.15)
        value += delta
        top = max(credit.items(), key=lambda kv: kv[1])[0] if credit else "its criteria"
        add("Fit with what you value", delta, (
            f"This win is driven mostly by {TAG_LABELS.get(top, top)}, which "
            f"your feedback history weights "
            f"{'above' if alignment > 0 else 'below'} average."
        ))

    # --- who produced the ratings ---
    source = result.get("ratings_source", "manual")
    if source == "manual":
        value += 0.05
        add("You set the ratings", 0.05,
            "People are measurably happier with a call built on their own numbers.")
    elif source == "engine":
        value -= 0.15
        add("Placeholder ratings", -0.15,
            "Every rating defaulted to a neutral 3, so the result is not yet "
            "a real comparison.")

    # --- revealed tendency to follow advice ---
    if state["enabled"] and state["rated_decisions"] >= 3:
        delta = _clip(0.10 * (state["follow_rate"] - 0.5) * 2, -0.10, 0.10)
        value += delta
        add("How you use recommendations", delta, (
            f"You follow the recommendation {state['follow_rate'] * 100:.0f}% of "
            "the time; people who act on a call report more satisfaction with it."
        ))

    # --- learned calibration correction ---
    if state["enabled"] and abs(state["calibration_bias"]) > 0.005:
        delta = state["calibration_bias"]
        value += delta
        add("Calibration correction", delta, (
            f"Past predictions for you have run "
            f"{'low' if delta > 0 else 'high'} by "
            f"{abs(delta) * 100:.0f} points on average; corrected here."
        ))

    score = round(_clip(value, 0.05, 0.95) * 100, 1)
    if state["rated_decisions"] >= 5:
        basis = (
            f"Learned from your {state['rated_decisions']} rated decisions "
            f"(level {state['level']}/{state['max_level']})."
        )
    elif state["enabled"]:
        basis = (
            f"Mostly the population baseline so far - {state['rated_decisions']} "
            "of the 5 rated decisions needed before this is really yours."
        )
    else:
        basis = (
            "Population baseline only. Learning from your decisions is switched "
            "off for this account, so this cannot adapt to you."
        )

    return {
        "score": score,
        **_band(score),
        "notes": notes,
        "headline": f"{score:.0f}% predicted satisfaction. {basis}",
        "basis": basis,
        "method": (
            "A prior for this user, adjusted by how clear-cut the win is, "
            "whether it rests on criteria they value, who supplied the "
            "ratings, and a learned correction for past over- or "
            "under-prediction. Every rating you give moves it."
        ),
    }


def assess(
    result: dict,
    audit: dict | None,
    survey: dict | None,
    state: dict,
) -> dict[str, Any]:
    """Both scores plus the credit trace, ready to store on the decision."""
    credit = credit_from_audit(audit)
    return {
        "confidence": confidence(result, audit, survey, state),
        "satisfaction": satisfaction(result, audit, state, credit),
        "credit": credit,
        "learning": {
            "enabled": state["enabled"],
            "level": state["level"],
            "max_level": state["max_level"],
            "label": state["label"],
            "blurb": state["blurb"],
            "events": state["events"],
            "rated_decisions": state["rated_decisions"],
            "trust_pct": state["trust_pct"],
            "progress_pct": state["progress_pct"],
            "to_next": state["to_next"],
        },
        "scored_at": _utcnow().isoformat(),
    }
