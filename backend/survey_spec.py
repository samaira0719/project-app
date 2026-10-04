"""User Decision-Making Survey - single source of truth.

The frontend renders the wizard from GET /api/survey/spec and the backend
validates submissions against the same spec, so questions live in one place.

Question types:
  single - pick exactly one option
  multi  - pick one or more options
  scale  - integer 1..5
  number - integer within [min, max]
  text   - free text (optional unless required=True)

Branching
---------
Steps 1-4 are asked of everybody: they carry every answer the scoring engine
and the option-level decision engine actually read (motivator, many_tasks,
choice_factors, when_unsure, the procrastination scales...).
Nothing downstream can lose an input it depends on.

Step 4 ("Where you need help") is the branch point. Each later section
declares a `depends_on` naming the support areas that unlock it, so a student
who asks for help with Studies and Health answers those two deep-dives and
skips Career, Travel, Purchases and the rest. Sections carry:

    depends_on  {"key": ..., "contains": x}  or  {"key": ..., "contains_any": [...]}
    because     the phrase the wizard shows to explain why this step appeared
    accent      a colour token, so each branch reads as its own path

Both `depends_on` forms work at question level too (see `support_other`).
"""

from typing import Any

FREQ = ["Never", "Rarely", "Sometimes", "Often", "Very Often"]

