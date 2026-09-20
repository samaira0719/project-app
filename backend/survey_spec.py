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
choice_factors, decision_style, when_unsure, the procrastination scales...).
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
            {"key": "age", "label": "What is your age?", "type": "number", "min": 14, "max": 24},
            {"key": "gender", "label": "Gender", "type": "single",
             "options": ["Male", "Female", "Other", "Prefer not to say"]},
            {"key": "pending_tasks", "label": "On a normal day, roughly how many pending tasks do you have?",
             "type": "single", "options": ["1-2", "2-5", "5-7", "7+"]},
        ],
    },
    {
        "id": "scales",
        "title": "How you decide",
        "blurb": "Be honest - there are no wrong answers.",
        "accent": "sage",
        "questions": [
            {"key": "stuck_first", "label": "How often do you feel stuck deciding what to do first?",
             "type": "single", "options": FREQ},
            {"key": "decision_quality", "label": "How would you rate the quality of your decisions?",
             "type": "single", "options": ["Very Bad", "Bad", "Average", "Good", "Very Good"]},
            {"key": "delay_start",
             "label": "How often do you delay starting work because you cannot decide where to begin?",
             "type": "single", "options": FREQ},
            {"key": "regret",
             "label": "After finishing a task, how often do you wish you had done a different task first?",
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
            {"key": "decide_time", "label": "How long do you usually take to decide what to start?",
             "type": "single", "options": ["Under 1 min", "1-5 min", "5-15 min", "More"]},
            {"key": "choice_factors",
             "label": "When choosing between options, which factors are important to you?",
             "type": "multi", "options": [
                 "Cost", "Convenience", "Quality", "Fun / Enjoyment", "Long-term benefits",
                 "Time required", "Social approval", "Personal growth", "Risk level"]},
            {"key": "decision_style", "label": "Which best describes your decision-making style?",
             "type": "single", "options": [
                 "I decide quickly and move on", "I compare many options before deciding",
                 "I often postpone decisions", "I frequently ask others for advice"]},
            # These two drive the scoring weights and the criterion multipliers
            # for *every* category, so they are asked of everybody rather than
            # living behind the Motivation branch.
            {"key": "motivator", "label": "What keeps you motivated the most?",
             "type": "single", "options": [
                 "A reward", "A deadline", "Fear of falling behind", "A personal goal", "Others"]},
            {"key": "when_unsure", "label": "When you're unsure, you usually:",
             "type": "single", "options": [
                 "Decide anyway", "Ask someone", "Postpone it", "Keep over-researching"]},
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
            {"key": "study_session",
             "label": "How long can you study before you genuinely need a break?",
             "type": "single", "options": [
                 "Under 20 minutes", "20-45 minutes", "45-90 minutes", "Over 90 minutes"]},
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
            {"key": "energy_dip", "label": "When does your energy usually crash?",
             "type": "single", "options": [
                 "Mid-morning", "Right after lunch", "Early evening", "Late night",
                 "It doesn't really crash"]},
            {"key": "health_blocker",
             "label": "What actually gets in the way of looking after yourself?",
             "type": "multi", "options": [
                 "No time", "No motivation", "Cost", "Nowhere convenient to go",
                 "Nobody to do it with", "Nothing really"]},
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
            {"key": "unmotivated_freq", "label": "How often do you feel unmotivated?",
             "type": "single", "options": FREQ},
            {"key": "motivation_dip", "label": "What usually kills your motivation?",
             "type": "multi", "options": [
                 "The task feels too big", "Not sure where to start",
                 "Worried I'll do it badly", "No visible progress",
                 "Comparing myself to others", "Plain boredom"]},
            {"key": "restart_effort",
             "label": "Once you've stopped, how hard is it to start again?",
             "type": "scale", "low": "I pick it straight back up", "high": "Almost impossible"},
            {"key": "accountability",
             "label": "Do you follow through more when someone else knows your plan?",
             "type": "single", "options": [
                 "Much more", "A bit more", "No difference", "It makes it worse"]},
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
            {"key": "purchase_time",
             "label": "How much time do you usually spend deciding before making a purchase?",
             "type": "single", "options": [
                 "Less than 5 minutes", "5-30 minutes", "1-2 hours", "More than 2 hours"]},
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
            {"key": "budget_clarity",
             "label": "How clearly do you know what you can afford right now?",
             "type": "scale", "low": "No idea", "high": "To the rupee"},
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
            {"key": "decide_first_task", "label": "How good are you at deciding what task to do first?",
             "type": "scale", "low": "Very bad", "high": "Very good"},
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
            {"key": "social_challenge", "label": "What is your biggest challenge when making social decisions?",
             "type": "single", "options": [
                 "Fear of missing out (FOMO)", "Peer pressure", "Timings",
                 "Transportation", "Social anxiety", "Unsure what I'll enjoy"]},
            {"key": "ask_friends_freq",
             "label": "How often do you ask friends for advice before making social decisions?",
             "type": "single", "options": FREQ},
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

    unknown = set(answers) - set(ALL_QUESTIONS)
    if unknown:
        errors.append(f"Unknown fields: {', '.join(sorted(unknown))}")
    return cleaned, errors
