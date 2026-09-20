"""Option-level decision engine (multi-criteria decision analysis).

When a user opens one task ("Which subject to study first", "Which laptop to
buy") they list their *options*, keep or edit the category's suggested
*criteria* (each weighted 1-5), and rate every option on every criterion
(1-5). The engine then computes, for option i:

    score_i = 100 * sum over j of ( w_j * r_ij / 5 )

where w_j are the normalised *effective* criterion weights:

    effective_j = user_weight_j * survey_multiplier(tag_j)

Each criterion carries a semantic tag (urgency / importance / interest /
effort / cost / quality). The User Decision-Making Survey boosts the tags
that match how this student actually decides - a deadline-driven student's
"upcoming test" criterion counts ~20% more, a price-conscious student's
"value for money" counts ~20% more, and so on. Every boost is reported back
as a human-readable note so the decision stays explainable.

A third multiplier stacks on top when the student has opted into the feedback
loop: `learned`, the per-tag weight feedback.py has arrived at from how their
past decisions actually turned out. The survey says how a student *describes*
their decision-making; the learner says how it has gone. Both are reported
separately in the audit's weight table, so neither can move a weight
invisibly.

Clarity mirrors the ranking metric:  clarity = (s1 - s2) / s1.

`build_audit` re-derives that result from first principles for the Audit
panel: the normalised weight table, every option's per-criterion point
contribution, a dominance and equal-weights robustness check, and a formal
sensitivity analysis (how far a weight or a rating would have to move before
the runner-up takes over). See its docstring for the derivation.
"""

from __future__ import annotations

from typing import Any

TAGS = ("urgency", "importance", "interest", "effort", "cost", "quality", "other")

# Suggested criteria per task category. Weights are starting points the
# user can change; hints explain what a 5 means when rating options.
CATEGORY_TEMPLATES: dict[str, dict[str, Any]] = {
    "Study": {
        "options_prompt": "Which subjects or topics are you choosing between?",
        "options_placeholder": "e.g. Maths, Physics, DBMS",
        "criteria": [
            {"name": "Upcoming test or exam", "tag": "urgency", "weight": 5,
             "hint": "5 = a test on this is very soon"},
            {"name": "Upcoming assignment", "tag": "urgency", "weight": 4,
             "hint": "5 = an assignment on this is due soon"},
            {"name": "Importance for grades", "tag": "importance", "weight": 4,
             "hint": "5 = weighs heavily on your result"},
            {"name": "Want to study it right now", "tag": "interest", "weight": 3,
             "hint": "5 = you actually feel like doing this now"},
        ],
    },
    "Purchases": {
        "options_prompt": "Which products or choices are you comparing?",
        "options_placeholder": "e.g. MacBook Air, ThinkPad, Zephyrus",
        "criteria": [
            {"name": "Value for money", "tag": "cost", "weight": 5,
             "hint": "5 = great price for what you get"},
            {"name": "Quality & reviews", "tag": "quality", "weight": 4,
             "hint": "5 = excellent quality / rated highly"},
            {"name": "How much you need it", "tag": "urgency", "weight": 4,
             "hint": "5 = you need it right away"},
            {"name": "Excitement", "tag": "interest", "weight": 2,
             "hint": "5 = you really want this one"},
        ],
    },
    "Travel": {
        "options_prompt": "Which destinations or plans are you choosing between?",
        "options_placeholder": "e.g. Goa, Manali, stay home",
        "criteria": [
            {"name": "Cost fits budget", "tag": "cost", "weight": 5,
             "hint": "5 = comfortably affordable"},
            {"name": "Ease of planning & travel", "tag": "effort", "weight": 3,
             "hint": "5 = simple to organise and reach"},
            {"name": "Excitement", "tag": "interest", "weight": 4,
             "hint": "5 = you really want to go"},
            {"name": "Fits your dates", "tag": "urgency", "weight": 3,
             "hint": "5 = works perfectly with your schedule"},
        ],
    },
    "Career": {
        "options_prompt": "Which paths or opportunities are you comparing?",
        "options_placeholder": "e.g. Internship A, Research role, Higher studies",
        "criteria": [
            {"name": "Growth & learning", "tag": "importance", "weight": 5,
             "hint": "5 = you will grow a lot"},
            {"name": "Passion fit", "tag": "interest", "weight": 4,
             "hint": "5 = genuinely excites you"},
            {"name": "Pay / benefit", "tag": "cost", "weight": 3,
             "hint": "5 = strong financial upside"},
            {"name": "Long-term stability", "tag": "quality", "weight": 3,
             "hint": "5 = safe, durable choice"},
        ],
    },
    "Health": {
        "options_prompt": "Which activities or plans are you choosing between?",
        "options_placeholder": "e.g. Gym, Run, Yoga",
        "criteria": [
            {"name": "Health impact", "tag": "importance", "weight": 5,
             "hint": "5 = big benefit for you"},
            {"name": "Enjoyment", "tag": "interest", "weight": 3,
             "hint": "5 = you'll actually enjoy it"},
            {"name": "Fits your time & energy", "tag": "effort", "weight": 3,
             "hint": "5 = easy to fit in today"},
        ],
    },
    "Entertainment": {
        "options_prompt": "What are the options?",
        "options_placeholder": "e.g. Movie, Gaming, Concert",
        "criteria": [
            {"name": "Fun factor", "tag": "interest", "weight": 5,
             "hint": "5 = maximum fun"},
            {"name": "Cost", "tag": "cost", "weight": 3,
             "hint": "5 = cheap or free"},
            {"name": "Time it takes", "tag": "effort", "weight": 2,
             "hint": "5 = fits easily in your day"},
        ],
    },
}