SURVEY_SPEC: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- core
    {
        "id": "intro",
        "title": "About you",
        "blurb": "A few basics so the engine knows who it is helping.",
        "accent": "sage",
        "questions": [
            {"key": "age", "label": "What is your age?", "type": "number", "min": 14, "max": 99},
            {"key": "gender", "label": "Gender", "type": "single",
             "options": ["Male", "Female", "Other", "Prefer not to say"]},
        ],
    },
    {
        "id": "scales",
        "title": "How you decide",
        "blurb": "Be honest - there are no wrong answers.",
        "accent": "sage",
        "questions": [
            {"key": "delay_start",
             "label": "How often do you delay starting work because you cannot decide where to begin?",
             "type": "single", "options": FREQ},
            {"key": "confidence_after", "label": "How confident do you feel after making a decision?",
             "type": "scale", "low": "Not confident at all", "high": "Extremely confident"},
        ],
    },
    {
        "id": "behavior",
        "title": "Your patterns",
        "blurb": "How you actually behave when the to-do list piles up.",
        "accent": "sage",
        "questions": [
            {"key": "many_tasks", "label": "When you have many tasks, you usually:",
             "type": "single", "options": [
                 "Do the easiest first", "Do the most urgent",
                 "Do whatever you feel like", "Freeze and do nothing for a while"]},
            {"key": "choice_factors",
             "label": "When choosing between options, which factors are important to you?",
             "type": "multi", "options": [
                 "Cost", "Convenience", "Quality", "Fun / Enjoyment", "Long-term benefits",
                 "Time required", "Social approval", "Personal growth", "Risk level"]},
            # These two drive the scoring weights and the criterion multipliers
            # for *every* category, so they are asked of everybody rather than
            # living behind the Motivation branch.
            {"key": "motivator", "label": "What keeps you motivated the most?",
             "type": "single", "options": [
                 "A reward", "A deadline", "Fear of falling behind", "A personal goal", "Others"]},
        ],
    },
    {
        "id": "support",
        "title": "Where you need help",
        "blurb": "Pick your areas - the engine weights them higher, and each one "
                 "adds a short set of questions built for that kind of decision.",
        "accent": "sage",
        "questions": [
            {"key": "support_areas",
             "label": "In which areas do you feel you need the most help making decisions?",
             "type": "multi", "options": [
                 "Studies", "Health", "Motivation", "Confidence", "Career", "Travel Planning",
                 "Managing Time", "Purchases", "Social Activity", "Entertainment", "Other"]},
            {"key": "support_other", "label": "If Other - please specify", "type": "text",
             "required": False, "depends_on": {"key": "support_areas", "contains": "Other"}},
        ],
    },

    # ------------------------------------------------------- branches
    {
        "id": "studies",
        "title": "Studies",
        "blurb": "So study tasks get ranked with your reality in mind.",
        "accent": "navy",
        "because": "Studies",
        "depends_on": {"key": "support_areas", "contains": "Studies"},
        "questions": [
            {"key": "hard_subject", "label": "Which subject do you face the most difficulty in?",
             "type": "single", "options": [
                 "Science / Computer Science", "Mathematics / Finance", "Social Studies",
                 "Languages", "Business Studies / Economics"]},
            {"key": "study_challenges", "label": "What are your biggest challenges when studying?",
             "type": "multi", "options": [
                 "Time management", "Distractions", "Motivation", "Stress", "Procrastination"]},
            {"key": "study_peak", "label": "When do you actually focus best?",
             "type": "single", "options": [
                 "Early morning", "Late morning", "Afternoon", "Evening", "Late night"]},
            {"key": "deadline_start",
             "label": "How close does a deadline have to be before you start?",
             "type": "single", "options": [
                 "A week or more out", "A few days before", "The day before",
                 "The night before", "Usually after it is due"]},
        ],
    },
    {
        "id": "health",
        "title": "Health & energy",
        "blurb": "Sleep and energy sit upstream of every other decision you make today.",
        "accent": "green",
        "because": "Health",
        "depends_on": {"key": "support_areas", "contains": "Health"},
        "questions": [
            {"key": "health_conscious", "label": "Are you health conscious?",
             "type": "scale", "low": "Not at all", "high": "Very much"},
            {"key": "exercise_freq", "label": "How often do you exercise?", "type": "single", "options": FREQ},
            {"key": "sleep_hours", "label": "How many hours do you sleep per day on average?",
             "type": "single", "options": ["Less than 4", "4-6", "6-8", "8+"]},
        ],
    },
    {
        "id": "drive",
        "title": "Motivation & confidence",
        "blurb": "What starts you, what stops you, and what gets you going again.",
        "accent": "amber",
        "because": "Motivation / Confidence",
        "depends_on": {"key": "support_areas",
                       "contains_any": ["Motivation", "Confidence"]},
        "questions": [
            {"key": "motivation_dip", "label": "What usually kills your motivation?",
             "type": "multi", "options": [
                 "The task feels too big", "Not sure where to start",
                 "Worried I'll do it badly", "No visible progress",
                 "Comparing myself to others", "Plain boredom"]},
            {"key": "self_trust",
             "label": "How much do you trust your own judgement on a close call?",
             "type": "scale", "low": "Not at all", "high": "Completely"},
        ],
    },
    {
        "id": "career",
        "title": "Career",
        "blurb": "The decisions with the longest shadow - and the least deadline pressure.",
        "accent": "violet",
        "because": "Career",
        "depends_on": {"key": "support_areas", "contains": "Career"},
        "questions": [
            {"key": "career_priority", "label": "In career choices, what matters most for you?",
             "type": "single", "options": ["Salary", "Passion", "Stability", "Growth & learning"]},
            {"key": "career_approach", "label": "How do you approach career decisions?",
             "type": "single", "options": [
                 "Plan far ahead", "Take opportunities as they come",
                 "Follow others' advice", "Avoid thinking about it"]},
            {"key": "career_clarity",
             "label": "How clear are you on what you want next?",
             "type": "scale", "low": "No idea at all", "high": "Completely clear"},
            {"key": "career_blocker", "label": "What makes career decisions hard for you?",
             "type": "multi", "options": [
                 "Too many options", "Not enough information", "Family expectations",
                 "Fear of choosing wrong", "Money pressure", "No one to ask"]},
        ],
    },
    {
        "id": "travel",
        "title": "Travel planning",
        "blurb": "Trips are one big decision wearing a lot of small ones.",
        "accent": "blue",
        "because": "Travel Planning",
        "depends_on": {"key": "support_areas", "contains": "Travel Planning"},
        "questions": [
            {"key": "travel_challenge", "label": "Which part of travel planning is hardest for you?",
             "type": "single", "options": [
                 "Choosing a destination", "Planning an itinerary",
                 "Choosing accommodation and transportation"]},
            {"key": "travel_protect",
             "label": "On a trip, what do you protect first when something has to give?",
             "type": "single", "options": [
                 "The budget", "Comfort", "The experiences", "Flexibility to change plans"]},
            {"key": "travel_lead_time", "label": "How far ahead do you usually plan a trip?",
             "type": "single", "options": [
                 "The same week", "2-4 weeks", "1-3 months", "More than 3 months"]},
        ],
    },
    {
        "id": "money",
        "title": "Purchases & money",
        "blurb": "What you weigh when you're about to spend.",
        "accent": "mint",
        "because": "Purchases",
        "depends_on": {"key": "support_areas", "contains": "Purchases"},
        "questions": [
            {"key": "purchase_influence",
             "label": "When making a purchase, what influences your decision the most?",
             "type": "single", "options": ["Price", "Quality", "Brand", "Reviews and ratings", "Discounts"]},
            {"key": "hard_purchases", "label": "Which purchase-related decisions do you find most difficult?",
             "type": "multi", "options": [
                 "Clothes and fashion", "Electronics and gadgets", "Food and dining",
                 "Beauty and personal care", "Books or study materials", "Subscriptions", "Other"]},
            {"key": "purchase_regret",
             "label": "How often do you regret a purchase afterwards?",
             "type": "single", "options": FREQ},
        ],
    },
    {
        "id": "time",
        "title": "Managing time",
        "blurb": "How far ahead you see, and how full you let the day get.",
        "accent": "coral",
        "because": "Managing Time",
        "depends_on": {"key": "support_areas", "contains": "Managing Time"},
        "questions": [
            {"key": "balance_life", "label": "Can you balance studies/work and personal life?",
             "type": "scale", "low": "Not at all", "high": "Perfectly"},
            {"key": "plan_horizon", "label": "How far ahead do you plan your day?",
             "type": "single", "options": [
                 "I don't plan", "The morning of", "The night before", "A week ahead"]},
            {"key": "overcommit",
             "label": "How often do you say yes to more than you can actually fit?",
             "type": "single", "options": FREQ},
            {"key": "time_leak", "label": "Where does your time actually disappear?",
             "type": "multi", "options": [
                 "Phone and social media", "Streaming or gaming", "Commuting",
                 "Other people's requests", "Deciding what to do", "Tiredness"]},
        ],
    },
    {
        "id": "social",
        "title": "Social life",
        "blurb": "The decisions other people are in the room for.",
        "accent": "rose",
        "because": "Social Activity / Entertainment",
        "depends_on": {"key": "support_areas",
                       "contains_any": ["Social Activity", "Entertainment"]},
        "questions": [
            {"key": "social_challenge", "label": "What are your biggest challenges when making social decisions?",
             "type": "multi", "options": [
                 "Fear of missing out (FOMO)", "Peer pressure", "Timings",
                 "Transportation", "Social anxiety", "Unsure what I'll enjoy"]},
            {"key": "social_recharge", "label": "After a heavy week, what actually recharges you?",
             "type": "single", "options": [
                 "A big night out", "A few close friends", "Time on my own", "A mix of both"]},
            {"key": "social_guilt",
             "label": "How often do you go out when you'd honestly rather rest?",
             "type": "single", "options": FREQ},
        ],
    },
]

