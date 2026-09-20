"""Behavioural-science layer - the *why behind the why*.

Every number this app produces comes from a model of how students actually
decide, not from a hunch. This module names that model: it maps the user's
survey answers, their Eisenhower distribution and each option-level decision
onto documented findings from judgement-and-decision-making research, and
reports (a) what in *their* data triggered the correlation, (b) what the
engine did about it, and (c) the citation, so the claim is checkable.

Two kinds of correlation are reported:

  driver - the finding explains a parameter the engine actually moved for
           this user (e.g. urgency weight 0.48 to 0.53). The correlation is
           quantitative: the `metric` field carries the before/after.
  risk   - the finding predicts a distortion this user is exposed to. The
           engine cannot remove a bias, so it surfaces it and states the
           countermeasure instead of silently "correcting" the answer.

Nothing here changes a score. It is an explanation layer over `scoring.py`
and `decision_engine.py`, which stay the single source of numeric truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .scoring import (
    BASE_CORE_WEIGHTS,
    BASE_EFFORT_BONUS,
    BASE_QUICK_WIN,
    personalize,
)

OFTEN = {"Often", "Very Often"}


@dataclass(frozen=True)
class Effect:
    """One documented finding, stated so a student can check it."""

    name: str
    finding: str   # what the research shows
    source: str    # citation
    lever: str     # what Decide Well does about it, in general


EFFECTS: dict[str, Effect] = {
    "mere_urgency": Effect(
        name="Mere-urgency effect",
        finding=(
            "People pick the task with the nearer deadline even when they know "
            "the other task pays off more - urgency is more salient than "
            "importance, so it wins attention it has not earned."
        ),
        source="Zhu, Yang & Hsee (2018), Journal of Consumer Research 45(3), 673-690",
        lever=(
            "The Eisenhower matrix separates urgent from important so a loud "
            "deadline cannot pass itself off as a big payoff."
        ),
    ),
    "temporal_discounting": Effect(
        name="Hyperbolic discounting",
        finding=(
            "The value you place on an outcome falls as its delay grows, and it "
            "falls fastest near the present - which is why a deadline feels "
            "almost weightless until it is suddenly days away."
        ),
        source="Ainslie (1975), Psychological Bulletin 82(4); Laibson (1997), QJE 112(2)",
        lever=(
            "Urgency decays on a 48-hour half-life (U = 2^(-h/48)) rather than "
            "linearly, so the model discounts time the way you already do."
        ),
    ),
    "temporal_motivation": Effect(
        name="Temporal Motivation Theory",
        finding=(
            "Motivation = (Expectancy × Value) / (1 + Impulsiveness × Delay). "
            "Procrastination is the predictable output of a distant deadline "
            "divided by an impulsive present, not a character flaw."
        ),
        source="Steel & König (2006), Academy of Management Review 31(4), 889-913",
        lever=(
            "The aging term raises a task's score the longer it sits "
            "(A = 1 - 2^(-days/5)), shrinking the delay denominator for you."
        ),
    ),
    "present_bias": Effect(
        name="Present bias (β-δ discounting)",
        finding=(
            "Choices made for right now weight immediate comfort far more heavily "
            "than the same choices made for next week - so 'later me' is "
            "reliably handed the hard task."
        ),
        source="O'Donoghue & Rabin (1999), American Economic Review 89(1), 103-124",
        lever=(
            "The ranking is computed for one moment - now - so the comparison "
            "you see is the one you are actually making."
        ),
    ),
    "planning_fallacy": Effect(
        name="Planning fallacy",
        finding=(
            "People underestimate how long their own tasks will take even when "
            "they remember overrunning on identical past tasks; the estimate is "
            "built from the best case, not from the track record."
        ),
        source="Buehler, Griffin & Ross (1994), JPSP 67(3), 366-381",
        lever=(
            "The effort term uses the critical ratio r = estimate ÷ time left, "
            "so an optimistic estimate against a tight window still raises alarm."
        ),
    ),
    "zeigarnik": Effect(
        name="Zeigarnik effect",
        finding=(
            "Unfinished tasks keep intruding on attention and degrade focus on "
            "whatever you are doing instead. Making a concrete plan for them "
            "releases the intrusion - finishing is not required."
        ),
        source="Zeigarnik (1927); Masicampo & Baumeister (2011), JPSP 101(4), 667-683",
        lever=(
            "Backlog items resurface through the aging term instead of rotting "
            "silently at the bottom of the list."
        ),
    ),
    "goal_gradient": Effect(
        name="Goal-gradient / small-wins effect",
        finding=(
            "Effort accelerates as a goal comes into reach, and a single small "
            "completed step measurably lifts motivation for the rest of the day."
        ),
        source=(
            "Kivetz, Urminsky & Zheng (2006), Journal of Marketing Research 43(1); "
            "Amabile & Kramer (2011), The Progress Principle"
        ),
        lever=(
            "A quick-win floor keeps short tasks from sinking, so the engine can "
            "hand you a cheap win when you need momentum more than optimality."
        ),
    ),
    "decision_fatigue": Effect(
        name="Decision fatigue",
        finding=(
            "The quality of successive decisions degrades across a run of them; "
            "as depletion sets in people default to the status quo or avoid "
            "deciding at all."
        ),
        source=(
            "Baumeister et al. (1998), JPSP 74(5); "
            "Danziger, Levav & Avnaim-Pesso (2011), PNAS 108(17)"
        ),
        lever=(
            "The ranking is pre-computed and a decided task carries a bonus, so "
            "starting costs you no further deliberation."
        ),
    ),
    "choice_overload": Effect(
        name="Choice overload",
        finding=(
            "Past roughly six alternatives, adding options lowers the chance of "
            "choosing at all and lowers satisfaction with whatever is chosen."
        ),
        source="Iyengar & Lepper (2000), JPSP 79(6), 995-1006",
        lever="Decisions are capped at 8 options and 8 criteria by design.",
    ),
    "maximizing": Effect(
        name="Maximizing vs. satisficing",
        finding=(
            "People who search for the best option rather than a good-enough one "
            "reach objectively better outcomes yet report more regret and less "
            "satisfaction with them."
        ),
        source="Schwartz et al. (2002), JPSP 83(5), 1178-1197",
        lever=(
            "The clarity margin tells you when the gap is too small to be worth "
            "more searching - the explicit signal to stop and commit."
        ),
    ),
    "wadd": Effect(
        name="Weighted-additive rule under cognitive load",
        finding=(
            "Given time pressure or many options, people abandon the compensatory "
            "weighted rule for a lexicographic shortcut - deciding on one loud "
            "attribute and ignoring the rest."
        ),
        source="Payne, Bettman & Johnson (1993), The Adaptive Decision Maker",
        lever=(
            "Writing the weights down and doing the arithmetic externally "
            "restores the compensatory rule your head drops under load."
        ),
    ),
    "attribute_substitution": Effect(
        name="Attribute substitution",
        finding=(
            "When a question is hard ('which of these matters most?') the mind "
            "quietly answers an easier one ('which feels loudest right now?') and "
            "reports the answer with the same confidence."
        ),
        source="Kahneman & Frederick (2002), in Heuristics and Biases, 49-81",
        lever=(
            "Naming the criteria up front forces the hard question to be answered "
            "explicitly, before any option is scored."
        ),
    ),
    "anchoring": Effect(
        name="Anchoring",
        finding=(
            "A number you see first drags your own estimate toward it, even when "
            "you know it is arbitrary and are warned about the pull."
        ),
        source="Tversky & Kahneman (1974), Science 185(4157), 1124-1131",
        lever=(
            "AVEX's ratings are shown with their reasoning and stay editable - an "
            "anchor you can see and overwrite is a weaker anchor."
        ),
    ),
    "affect_heuristic": Effect(
        name="Affect heuristic",
        finding=(
            "How an option feels arrives before any analysis and then steers the "
            "analysis - perceived benefit rises and perceived cost falls "
            "together, which real trade-offs rarely do."
        ),
        source="Slovic, Finucane, Peters & MacGregor (2007), EJOR 177(3), 1333-1352",
        lever=(
            "Interest is carried as its own weighted criterion, so preference is "
            "counted once and openly rather than leaking into every other score."
        ),
    ),
    "regret_aversion": Effect(
        name="Regret aversion",
        finding=(
            "Anticipated regret changes choices before the outcome exists, and "
            "regret over things not done outlasts regret over things done badly."
        ),
        source=(
            "Loomes & Sugden (1982), Economic Journal 92(368); "
            "Gilovich & Medvec (1995), Psychological Review 102(2)"
        ),
        lever=(
            "Every ranking is stored with its factors, so a later 'I should have "
            "done the other one' can be checked against what was actually known."
        ),
    ),
    "dissonance": Effect(
        name="Post-decision dissonance",
        finding=(
            "After a close call the chosen option starts looking better and the "
            "rejected one worse - the spread appears after the choice, not "
            "before it, which is why second-guessing a near-tie is wasted effort."
        ),
        source="Brehm (1956), Journal of Abnormal and Social Psychology 52(3)",
        lever=(
            "On a near-tie the engine says so and tells you to commit, rather than "
            "manufacturing a confidence the numbers do not support."
        ),
    ),
    "implementation_intentions": Effect(
        name="Implementation intentions",
        finding=(
            "Specifying when, where and how you will start roughly doubles "
            "follow-through versus holding the same goal without a plan "
            "(about d = 0.65 averaged across 94 studies)."
        ),
        source=(
            "Gollwitzer (1999), American Psychologist 54(7); "
            "Gollwitzer & Sheeran (2006), Advances in Exp. Social Psychology 38"
        ),
        lever=(
            "Every decision ends on a concrete first move rather than on the "
            "conclusion alone."
        ),
    ),
    "self_imposed_deadlines": Effect(
        name="Self-imposed deadlines",
        finding=(
            "People who set their own spaced deadlines outperform those who leave "
            "everything to the final due date - though they still set them "
            "later than would be optimal."
        ),
        source="Ariely & Wertenbroch (2002), Psychological Science 13(3), 219-224",
        lever=(
            "Important-but-not-urgent work is surfaced early so it can get a date "
            "before its deadline supplies one."
        ),
    ),
    "social_proof": Effect(
        name="Social proof / informational conformity",
        finding=(
            "Under uncertainty other people's choices are read as evidence, and "
            "the pull persists even when their information is no better than yours."
        ),
        source=(
            "Asch (1956), Psychological Monographs 70(9); "
            "Cialdini & Goldstein (2004), Annual Review of Psychology 55"
        ),
        lever=(
            "Your criteria and weights are recorded before any advice arrives, so "
            "you can see whether the advice changed the reasons or only the answer."
        ),
    ),
    "fomo": Effect(
        name="Fear of missing out",
        finding=(
            "FOMO predicts choosing the option that keeps you connected over the "
            "one you would otherwise prefer, and tracks lower mood and life "
            "satisfaction afterwards."
        ),
        source="Przybylski, Murayama, DeHaan & Gladwell (2013), Computers in Human Behavior 29(4)",
        lever=(
            "Social options get scored against the same stated criteria as "
            "everything else, so the pull has to compete on the record."
        ),
    ),
    "loss_aversion": Effect(
        name="Loss aversion & framing",
        finding=(
            "A loss is felt roughly twice as strongly as an equivalent gain, so a "
            "'20% off' frame moves choices more than the same option simply priced "
            "20% lower with no discount shown."
        ),
        source="Kahneman & Tversky (1979), Econometrica 47(2), 263-291",
        lever=(
            "Cost is weighted as a criterion you set, not as a headline number "
            "attached to one option."
        ),
    ),
    "sleep_debt": Effect(
        name="Sleep restriction and executive control",
        finding=(
            "Under 6 hours a night measurably degrades inhibitory control and "
            "risky decision-making, and self-rated alertness stops tracking the "
            "actual impairment after a few days."
        ),
        source="Killgore (2010), Progress in Brain Research 185, 105-129",
        lever=(
            "Health tasks are nudged upward, because on this evidence they sit "
            "upstream of every other decision in the list."
        ),
    ),
    "broken_leg": Effect(
        name="Clinical vs. actuarial judgement",
        finding=(
            "Across decades of repeated-prediction studies, a simple formula "
            "beats expert judgement more often than not. The documented "
            "exception is the 'broken leg' case - one rare fact the formula "
            "cannot see. Overriding pays when you have such a fact, and costs "
            "you when you are merely disagreeing with the number."
        ),
        source=(
            "Meehl (1954), Clinical versus Statistical Prediction; "
            "Dawes, Faust & Meehl (1989), Science 243(4899), 1668-1674"
        ),
        lever=(
            "Moving a task changes where it sits, never what it scores - so "
            "the engine's number stays on screen beside your judgement, and "
            "you can hand the task back with one click."
        ),
    ),
    "exercise_cognition": Effect(
        name="Exercise and executive function",
        finding=(
            "Aerobic activity produces reliable gains in executive function - "
            "the task-switching and inhibition that choosing what to do first "
            "runs on."
        ),
        source="Hillman, Erickson & Kramer (2008), Nature Reviews Neuroscience 9(1), 58-65",
        lever="Health tasks carry an importance boost when activity is low.",
    ),
}


def _link(
    key: str,
    *,
    signal: str,
    response: str | None = None,
    kind: str = "driver",
    strength: str = "moderate",
    metric: str | None = None,
) -> dict[str, Any]:
    """One correlation row: a finding plus the user's own evidence for it."""
    effect = EFFECTS[key]
    return {
        "key": key,
        "effect": effect.name,
        "finding": effect.finding,
        "source": effect.source,
        "signal": signal,
        "response": response or effect.lever,
        "kind": kind,          # driver | risk
        "strength": strength,  # strong | moderate
        "metric": metric,      # quantitative before -> after, where there is one
    }