DEFAULT_TEMPLATE: dict[str, Any] = {
    "options_prompt": "What are the options you're deciding between?",
    "options_placeholder": "e.g. Option A, Option B",
    "criteria": [
        {"name": "Importance", "tag": "importance", "weight": 5,
         "hint": "5 = matters a lot"},
        {"name": "Urgency", "tag": "urgency", "weight": 4,
         "hint": "5 = needs action right away"},
        {"name": "Interest", "tag": "interest", "weight": 3,
         "hint": "5 = you want to do it"},
        {"name": "Effort required", "tag": "effort", "weight": 2,
         "hint": "5 = easy to do"},
    ],
}


def get_template(category: str) -> dict[str, Any]:
    template = CATEGORY_TEMPLATES.get(category, DEFAULT_TEMPLATE)
    return {"category": category, **template}


def survey_multipliers(survey: dict | None) -> tuple[dict[str, float], list[str]]:
    """Map the survey onto per-tag weight multipliers, with explanations."""
    mult: dict[str, float] = {tag: 1.0 for tag in TAGS}
    notes: list[str] = []
    if not survey:
        return mult, notes

    factors = set(survey.get("choice_factors") or [])
    motivator = survey.get("motivator", "")

    if motivator in ("A deadline", "Fear of falling behind") or \
            survey.get("many_tasks") == "Do the most urgent":
        mult["urgency"] *= 1.20
        notes.append("Deadline-driven (survey): urgency criteria weighted +20%")
    if motivator == "A reward" or "Fun / Enjoyment" in factors:
        mult["interest"] *= 1.15
        notes.append("Enjoyment matters to you: interest criteria weighted +15%")
    if factors & {"Long-term benefits", "Personal growth"} or motivator == "A personal goal":
        mult["importance"] *= 1.15
        notes.append("Long-term focused: importance criteria weighted +15%")
    if "Cost" in factors or survey.get("purchase_influence") in ("Price", "Discounts"):
        mult["cost"] *= 1.20
        notes.append("Price-conscious: cost criteria weighted +20%")
    if "Quality" in factors or survey.get("purchase_influence") in ("Quality", "Reviews and ratings"):
        mult["quality"] *= 1.15
        notes.append("Quality-focused: quality criteria weighted +15%")
    if "Time required" in factors or "Convenience" in factors:
        mult["effort"] *= 1.15
        notes.append("Time/convenience sensitive: effort criteria weighted +15%")
    if survey.get("career_priority") == "Passion":
        mult["interest"] *= 1.10
        notes.append("Passion-first career answers: interest criteria weighted +10%")
    return mult, notes