ALL_QUESTIONS: dict[str, dict[str, Any]] = {
    q["key"]: q for section in SURVEY_SPEC for q in section["questions"]
}

# Questions that used to be in the survey. Answers saved under these keys are
# still sitting in older profiles (and come straight back through the wizard
# when a student retakes it), so a submission that carries them is dropped
# silently rather than rejected as "unknown fields". Nothing downstream reads
# them any more.
RETIRED_KEYS: frozenset[str] = frozenset({
    "stuck_first",       # removed from the current survey
    "decision_quality",  # removed from the current survey
    "regret",             # removed from the current survey
    "when_unsure",        # removed from the current survey
    "study_session",      # removed from the current survey
    "energy_dip",         # removed from the current survey
    "health_blocker",     # removed from the current survey
    "unmotivated_freq",   # removed from the current survey
    "restart_effort",     # removed from the current survey
    "budget_clarity",     # removed from the current survey
    "ask_friends_freq",   # removed from the current survey
    "pending_tasks",      # roughly how many pending tasks per day
    "decide_time",        # how long to decide what to start
    "decision_style",     # decision-making style
    "accountability",     # follow through more when someone knows the plan
    "purchase_time",      # time spent deciding before a purchase
    "decide_first_task",  # how good at deciding what to do first
})