def _dedupe(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First mention of an effect wins; drivers sort above risks."""
    seen: set[str] = set()
    unique = []
    for link in links:
        if link["key"] in seen:
            continue
        seen.add(link["key"])
        unique.append(link)
    order = {"driver": 0, "risk": 1}
    strength_order = {"strong": 0, "moderate": 1}
    unique.sort(key=lambda item: (order[item["kind"]], strength_order[item["strength"]]))
    return unique


# ---------- parameter deltas: the quantitative half of the correlation ----------

def engine_deltas(survey: dict | None) -> dict[str, dict[str, float]]:
    """Per parameter: base value, the raw lever the survey pulled, final share.

    `raw` is the weight the survey rules actually set; `share` is what it
    becomes after the three core weights are renormalised to sum to 1. They
    can disagree in sign - raising urgency by 0.08 while aging also rises by
    0.06 leaves urgency *larger in absolute terms but a smaller slice of the
    total*. Both are reported because either one alone tells half a story.
    """
    p = personalize(survey)
    raw_core = p.raw_core or dict(BASE_CORE_WEIGHTS)
    deltas = {
        name: {"base": base, "raw": raw_core[name], "share": p.core[name]}
        for name, base in BASE_CORE_WEIGHTS.items()
    }
    # These two are applied directly - there is no renormalisation step.
    for name, base, tuned in (
        ("effort_bonus", BASE_EFFORT_BONUS, p.effort_bonus),
        ("quick_win", BASE_QUICK_WIN, p.quick_win),
    ):
        deltas[name] = {"base": base, "raw": tuned, "share": tuned}
    return deltas


def _metric(deltas: dict[str, dict[str, float]], key: str, label: str) -> str | None:
    """'urgency weight 0.48 to 0.56 (+17%)', or None when the survey moved nothing.

    Reports the raw lever, which is the change the survey rule actually made.
    The resulting share after renormalisation is shown separately, in the
    parameters table on the Insights tab.
    """
    values = deltas.get(key)
    if not values:
        return None
    base, raw = values["base"], values["raw"]
    if abs(raw - base) < 5e-3:
        return None
    pct = (raw - base) / base * 100 if base else 0.0
    return f"{label} {base:.2f} to {raw:.2f} ({pct:+.0f}%)"


# ---------- correlation 1: the student's profile ----------

def profile_correlations(survey: dict | None) -> list[dict[str, Any]]:
    """Map the decision-making survey onto the research it lines up with."""
    if not survey:
        return []

    pick = survey.get
    deltas = engine_deltas(survey)
    factors = set(pick("choice_factors") or [])
    challenges = set(pick("study_challenges") or [])
    motivator = pick("motivator", "")
    style = pick("decision_style", "")
    many = pick("many_tasks", "")
    links: list[dict[str, Any]] = []

    # --- how they triage a full plate ---
    if many == "Do the most urgent" or motivator in ("A deadline", "Fear of falling behind"):
        links.append(_link(
            "temporal_discounting",
            signal=(
                f"You said {'you do the most urgent thing first' if many == 'Do the most urgent' else f'{motivator.lower()} is what keeps you moving'}."
            ),
            response=(
                "Urgency was weighted up, and it decays exponentially rather than "
                "linearly so it matches how deadline pressure actually feels to you."
            ),
            strength="strong",
            metric=_metric(deltas, "urgency", "urgency weight"),
        ))
        links.append(_link(
            "mere_urgency",
            signal="Deadline-first triage is exactly the pattern this effect predicts.",
            response=(
                "Your Eisenhower matrix flags urgent-but-unimportant tasks in their "
                "own quadrant, so the ones that are merely loud stay visible as such."
            ),
            kind="risk",
            strength="strong",
        ))

    if many == "Do the easiest first" or motivator == "A reward":
        links.append(_link(
            "goal_gradient",
            signal=(
                "You start with the easiest task"
                if many == "Do the easiest first"
                else "A reward is what keeps you going"
            ),
            response="The quick-win floor was raised, so short tasks surface sooner.",
            strength="strong",
            metric=_metric(deltas, "quick_win", "quick-win floor"),
        ))
        links.append(_link(
            "present_bias",
            signal="Easiest-first is present bias doing the choosing for you.",
            response=(
                "Quick wins are boosted, not handed the top spot - an easy task "
                "still has to beat a genuinely urgent one on the score."
            ),
            kind="risk",
        ))

    if many == "Freeze and do nothing for a while":
        links.append(_link(
            "decision_fatigue",
            signal="You said a full plate makes you freeze rather than choose.",
            response=(
                "The Up Next card is pre-decided: the deliberation is already "
                "spent, so there is nothing left to freeze on."
            ),
            strength="strong",
        ))

    # --- procrastination cluster ---
    procrastination = [
        label for key, label in (
            ("delay_start", "you often delay starting because you cannot pick a first move"),
            ("stuck_first", "you often feel stuck deciding what to do first"),
            ("regret", "you often wish afterwards that you had done something else first"),
        ) if pick(key) in OFTEN
    ]
    if style == "I often postpone decisions":
        procrastination.append("you describe yourself as postponing decisions")
    if pick("when_unsure") == "Postpone it":
        procrastination.append("when unsure you postpone")
    if "Procrastination" in challenges:
        procrastination.append("you named procrastination as a study challenge")
    if pick("deadline_start") in ("The night before", "Usually after it is due"):
        procrastination.append(
            f"you start {str(pick('deadline_start')).lower()}")

    if procrastination:
        links.append(_link(
            "temporal_motivation",
            signal="You told us " + "; ".join(procrastination[:2]) + ".",
            response=(
                "Aging was weighted up, so a task that has been sitting keeps "
                "climbing instead of waiting for its deadline to rescue it."
            ),
            strength="strong" if len(procrastination) >= 2 else "moderate",
            metric=_metric(deltas, "aging", "aging weight"),
        ))
        links.append(_link(
            "zeigarnik",
            signal="An open backlog plus delayed starts is the condition this effect describes.",
        ))

    if pick("stuck_first") in OFTEN or pick("decide_time") in ("5-15 min", "More"):
        links.append(_link(
            "attribute_substitution",
            signal=(
                "Choosing a first task regularly costs you real time - the sign "
                "of a hard question being answered by feel."
            ),
            kind="risk",
        ))

    # --- search style ---
    if style == "I compare many options before deciding" or \
            pick("when_unsure") == "Keep over-researching" or \
            pick("purchase_time") == "More than 2 hours":
        links.append(_link(
            "maximizing",
            signal=(
                "You compare exhaustively before committing"
                + (" and spend over two hours on a purchase decision"
                   if pick("purchase_time") == "More than 2 hours" else "")
                + "."
            ),
            kind="risk",
            strength="strong",
        ))
        links.append(_link(
            "choice_overload",
            signal="Exhaustive comparison is where added options start costing you.",
        ))

    if style == "I frequently ask others for advice" or pick("ask_friends_freq") in OFTEN:
        links.append(_link(
            "social_proof",
            signal="You often check with other people before deciding.",
            kind="risk",
        ))

    if pick("regret") in OFTEN or (pick("confidence_after") or 5) <= 2:
        links.append(_link(
            "regret_aversion",
            signal=(
                "You often second-guess the order you worked in"
                if pick("regret") in OFTEN
                else "You rate your confidence after deciding as low"
            ),
            response=(
                "Every ranking is stored with the factors behind it, so you can "
                "check a later regret against what was knowable at the time."
            ),
            kind="risk",
            strength="strong",
        ))
        links.append(_link(
            "dissonance",
            signal="Post-hoc regret is partly this: the gap between options widens after you choose.",
        ))

    # --- values ---
    if factors & {"Long-term benefits", "Personal growth"} or motivator == "A personal goal":
        links.append(_link(
            "self_imposed_deadlines",
            signal="You value long-term payoff over what is loudest today.",
            response=(
                "Importance was weighted up, and the Schedule quadrant exists to "
                "get those tasks a date before a deadline invents one."
            ),
            metric=_metric(deltas, "importance", "importance weight"),
        ))
    if "Time required" in factors or "Convenience" in factors:
        links.append(_link(
            "planning_fallacy",
            signal="You weigh how long something will take when you choose.",
            response=(
                "The effort bonus was raised, so a tight estimate-to-deadline "
                "ratio raises more alarm in your ranking."
            ),
            metric=_metric(deltas, "effort_bonus", "effort bonus"),
        ))
    elif "Time management" in challenges:
        links.append(_link(
            "planning_fallacy",
            signal="You named time management as a study challenge.",
            kind="risk",
        ))
    if "Fun / Enjoyment" in factors or pick("career_priority") == "Passion":
        links.append(_link(
            "affect_heuristic",
            signal="Enjoyment and passion are explicit factors in how you choose.",
        ))
    if "Cost" in factors or pick("purchase_influence") in ("Price", "Discounts"):
        links.append(_link(
            "loss_aversion",
            signal=(
                f"Your purchase decisions turn on {str(pick('purchase_influence')).lower()}."
                if pick("purchase_influence") in ("Price", "Discounts")
                else "Cost is one of the factors you weigh."
            ),
            kind="risk",
        ))
    if pick("social_challenge") == "Fear of missing out (FOMO)":
        links.append(_link(
            "fomo",
            signal="You named FOMO as your hardest part of social decisions.",
            kind="risk",
        ))

    # --- detail from the branches the student unlocked ---
    if pick("plan_horizon") == "I don't plan" or pick("accountability") == "Much more":
        links.append(_link(
            "implementation_intentions",
            signal=(
                "You don't plan the day ahead at all"
                if pick("plan_horizon") == "I don't plan"
                else "You follow through much more when someone else knows the plan"
            ),
            response=(
                "Every decision ends on a concrete first move - a when and a "
                "where, which is the part that does the work."
            ),
            strength="strong",
        ))
    if pick("overcommit") in OFTEN:
        links.append(_link(
            "planning_fallacy",
            signal="You often say yes to more than actually fits in the day.",
            response=(
                "The effort term measures your estimate against the time "
                "genuinely left, so an overfull day shows up as alarm rather "
                "than as a surprise at midnight."
            ),
            kind="risk",
            strength="strong",
        ))
    if "The task feels too big" in (pick("motivation_dip") or []) or \
            (pick("restart_effort") or 0) >= 4:
        links.append(_link(
            "goal_gradient",
            signal=(
                "Tasks that feel too big are what stall you"
                if "The task feels too big" in (pick("motivation_dip") or [])
                else "You find it very hard to restart once you have stopped"
            ),
            response=(
                "Quick wins keep a floor under short tasks, so there is always "
                "something small enough to actually begin."
            ),
            strength="strong",
        ))
    if pick("energy_dip") == "Right after lunch":
        links.append(_link(
            "decision_fatigue",
            signal="Your energy crashes right after lunch.",
            response=(
                "That is the documented dip, not a character flaw - put the "
                "pre-decided task there, not the choosing."
            ),
            strength="strong",
        ))
    if "Deciding what to do" in (pick("time_leak") or []):
        links.append(_link(
            "attribute_substitution",
            signal="You named deciding what to do as one of your real time leaks.",
            kind="risk",
            strength="strong",
        ))
    if pick("purchase_regret") in OFTEN:
        links.append(_link(
            "maximizing",
            signal="You often regret purchases after the fact.",
            kind="risk",
            strength="strong",
        ))
    if pick("social_guilt") in OFTEN:
        links.append(_link(
            "fomo",
            signal="You often go out when you would rather have rested.",
            kind="risk",
            strength="strong",
        ))
    if (pick("career_clarity") or 5) <= 2 or "Too many options" in (pick("career_blocker") or []):
        links.append(_link(
            "choice_overload",
            signal=(
                "Career is still unclear to you"
                if (pick("career_clarity") or 5) <= 2
                else "You named 'too many options' as what makes career decisions hard"
            ),
            kind="risk",
        ))

    # --- physiology: upstream of every other decision ---
    if pick("sleep_hours") in ("Less than 4", "4-6"):
        links.append(_link(
            "sleep_debt",
            signal=f"You sleep {pick('sleep_hours')} hours on an average day.",
            strength="strong",
        ))
    if pick("exercise_freq") in ("Never", "Rarely"):
        links.append(_link(
            "exercise_cognition",
            signal=f"You exercise {str(pick('exercise_freq')).lower()}.",
        ))

    return _dedupe(links)


# ---------- correlation 2: the Eisenhower distribution ----------

QUADRANT_SCIENCE: dict[str, dict[str, str]] = {
    "do": {
        "title": "Do",
        "rule": "Urgent and important",
        "science": (
            "A full Q1 is the firefighting state: every choice is forced by a "
            "deadline, which is where decision quality degrades fastest."
        ),
        "source": "Covey (1989), The 7 Habits of Highly Effective People, Quadrant II",
    },
    "schedule": {
        "title": "Decide",
        "rule": "Important, not yet urgent - give it a date",
        "science": (
            "This is the quadrant that pays, and the one hyperbolic discounting "
            "empties - it has no deadline pressure to make it feel real yet."
        ),
        "source": "Ariely & Wertenbroch (2002), Psychological Science 13(3)",
    },
    "delegate": {
        "title": "Delegate",
        "rule": "Urgent, not important - hand off or batch",
        "science": (
            "The mere-urgency trap. These beat important work on salience alone, "
            "so batch them into one slot instead of letting them interrupt."
        ),
        "source": "Zhu, Yang & Hsee (2018), Journal of Consumer Research 45(3)",
    },
    "eliminate": {
        "title": "Delete",
        "rule": "Neither urgent nor important",
        "science": (
            "Carrying these costs attention even untouched - open loops intrude "
            "whether or not you work on them."
        ),
        "source": "Masicampo & Baumeister (2011), JPSP 101(4)",
    },
}


def matrix_correlations(
    counts: dict[str, int], overrides: int = 0
) -> list[dict[str, Any]]:
    """Read the shape of the user's matrix, not just its contents."""
    total = sum(counts.values())
    if not total:
        return []
    links: list[dict[str, Any]] = []

    if overrides:
        links.append(_link(
            "broken_leg",
            signal=(
                f"You have moved {overrides} task(s) out of the quadrant the "
                "engine picked for them."
            ),
            response=(
                "Worth doing when you know something the model cannot - a "
                "lecturer who moved a deadline, a task that is really someone "
                "else's. Worth undoing if it was just the number feeling wrong: "
                "each moved task shows where the engine had put it."
            ),
            kind="risk",
            strength="strong",
        ))

    if counts.get("delegate"):
        links.append(_link(
            "mere_urgency",
            signal=(
                f"{counts['delegate']} of your {total} pending tasks are urgent but "
                "not important - the quadrant that steals time from the one that pays."
            ),
            response="Batch them into a single slot instead of answering them as they land.",
            kind="risk",
            strength="strong",
        ))
    if counts.get("schedule"):
        links.append(_link(
            "self_imposed_deadlines",
            signal=(
                f"{counts['schedule']} important task(s) have no deadline pressure yet."
            ),
            response=(
                "Give each one your own earlier date now; self-set deadlines beat "
                "waiting for the real one."
            ),
            strength="strong",
        ))
    if counts.get("do", 0) >= 3:
        links.append(_link(
            "decision_fatigue",
            signal=f"{counts['do']} tasks are urgent AND important at once.",
            response=(
                "Work strictly down the ranked order - re-choosing between them "
                "each time is the part that degrades."
            ),
            kind="risk",
            strength="strong",
        ))
    if counts.get("eliminate"):
        links.append(_link(
            "zeigarnik",
            signal=f"{counts['eliminate']} task(s) are neither urgent nor important.",
            response="Drop them or give them a date; either one closes the loop.",
        ))
    if not counts.get("schedule") and counts.get("do"):
        links.append(_link(
            "temporal_discounting",
            signal="Everything important on your list is already urgent.",
            response=(
                "Nothing was caught early. Adding important work before it has a "
                "deadline is what keeps Q1 from filling up."
            ),
            kind="risk",
        ))
    return _dedupe(links)


# ---------- correlation 3: one option-level decision ----------

def decision_correlations(
    task,
    payload: dict,
    result: dict,
    survey: dict | None,
) -> list[dict[str, Any]]:
    """The behavioural reading of one completed decision."""
    criteria = result.get("criteria") or payload.get("criteria") or []
    options = payload.get("options") or []
    ratings = payload.get("ratings") or {}
    best = result.get("best")
    clarity = result.get("clarity") or 0.0
    links: list[dict[str, Any]] = []

    # The method itself is the first correlation - it is why this is not a hunch.
    links.append(_link(
        "wadd",
        signal=(
            f"You compared {len(options)} options on {len(criteria)} criteria - "
            f"{len(options) * len(criteria)} separate judgements, past the point "
            "where an unaided choice stays compensatory."
        ),
        response=(
            "The weighted-additive rule was applied externally, so every criterion "
            "still counts in the final number instead of being dropped under load."
        ),
        strength="strong",
    ))

    if len(options) >= 5:
        links.append(_link(
            "choice_overload",
            signal=f"{len(options)} options is at the edge where added choice starts to cost.",
            response=(
                "Scoring them against fixed criteria keeps the comparison stable "
                "as the set grows - the part unaided comparison loses first."
            ),
            kind="risk",
        ))

    # Which tag carried the winner?
    best_ratings = ratings.get(best, {})
    by_tag: dict[str, float] = {}
    for criterion in criteria:
        weight = criterion.get("effective_weight", criterion.get("weight", 1))
        contribution = weight * best_ratings.get(criterion["name"], 0) / 5.0
        by_tag[criterion.get("tag", "other")] = by_tag.get(criterion.get("tag", "other"), 0.0) + contribution
    lead_tag = max(by_tag, key=by_tag.get) if by_tag else None
    lead_share = (by_tag[lead_tag] / sum(by_tag.values())) if by_tag and sum(by_tag.values()) else 0.0

    if lead_tag == "urgency" and lead_share >= 0.4:
        links.append(_link(
            "mere_urgency",
            signal=(
                f"{lead_share:.0%} of {best}'s score came from urgency criteria."
            ),
            response=(
                "That may well be right - but check that it is a real deadline "
                "and not just the option that shouts loudest."
            ),
            kind="risk",
            strength="strong",
        ))
    elif lead_tag == "interest" and lead_share >= 0.4:
        links.append(_link(
            "affect_heuristic",
            signal=f"{lead_share:.0%} of {best}'s score came from how much you want it.",
            response=(
                "Preference is counted once, openly, as its own criterion - rather "
                "than quietly inflating the ratings you gave everything else."
            ),
            kind="risk",
        ))
    elif lead_tag == "cost" and lead_share >= 0.4:
        links.append(_link(
            "loss_aversion",
            signal=f"{lead_share:.0%} of {best}'s score came from cost.",
            kind="risk",
        ))

    if result.get("ratings_source") == "gemini":
        links.append(_link(
            "anchoring",
            signal="AVEX supplied the first number on every rating in this grid.",
            response=(
                "Each rating shows the reasoning behind it and stays editable. "
                "Open 'Adjust ratings' on anything that reads wrong - disagreeing "
                "with a visible anchor is far easier than with an invisible one."
            ),
            kind="risk",
            strength="strong",
        ))

    if clarity < 0.10:
        links.append(_link(
            "dissonance",
            signal=f"The top two options are {clarity:.0%} apart - statistically a tie.",
            response=(
                "Do not buy more analysis with the difference. Commit; the "
                "confidence arrives after the choice, not before it."
            ),
            strength="strong",
        ))
        links.append(_link(
            "maximizing",
            signal="A near-tie is exactly where further searching stops paying.",
        ))
    else:
        links.append(_link(
            "implementation_intentions",
            signal=f"{best} leads by {clarity:.0%} - clear enough to plan against.",
            response=(
                f"Name the when and where now: 'after {'class' if task.category == 'Study' else 'dinner'} "
                f"today, I start {best} for 25 minutes.'"
            ),
            strength="strong",
        ))

    if survey:
        # Profile risks that bear directly on an option-level choice.
        keep = {"maximizing", "social_proof", "regret_aversion", "anchoring", "fomo"}
        links.extend(l for l in profile_correlations(survey) if l["key"] in keep)

    return _dedupe(links)
