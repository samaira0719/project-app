"""Priority scoring engine.

Every pending task receives a score S in [0, 100]:

    S = 100 * ( wU*U + wC*C + wA*A )  +  100 * wE*E,   capped at 100

The three core weights sum to 1; the effort term E is a pure *bonus*.
This keeps the ranking dominance-consistent: a task that beats another on
urgency and importance can never rank below it just because it carries a
time estimate. An estimate can only raise alarm (tight time budget), never
lower a task. All factors are normalised to [0, 1]:

  U - Urgency (deadline pressure).
      Exponential decay with a 48-hour half-life on the time remaining
      until the *effective* deadline (for Travel tasks the departure date,
      if earlier, is the effective deadline):
          U = 2^(-h / 48)   for h hours remaining;  U = 1 when overdue.
      Rationale: deadline pressure is not linear - the difference between
      "due in 2h" and "due in 10h" matters far more than "due in 6 vs 7 days".

  C - Category importance.
      A calibrated base weight per category, personalised by the
      User Decision-Making Survey (see below).

  E - Effort criticality (Study tasks with a time estimate).
      The critical ratio r = estimated_time / time_remaining. If r >= 1 you
      no longer have enough time - maximum criticality. A short "quick win"
      floor keeps sub-30-minute tasks from disappearing at the bottom:
          E = max( min(r, 1), quick_win * 2^(-estimated_hours) )
      For tasks without an estimate E = 0 (no alarm, no bonus).

  A - Aging (anti-starvation).
      Saturating growth so old backlog items slowly float upward:
          A = 1 - 2^(-days_open / 5)      (half-saturated after 5 days)

How the survey personalises the model
-------------------------------------
Weights (base wU=0.48, wC=0.36, wA=0.16; effort bonus 0.15, quick-win 0.30):

  * motivator "A deadline" / "Fear of falling behind", or handling many
    tasks by doing "the most urgent" raises the urgency weight
  * motivator "A personal goal", or valuing "Long-term benefits" /
    "Personal growth" / "Quality" raises the importance weight
  * frequent procrastination signals (delay_start, stuck_first, regret
    Often/Very Often; postponing styles) raises the aging weight, so stale
    tasks resurface instead of rotting in the backlog
  * "Do the easiest first" / motivator "A reward" raises the quick-win floor (the
    engine hands them small wins to build momentum)
  * valuing "Time required" raises the effort bonus

Category importance:

  * every area picked in support_areas boosts its matching category
    (Studies to Study, Career to Career, Health to Health, Travel Planning
    to Travel, Purchases to Purchases, Social Activity and Entertainment
    to Entertainment, Managing Time to Personal)
  * poor health signals (sleeping under 6h, exercising Never/Rarely) or a
    high health-consciousness rating raises Health
  * career_priority "Growth & learning" or "Passion" raises Career

Learned adjustment (the feedback loop)
--------------------------------------
On top of the survey, `score_tasks(..., learned=...)` accepts a per-factor
multiplier learned from what the student actually does - see feedback.py.
It multiplies wU, wC, wA and the effort bonus *before* the three core weights
are renormalised, so the learner can shift the balance between them but can
never change the shape of the model or add a factor the audit cannot explain.
The multipliers arrive already shrunk toward 1.0 by how much evidence stands
behind them, so a new account is effectively untouched here. Each change that
clears 2% adds its own note, exactly as a survey adjustment does.

Decided-task momentum bonus
---------------------------
When the user has already run an option-level decision inside a task (see
decision_engine.py), the start-friction is gone - they know exactly what to
do. Acting on a fresh decision beats letting it go stale, so decided tasks
get a flat +DECIDED_BONUS points (still capped at 100).

The ranking clarity metric (how separated #1 is from #2) is
    clarity = (S1 - S2) / S1
mirroring the Decision Clarity Index of the original prototype.

Eisenhower placement
--------------------
The same U and C factors also place each task in the Eisenhower matrix, so
the grid and the ranking can never disagree - they read the same numbers.

    urgent     means  U >= 0.5  (exactly 48h to the effective deadline)
                  or E >= 0.8 (the time budget is all but spent, so the
                  task is urgent even if the deadline still looks far off)
    important  means  C >= 0.70 (personalised category importance)

Both cutoffs are the natural midpoints of their own scale rather than free
parameters: U = 0.5 *is* one deadline half-life, and C = 0.70 is where the
calibrated category table separates outcome-bearing categories (Study,
Career, Health, Travel) from discretionary ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Task

DEADLINE_HALF_LIFE_HOURS = 48.0
AGING_HALF_LIFE_DAYS = 5.0

CATEGORY_BASE_IMPORTANCE: dict[str, float] = {
    "Study": 0.90,
    "Career": 0.85,
    "Health": 0.80,
    "Travel": 0.70,
    "Personal": 0.60,
    "Purchases": 0.50,
    "Other": 0.45,
    "Entertainment": 0.35,
}

SUPPORT_AREA_TO_CATEGORY: dict[str, str] = {
    "Studies": "Study",
    "Career": "Career",
    "Health": "Health",
    "Travel Planning": "Travel",
    "Purchases": "Purchases",
    "Social Activity": "Entertainment",
    "Entertainment": "Entertainment",
    "Managing Time": "Personal",
}
FOCUS_BOOST = 0.08

BASE_CORE_WEIGHTS = {"urgency": 0.48, "importance": 0.36, "aging": 0.16}
BASE_EFFORT_BONUS = 0.15
BASE_QUICK_WIN = 0.30
DECIDED_BONUS = 4.0  # points added once an option-level decision is made

OFTEN = {"Often", "Very Often"}

# ---- Eisenhower cutoffs (see the module docstring for why these values) ----
URGENT_URGENCY_CUTOFF = 0.50
URGENT_EFFORT_CUTOFF = 0.80
IMPORTANT_CUTOFF = 0.70

QUADRANT_KEYS = ("do", "schedule", "delegate", "eliminate")


@dataclass
class Personalization:
    """Weights derived from the survey, plus notes explaining each change."""

    core: dict[str, float]
    effort_bonus: float
    quick_win: float
    category_boosts: dict[str, float]
    notes: list[str]
    # The core weights *before* they are renormalised to sum to 1. The survey
    # only ever raises a weight, but since the three are then rescaled, a
    # raised weight can still end up with a smaller share when the others rose
    # further. The audit reports both so neither half is misleading on its own.
    raw_core: dict[str, float] | None = None


@dataclass
class ScoredTask:
    task: Task
    score: float
    factors: dict[str, float]
    weights: dict[str, float]
    reason: str
    urgent: bool = False
    important: bool = False
    quadrant: str = "eliminate"


def classify(factors: dict[str, float]) -> tuple[bool, bool, str]:
    """Place one task in the Eisenhower matrix from its own factors."""
    urgent = (
        factors["urgency"] >= URGENT_URGENCY_CUTOFF
        or factors.get("effort", 0.0) >= URGENT_EFFORT_CUTOFF
    )
    important = factors["importance"] >= IMPORTANT_CUTOFF
    if important:
        quadrant = "do" if urgent else "schedule"
    else:
        quadrant = "delegate" if urgent else "eliminate"
    return urgent, important, quadrant


def _apply_learned(
    weights: dict[str, float],
    effort_bonus: float,
    learned: dict[str, float] | None,
    notes: list[str],
) -> float:
    """Stack the feedback loop's multipliers onto the survey-tuned weights.

    `learned` comes from feedback.py already shrunk toward 1.0 by how much
    evidence stands behind it, so a new account is effectively untouched here
    and a long-running one is meaningfully retuned. The effort *bonus* is a
    separate scalar rather than one of the three core weights, so it is
    multiplied here and left out of the renormalisation below.
    """
    if not learned:
        return effort_bonus
    for name in ("urgency", "importance", "aging"):
        multiplier = float(learned.get(name, 1.0))
        if abs(multiplier - 1.0) > 0.02:
            weights[name] *= multiplier
            notes.append(
                f"learned from your feedback: {name} weighted "
                f"{'up' if multiplier > 1 else 'down'} "
                f"{abs(multiplier - 1) * 100:.0f}%"
            )
    effort_multiplier = float(learned.get("effort", 1.0))
    if abs(effort_multiplier - 1.0) > 0.02:
        effort_bonus *= effort_multiplier
        notes.append(
            f"learned from your feedback: tight time budgets raise "
            f"{'more' if effort_multiplier > 1 else 'less'} alarm"
        )
    return effort_bonus


def personalize(
    survey: dict | None, learned: dict[str, float] | None = None
) -> Personalization:
    weights = dict(BASE_CORE_WEIGHTS)
    effort_bonus = BASE_EFFORT_BONUS
    quick_win = BASE_QUICK_WIN
    boosts: dict[str, float] = {}
    notes: list[str] = []
    if not survey:
        effort_bonus = _apply_learned(weights, effort_bonus, learned, notes)
        total = sum(weights.values())
        return Personalization({k: v / total for k, v in weights.items()},
                               effort_bonus, quick_win, boosts, notes,
                               raw_core=dict(weights))

    def bump(category: str, amount: float) -> None:
        boosts[category] = boosts.get(category, 0.0) + amount

    # --- weight adaptation ---
    motivator = survey.get("motivator", "")
    if motivator in ("A deadline", "Fear of falling behind") or \
            survey.get("many_tasks") == "Do the most urgent":
        weights["urgency"] += 0.08
        notes.append("deadline-driven, so urgency is weighted higher")

    factors_valued = set(survey.get("choice_factors") or [])
    if motivator == "A personal goal" or \
            factors_valued & {"Long-term benefits", "Personal growth", "Quality"}:
        weights["importance"] += 0.07
        notes.append("goal and long-term oriented, so importance is weighted higher")

    procrastination_signals = sum((
        survey.get("delay_start") in OFTEN,
        survey.get("stuck_first") in OFTEN,
        survey.get("regret") in OFTEN,
        survey.get("decision_style") == "I often postpone decisions",
        survey.get("when_unsure") in ("Postpone it", "Keep over-researching"),
        "Procrastination" in (survey.get("study_challenges") or []),
    ))
    if procrastination_signals >= 2:
        weights["aging"] += 0.06
        notes.append("procrastination pattern, so older tasks resurface sooner")

    if survey.get("many_tasks") == "Do the easiest first" or motivator == "A reward":
        quick_win = 0.40
        notes.append("momentum-driven, so quick wins rank a little higher")

    if "Time required" in factors_valued:
        effort_bonus += 0.05
        notes.append("time-sensitive, so tight time budgets raise more alarm")

    # --- category importance ---
    for area in survey.get("support_areas") or []:
        category = SUPPORT_AREA_TO_CATEGORY.get(area)
        if category:
            bump(category, FOCUS_BOOST)
    if survey.get("support_areas"):
        notes.append("support areas boost their matching task categories")

    if (
        survey.get("sleep_hours") in ("Less than 4", "4-6")
        or survey.get("exercise_freq") in ("Never", "Rarely")
        or (survey.get("health_conscious") or 0) >= 4
    ):
        bump("Health", 0.05)
        notes.append("health signals, so Health tasks are nudged upward")

    if survey.get("career_priority") in ("Growth & learning", "Passion"):
        bump("Career", 0.04)

    effort_bonus = _apply_learned(weights, effort_bonus, learned, notes)

    total = sum(weights.values())
    core = {k: v / total for k, v in weights.items()}
    return Personalization(core, effort_bonus, quick_win, boosts, notes,
                           raw_core=dict(weights))


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _effective_deadline(task: Task) -> datetime:
    due = _as_utc(task.due_date)
    if task.category == "Travel" and task.travel_date:
        return min(due, _as_utc(task.travel_date))
    return due


def _urgency(task: Task, now: datetime) -> float:
    hours_left = (_effective_deadline(task) - now).total_seconds() / 3600.0
    if hours_left <= 0:
        return 1.0
    return 2.0 ** (-hours_left / DEADLINE_HALF_LIFE_HOURS)


def _importance(task: Task, p: Personalization) -> float:
    base = CATEGORY_BASE_IMPORTANCE.get(task.category, 0.5)
    return min(1.0, base + p.category_boosts.get(task.category, 0.0))


def _effort(task: Task, now: datetime, p: Personalization) -> float | None:
    if task.estimated_minutes is None:
        return None
    hours_left = max((_effective_deadline(task) - now).total_seconds() / 3600.0, 0.25)
    estimated_hours = task.estimated_minutes / 60.0
    critical_ratio = min(estimated_hours / hours_left, 1.0)
    quick_win_floor = p.quick_win * 2.0 ** (-estimated_hours)
    return max(critical_ratio, quick_win_floor)


def _aging(task: Task, now: datetime) -> float:
    days_open = max((now - _as_utc(task.created_at)).total_seconds() / 86400.0, 0.0)
    return 1.0 - 2.0 ** (-days_open / AGING_HALF_LIFE_DAYS)


def _reason(task: Task, factors: dict[str, float], now: datetime) -> str:
    """One human-readable sentence explaining the dominant factor."""
    hours_left = (_effective_deadline(task) - now).total_seconds() / 3600.0
    parts: list[str] = []
    if hours_left <= 0:
        parts.append("it is overdue")
    elif factors["urgency"] >= 0.5:
        parts.append(f"the deadline is only {max(hours_left, 1):.0f}h away")
    if factors.get("effort", 0) >= 0.7 and task.estimated_minutes:
        parts.append("the remaining time barely covers the estimated effort")
    if factors["importance"] >= 0.8:
        parts.append(f"{task.category.lower()} ranks high in your priorities")
    if factors["aging"] >= 0.6:
        parts.append("it has been waiting in your backlog")
    if not parts:
        parts.append("it balances deadline, importance and effort best right now")
    return "Prioritized because " + " and ".join(parts[:2]) + "."


def score_tasks(
    tasks: list[Task],
    survey: dict | None,
    now: datetime | None = None,
    learned: dict[str, float] | None = None,
) -> tuple[list[ScoredTask], float]:
    """Score and rank pending tasks. Returns (ranked tasks, clarity)."""
    now = now or datetime.now(timezone.utc)
    p = personalize(survey, learned)

    scored: list[ScoredTask] = []
    for task in tasks:
        factors = {
            "urgency": _urgency(task, now),
            "importance": _importance(task, p),
            "aging": _aging(task, now),
        }
        effort = _effort(task, now, p)
        factors["effort"] = effort if effort is not None else 0.0

        base = sum(p.core[name] * factors[name] for name in p.core)
        score = 100.0 * (base + p.effort_bonus * factors["effort"])

        reason = _reason(task, factors, now)
        decision = getattr(task, "decision", None)
        if decision is not None:
            score += DECIDED_BONUS
            best = (decision.result or {}).get("best")
            if best:
                reason += f" You already decided: go with {best}."

        weights_out = {**p.core, "effort": p.effort_bonus}
        urgent, important, quadrant = classify(factors)
        scored.append(
            ScoredTask(
                task=task,
                score=round(min(score, 100.0), 1),
                factors={k: round(v, 4) for k, v in factors.items()},
                weights={k: round(v, 4) for k, v in weights_out.items()},
                reason=reason,
                urgent=urgent,
                important=important,
                quadrant=quadrant,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)

    clarity = 0.0
    if len(scored) >= 2 and scored[0].score > 0:
        clarity = round((scored[0].score - scored[1].score) / scored[0].score, 4)
    elif len(scored) == 1:
        clarity = 1.0
    return scored, clarity