# question key -> the section that owns it, so validation can apply the
# section's branch condition as well as the question's own.
QUESTION_SECTION: dict[str, dict[str, Any]] = {
    q["key"]: section for section in SURVEY_SPEC for q in section["questions"]
}


def _match_option(value: Any, options: list[str]) -> Any:
    """Match an answer to its option, tolerating the old dash characters.

    Ranges like "20-45 minutes" used to be spelled with an en dash. Answers
    saved back then still hold that spelling, so compare with every kind of
    dash flattened and hand back the current spelling when it lines up.
    """
    if value in options:
        return value

    def flatten(text: Any) -> Any:
        if not isinstance(text, str):
            return text
        for dash in ("–", "—", "−"):
            text = text.replace(dash, "-")
        return text

    flat = flatten(value)
    for option in options:
        if flatten(option) == flat:
            return option
    return None


def condition_met(condition: dict[str, Any] | None, answers: dict[str, Any]) -> bool:
    """Is a `depends_on` satisfied by these answers?

    Works for section-level and question-level conditions, and for both the
    single-value (`contains`) and any-of (`contains_any`) forms.
    """
    if not condition:
        return True
    value = answers.get(condition["key"]) or []
    if not isinstance(value, list):
        value = [value]
    if "contains" in condition:
        return condition["contains"] in value
    return any(option in value for option in condition.get("contains_any") or [])


def section_visible(section: dict[str, Any], answers: dict[str, Any]) -> bool:
    return condition_met(section.get("depends_on"), answers)


def question_visible(question: dict[str, Any], answers: dict[str, Any]) -> bool:
    """A question counts only when its section *and* it are both unlocked."""
    section = QUESTION_SECTION.get(question["key"])
    if section is not None and not section_visible(section, answers):
        return False
    return condition_met(question.get("depends_on"), answers)


def validate_answers(answers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a submission against the spec.

    Questions in a branch the student did not unlock are skipped entirely -
    neither required nor kept, so deselecting a support area drops the answers
    that only existed because of it.

    Returns (cleaned answers, list of error messages).
    """
    cleaned: dict[str, Any] = {}
    errors: list[str] = []

    for key, question in ALL_QUESTIONS.items():
        value = answers.get(key)
        qtype = question["type"]
        required = question.get("required", True)

        # Branch gating: the section's condition, then the question's own.
        if not question_visible(question, answers):
            continue

        if value in (None, "", []):
            if required and qtype != "text":
                errors.append(f"'{question['label']}' is unanswered")
            elif qtype == "text" and value:
                pass
            continue

        if qtype == "single":
            matched = _match_option(value, question["options"])
            if matched is None:
                errors.append(f"Invalid choice for '{key}'")
            else:
                cleaned[key] = matched
        elif qtype == "multi":
            # A question promoted from single to multi (social_challenge) has
            # older answers stored as one string; treat that as a one-item list.
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                errors.append(f"Invalid choices for '{key}'")
            else:
                matched_all = [_match_option(v, question["options"]) for v in value]
                if any(m is None for m in matched_all):
                    errors.append(f"Invalid choices for '{key}'")
                else:
                    cleaned[key] = matched_all
        elif qtype == "scale":
            if not isinstance(value, int) or not 1 <= value <= 5:
                errors.append(f"'{key}' must be an integer 1-5")
            else:
                cleaned[key] = value
        elif qtype == "number":
            if not isinstance(value, int) or not question["min"] <= value <= question["max"]:
                errors.append(f"'{key}' must be between {question['min']} and {question['max']}")
            else:
                cleaned[key] = value
        elif qtype == "text":
            text = str(value).strip()[:200]
            if text:
                cleaned[key] = text

    unknown = set(answers) - set(ALL_QUESTIONS) - RETIRED_KEYS
    if unknown:
        errors.append(f"Unknown fields: {', '.join(sorted(unknown))}")
    return cleaned, errors