def decide(
    options: list[str],
    criteria: list[dict[str, Any]],
    ratings: dict[str, dict[str, int]],
    survey: dict | None,
    learned: dict[str, float] | None = None,
    learned_notes: list[str] | None = None,
) -> dict[str, Any]:
    """Score every option; return the full, explainable result.

    `learned` is the per-tag multiplier the feedback loop has arrived at for
    this user (see feedback.py). It stacks on top of the survey's multiplier
    rather than replacing it: the survey says how the student *describes*
    their decision-making, the learner says how it has actually gone. Both
    are reported in the audit, so neither can move a weight invisibly.
    """
    mult, notes = survey_multipliers(survey)
    notes = notes + list(learned_notes or [])
    learned = learned or {}

    effective = []
    for criterion in criteria:
        tag = criterion.get("tag", "other")
        survey_mult = mult.get(tag, 1.0)
        learned_mult = float(learned.get(tag, 1.0))
        effective.append({
            "name": criterion["name"],
            "tag": tag,
            "weight": criterion["weight"],
            "survey_multiplier": round(survey_mult, 4),
            "learned_multiplier": round(learned_mult, 4),
            "effective_weight": criterion["weight"] * survey_mult * learned_mult,
        })
    total_weight = sum(c["effective_weight"] for c in effective) or 1.0

    scores = []
    for option in options:
        option_ratings = ratings.get(option, {})
        value = sum(
            c["effective_weight"] * option_ratings.get(c["name"], 1) / 5.0
            for c in effective
        ) / total_weight
        scores.append({"option": option, "score": round(value * 100, 1)})
    scores.sort(key=lambda s: s["score"], reverse=True)

    best = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    clarity = 0.0
    if runner_up and best["score"] > 0:
        clarity = round((best["score"] - runner_up["score"]) / best["score"], 4)
    elif not runner_up:
        clarity = 1.0

    # Which criteria won it: the best option's largest weighted contributions.
    best_ratings = ratings.get(best["option"], {})
    drivers = sorted(
        effective,
        key=lambda c: c["effective_weight"] * best_ratings.get(c["name"], 0),
        reverse=True,
    )[:2]

    return {
        "best": best["option"],
        "scores": scores,
        "clarity": clarity,
        "criteria": [
            {**c, "effective_weight": round(c["effective_weight"], 3)} for c in effective
        ],
        "drivers": [d["name"] for d in drivers],
        "personalization_notes": notes,
    }


# ======================================================================
#  AUDIT - the decision, re-derived so it can be checked rather than
#  trusted. Everything below reads the stored decision; none of it can
#  change a score.
# ======================================================================

METHOD = {
    "name": "Simple Additive Weighting (SAW)",
    "family": "the weighted-sum form of Multi-Attribute Utility Theory (MAUT)",
    # Indices are written as _i / _j and turned into <sub> by the frontend -
    # Unicode subscripts and combining macrons are missing from too many
    # monospace fallbacks to be legible in the browser.
    "formula": "S_i = 100 × SUM_j ( w_j · r_ij / 5 )      where SUM_j w_j = 1",
    "source": (
        "Fishburn (1967), Management Science 13(7); "
        "Keeney & Raiffa (1976), Decisions with Multiple Objectives. "
        "Sensitivity analysis after Triantaphyllou & Sánchez (1997), "
        "Decision Sciences 28(1), 151-194."
    ),
    "steps": [
        "Each criterion's 1-5 importance is multiplied by the tag multiplier "
        "your survey earned it, giving an effective weight.",
        "Effective weights are normalised to sum to 1, so the result is a "
        "convex combination and every score lands in 0-100%.",
        "Each 1-5 rating becomes a value on a common scale, v = r / 5, so "
        "criteria measured in different units can be added at all.",
        "An option's score is the weighted sum of its values - a strong "
        "showing on a heavy criterion can compensate for a weak one elsewhere.",
    ],
    "assumptions": [
        "Preferential independence - how good an option is on one criterion "
        "does not depend on how it scores on another.",
        "Interval-scale ratings - the step from 3 to 4 is taken to be worth "
        "the same as the step from 4 to 5.",
        "A linear value function, v(r) = r / 5, with no diminishing returns.",
        "Full compensation - strength on one criterion can buy back weakness "
        "on another. A hard limit (a budget you cannot exceed) is not a low "
        "rating; remove that option instead.",
        "The arithmetic is exact, the ratings are judgements. The audit can "
        "show you how much a rating would have to be wrong to matter - it "
        "cannot tell you that it is right.",
    ],
}


def _weight_table(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise the effective weights and show every step of the arithmetic."""
    total = sum(c.get("effective_weight", c.get("weight", 1)) for c in criteria) or 1.0
    table = []
    for criterion in criteria:
        raw = criterion.get("weight", 1)
        effective = criterion.get("effective_weight", raw)
        multiplier = effective / raw if raw else 1.0
        table.append({
            "name": criterion["name"],
            "tag": criterion.get("tag", "other"),
            "user_weight": raw,
            "multiplier": round(multiplier, 3),
            # The combined multiplier above, split into where it came from.
            # Decisions stored before the feedback loop existed carry neither
            # key, so both fall back to "all of it was the survey".
            "survey_multiplier": round(criterion.get("survey_multiplier", multiplier), 3),
            "learned_multiplier": round(criterion.get("learned_multiplier", 1.0), 3),
            "effective_weight": round(effective, 3),
            "normalized": round(effective / total, 4),
            "share_pct": round(effective / total * 100, 1),
            "adjusted": abs(multiplier - 1.0) > 1e-6,
        })
    return table


def _contributions(
    options: list[str],
    weights: list[dict[str, Any]],
    ratings: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    """Per option: where each of its points came from."""
    rows = []
    for option in options:
        option_ratings = ratings.get(option, {})
        parts = []
        total = 0.0
        for weight in weights:
            rating = option_ratings.get(weight["name"], 0)
            points = 100.0 * weight["normalized"] * rating / 5.0
            total += points
            parts.append({
                "criterion": weight["name"],
                "rating": rating,
                "value": round(rating / 5.0, 2),
                "weight": weight["normalized"],
                "points": round(points, 1),
            })
        for part in parts:
            part["share_pct"] = round(part["points"] / total * 100, 1) if total else 0.0
        parts.sort(key=lambda p: p["points"], reverse=True)
        rows.append({"option": option, "total": round(total, 1), "parts": parts})
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def _sensitivity(
    weights: list[dict[str, Any]],
    ratings: dict[str, dict[str, int]],
    winner: str,
    runner_up: str,
) -> dict[str, Any]:
    """How wrong could this be and still come out the same way?

    Working in normalised weights w_j and values v_ij = r_ij/5, let
    d_j = v_Aj - v_Bj for winner A and runner-up B, and

        D = sum over j of w_j * d_j     (so S_A - S_B = 100 D, and D > 0)

    *Weight test.* Move w_j by delta and rescale the other weights proportionally
    so they still sum to 1. Setting the new margin to zero gives, after the
    cancellation, the closed form

        delta_j = (1 - w_j) D / (D - d_j)

    which is feasible only while w_j + delta_j stays inside [0, 1]. Where it is
    infeasible, no change to that one criterion's weight can flip the result.

    *Rating test.* Dropping the winner's rating on criterion j by x points
    costs it 20 * w_j * x, so the flip needs x >= 5D / w_j, feasible only while
    the rating stays on the 1-5 scale.
    """
    winner_ratings = ratings.get(winner, {})
    runner_ratings = ratings.get(runner_up, {})

    diffs = {
        w["name"]: (winner_ratings.get(w["name"], 0) - runner_ratings.get(w["name"], 0)) / 5.0
        for w in weights
    }
    margin = sum(w["normalized"] * diffs[w["name"]] for w in weights)

    weight_tests: list[dict[str, Any]] = []
    rating_tests: list[dict[str, Any]] = []
    for w in weights:
        name, w_j, d_j = w["name"], w["normalized"], diffs[w["name"]]

        denominator = margin - d_j
        if abs(denominator) > 1e-9:
            delta = (1.0 - w_j) * margin / denominator
            new_weight = w_j + delta
            if -1e-9 <= new_weight <= 1.0 + 1e-9 and abs(delta) > 1e-9:
                weight_tests.append({
                    "criterion": name,
                    "current_pct": round(w_j * 100, 1),
                    "flip_at_pct": round(max(0.0, min(1.0, new_weight)) * 100, 1),
                    "move_pts": round(delta * 100, 1),
                    "relative_pct": round(abs(delta) / w_j * 100, 0) if w_j else None,
                    "direction": "up" if delta > 0 else "down",
                })

        if w_j > 1e-9:
            needed = 5.0 * margin / w_j
            winner_rating = winner_ratings.get(name, 0)
            runner_rating = runner_ratings.get(name, 0)
            if needed <= winner_rating - 1 + 1e-9:
                rating_tests.append({
                    "criterion": name,
                    "option": winner,
                    "direction": "down",
                    "points": round(needed, 2),
                    "from": winner_rating,
                    "to": round(winner_rating - needed, 2),
                })
            if needed <= 5 - runner_rating + 1e-9:
                rating_tests.append({
                    "criterion": name,
                    "option": runner_up,
                    "direction": "up",
                    "points": round(needed, 2),
                    "from": runner_rating,
                    "to": round(runner_rating + needed, 2),
                })

    weight_tests.sort(key=lambda t: abs(t["move_pts"]))
    rating_tests.sort(key=lambda t: t["points"])

    return {
        "margin_pts": round(margin * 100, 1),
        "weight_tests": weight_tests[:4],
        "rating_tests": rating_tests[:3],
        "critical_criterion": weight_tests[0]["criterion"] if weight_tests else None,
        "min_rating_change": rating_tests[0]["points"] if rating_tests else None,
    }


def _robustness(
    weights: list[dict[str, Any]],
    ratings: dict[str, dict[str, int]],
    options: list[str],
    winner: str,
    runner_up: str,
    min_rating_change: float | None,
    margin_pts: float,
) -> dict[str, Any]:
    """Three checks that do not depend on the weights being exactly right."""
    winner_ratings = ratings.get(winner, {})
    runner_ratings = ratings.get(runner_up, {})
    names = [w["name"] for w in weights]

    # 1. Pareto dominance - at least as good everywhere, better somewhere.
    never_worse = all(
        winner_ratings.get(n, 0) >= runner_ratings.get(n, 0) for n in names
    )
    better_somewhere = any(
        winner_ratings.get(n, 0) > runner_ratings.get(n, 0) for n in names
    )
    dominates = never_worse and better_somewhere

    # 2. Equal weights - does the win survive throwing the weighting away?
    equal = {
        option: sum(ratings.get(option, {}).get(n, 0) for n in names) / (len(names) or 1)
        for option in options
    }
    equal_winner = max(equal, key=equal.get) if equal else None

    # 3. Criterion-by-criterion tally (a Condorcet-style majority check).
    won = sum(1 for n in names if winner_ratings.get(n, 0) > runner_ratings.get(n, 0))
    tied = sum(1 for n in names if winner_ratings.get(n, 0) == runner_ratings.get(n, 0))

    if dominates:
        verdict, headline = "unconditional", (
            f"{winner} is at least as good as {runner_up} on every single "
            "criterion. No set of weights could reverse this."
        )
    elif margin_pts < 0.05:
        # A dead heat. Saying a 0.0-point error would flip it is technically
        # true and completely useless; name the tie instead.
        verdict, headline = "tie", (
            f"{winner} and {runner_up} score identically - this is a genuine "
            "tie, and the order between them is arbitrary. Either add a "
            "criterion that actually separates them, or take the one you "
            "would regret not taking and start."
        )
    elif min_rating_change is None:
        verdict, headline = "robust", (
            "No single rating, moved anywhere on the 1-5 scale, flips this result."
        )
    elif min_rating_change >= 1.0:
        verdict, headline = "stable", (
            f"One rating would have to be wrong by {min_rating_change:.1f} points "
            "before the answer changes."
        )
    else:
        verdict, headline = "fragile", (
            f"A single rating being off by {min_rating_change:.1f} points would "
            "flip this. Treat it as close, and check the grid below."
        )

    return {
        "dominates": dominates,
        "verdict": verdict,
        "headline": headline,
        "equal_weight_winner": equal_winner,
        "weight_dependent": equal_winner != winner,
        "criteria_won": won,
        "criteria_tied": tied,
        "criteria_total": len(names),
    }


def build_audit(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Re-derive a stored decision so the user can check it, not just read it.

    Reads only what was saved, so decisions made before the audit existed get
    one too. Returns None-free plain data ready for the API.
    """
    options = list(payload.get("options") or [])
    ratings = payload.get("ratings") or {}
    criteria = result.get("criteria") or payload.get("criteria") or []
    if len(options) < 2 or not criteria:
        return {}

    weights = _weight_table(criteria)
    contributions = _contributions(options, weights, ratings)
    winner = contributions[0]["option"]
    runner_up = contributions[1]["option"]

    sensitivity = _sensitivity(weights, ratings, winner, runner_up)
    robustness = _robustness(
        weights, ratings, options, winner, runner_up,
        sensitivity["min_rating_change"], abs(sensitivity["margin_pts"]),
    )

    gap = contributions[0]["total"] - contributions[1]["total"]
    top = contributions[0]
    decisive = top["parts"][0] if top["parts"] else None

    return {
        "method": METHOD,
        "weights": weights,
        "weight_total": round(
            sum(c.get("effective_weight", c.get("weight", 1)) for c in criteria), 3
        ),
        "contributions": contributions,
        "margin": {
            "winner": winner,
            "runner_up": runner_up,
            "winner_score": top["total"],
            "runner_up_score": contributions[1]["total"],
            "gap_pts": round(gap, 1),
            "clarity_pct": round(result.get("clarity", 0.0) * 100, 1),
            "decisive_criterion": decisive["criterion"] if decisive else None,
            "decisive_share_pct": decisive["share_pct"] if decisive else None,
        },
        "robustness": robustness,
        "sensitivity": sensitivity,
        "personalization_notes": result.get("personalization_notes") or [],
    }
