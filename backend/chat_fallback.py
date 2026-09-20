"""Deterministic answers for the assistant bubble.

Same contract as every other AVEX surface in this app: when the model is
missing or unreachable, the feature still answers rather than dead-ending.
This is a small intent matcher over the *same* data the prompt would have
seen, so the facts it states are the engine's facts, not a canned script.

Intents are ordered most-specific first and the first match wins.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _due_phrase(due: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = _aware(due) - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return "overdue"
    if hours < 24:
        return f"due in {max(1, round(hours))}h"
    return f"due in {round(hours / 24)}d"


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0]


HELP_MENU = (
    "I can answer two kinds of question.\n"
    "- Your work: what to start with, what's overdue, why a task ranks where "
    "it does, what you've decided.\n"
    "- The app: what a score means, how the matrix works, where a button is, "
    "what the survey changes."
)


def _top(ranked: list, clarity: float, name: str | None) -> str:
    if not ranked:
        return (
            "Nothing pending right now, so there's nothing to rank. Add a task "
            "on the Tasks tab and I'll tell you where it lands."
        )
    top = ranked[0]
    who = _first_name(name)
    opener = f"{who}, start with" if who else "Start with"
    lines = [
        f"{opener} **{top.task.title}** at {top.score:.0f}%, "
        f"{_due_phrase(top.task.due_date)}."
    ]
    if len(ranked) > 1:
        second = ranked[1]
        gap = "a clear gap" if clarity >= 0.2 else "close behind"
        lines.append(
            f"- '{second.task.title}' is {gap} at {second.score:.0f}%."
        )
        if clarity < 0.2:
            lines.append(
                "- The top two are near enough that momentum is the tie-break. "
                "Pick one and give it 25 proper minutes."
            )
    return "\n".join(lines)


def _overdue(ranked: list) -> str:
    now = datetime.now(timezone.utc)
    late = [i for i in ranked if _aware(i.task.due_date) < now]
    if not late:
        return "Nothing is overdue. Every pending task still has time on it."
    head = f"{len(late)} task{'s' if len(late) > 1 else ''} past its due date:"
    rows = [
        f"- **{i.task.title}** [{i.task.category}], was due "
        f"{_aware(i.task.due_date):%d %b}, scoring {i.score:.0f}%."
        for i in late[:5]
    ]
    tail = (
        "\nOverdue pins urgency at its maximum, which is why these sit at the "
        "top of the list."
    )
    return head + "\n" + "\n".join(rows) + tail


def _due_soon(ranked: list) -> str:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)
    soon = [i for i in ranked if now <= _aware(i.task.due_date) <= horizon]
    if not soon:
        return "Nothing falls due in the next seven days."
    rows = [
        f"- **{i.task.title}**, {_due_phrase(i.task.due_date)} "
        f"({_aware(i.task.due_date):%a %d %b})."
        for i in soon[:6]
    ]
    return f"{len(soon)} due in the next week:\n" + "\n".join(rows)


def _summary(stats: dict, ranked: list, name: str | None) -> str:
    lines = [
        f"{stats['pending']} pending, {stats['completed']} done, "
        f"{stats['overdue']} overdue."
    ]
    if ranked:
        lines.append(
            f"Top of the list is **{ranked[0].task.title}** at "
            f"{ranked[0].score:.0f}%."
        )
    if stats["total"]:
        lines.append(f"You've finished {stats['completion_rate']:.0%} of everything you added.")
    return " ".join(lines)


def _why(ranked: list, message: str) -> str | None:
    """Explain one named task's rank, if the message names one."""
    lowered = message.lower()
    for rank, item in enumerate(ranked, start=1):
        title = item.task.title.lower()
        if len(title) > 3 and title in lowered:
            factors = item.factors
            return (
                f"**{item.task.title}** sits at #{rank} with {item.score:.0f}%.\n"
                f"- Urgency {factors['urgency']:.0%}, importance "
                f"{factors['importance']:.0%}, aging {factors['aging']:.0%}"
                + (f", effort {factors['effort']:.0%}" if factors.get("effort") else "")
                + ".\n"
                f"- {item.reason}"
            )
    return None


def _decisions(decisions: list) -> str:
    if not decisions:
        return (
            "You haven't made a per-task decision yet. Click any task title to "
            "open its workspace, list the options you're weighing up, and the "
            "engine scores them for you."
        )
    rows = [
        f"- On **{d.task.title if d.task else 'a task'}** you went with "
        f"{(d.result or {}).get('best')}."
        for d in decisions[:5]
    ]
    return "Decisions on record:\n" + "\n".join(rows)


SCORE_HELP = (
    "The percentage is the engine's priority score, S = 100*(wU*U + wC*C + "
    "wA*A) + 100*wE*E, capped at 100.\n"
    "- U is urgency, doubling every 48 hours as the deadline closes. Overdue "
    "pins it at 1, which is why overdue tasks sit at the top.\n"
    "- C is importance, a per-category weight your survey raises for the areas "
    "you asked for help with.\n"
    "- A is aging, so a task that has sat there for days climbs on its own.\n"
    "- E is effort criticality on Study tasks, estimated time over time left. "
    "It only ever adds, never subtracts."
)

MATRIX_HELP = (
    "The Matrix view splits the same urgency and importance the ranking uses "
    "into four boxes.\n"
    "- Do: urgent and important, handle it now.\n"
    "- Decide: important but not urgent yet, so give it a date.\n"
    "- Delegate: urgent but not important, batch it into one slot.\n"
    "- Delete: neither, and it can go.\n"
    "Drag a task to a different box to pin it there. That moves where it sits, "
    "not what it scores."
)

SURVEY_HELP = (
    "The Profile tab holds your decision-making survey, and it's what tunes "
    "the engine to you. Deadline-driven answers push urgency up, long-term "
    "goal answers push importance up, procrastination answers make stale tasks "
    "climb faster, and every support area you pick lifts that task category. "
    "Retaking it overwrites your old answers and re-tunes everything straight "
    "away."
)

ADD_HELP = (
    "Tasks tab, the form on the left: title, type and a due date from the "
    "calendar. Study tasks also want an estimated time, which feeds the effort "
    "factor. There's no edit-in-place yet, so to change a task, delete it with "
    "the bin icon and add it again."
)

COMPLETE_HELP = (
    "The tick on the right of a task marks it done, and the bin deletes it. "
    "Done tasks drop out of the ranking, and you can see them again by "
    "switching the filter from Pending to Done or All."
)

THEME_HELP = (
    "Top right of the header there are three icons: sun for light, moon for "
    "dark, and the monitor for matching whatever your device is set to. The "
    "choice sticks on this browser."
)

ACCOUNT_HELP = (
    "Your login lasts seven days, and everything in here - tasks, survey, "
    "decisions, history - is scoped to your account and private to you. Two "
    "things the app can't do yet: reset a password, and export your data. Both "
    "need whoever runs your instance."
)

EXPLAIN_HELP = (
    "Decide tab, the 'Explain with AVEX' button. It writes out why the order "
    "is what it is and saves the run to History so you can look back at how "
    "your list stood on a given day."
)

WHO_HELP = (
    "I'm AVEX. I can see your tasks, their scores and the factors behind them, "
    "plus everything about how this app works. Ask me either kind of question."
)


def answer(
    message: str,
    name: str | None,
    ranked: list,
    clarity: float,
    stats: dict,
    decisions: list,
    has_survey: bool,
) -> str:
    """Best deterministic answer for one chat turn."""
    text = (message or "").strip()
    if not text:
        return HELP_MENU
    low = text.lower()

    def hit(*words: str) -> bool:
        return any(re.search(rf"\b{w}\b", low) for w in words)

    # Greetings, before anything else can swallow them.
    if re.fullmatch(r"(hi|hey|hello|yo|sup|hiya)\b.*", low) and len(low) < 20:
        who = _first_name(name)
        opener = f"Hey {who}." if who else "Hey."
        if ranked:
            return (
                f"{opener} Top of your list is **{ranked[0].task.title}** at "
                f"{ranked[0].score:.0f}%. Ask me about any of it, or about the "
                "app itself."
            )
        return f"{opener} Nothing pending yet. {HELP_MENU}"

    if hit("who", "avex") and hit("you", "are", "avex"):
        return WHO_HELP

    # --- their data ---
    # A question that names one of their own tasks is the most specific thing
    # they can ask, so it is checked before the generic "what's first" rule -
    # otherwise a title like "Which Subject to study First" is misread as one.
    if hit("why", "rank", "ranked", "ranking", "score", "scoring") and ranked:
        named = _why(ranked, text)
        if named:
            return named

    if hit("overdue", "late", "missed", "past"):
        return _overdue(ranked)

    if (hit("first", "next", "start", "begin", "priority", "top")
            and not hit("score", "calculated", "computed", "work", "works")):
        return _top(ranked, clarity, name)

    if hit("due", "deadline", "deadlines", "week", "soon", "upcoming"):
        return _due_soon(ranked)

    if hit("why") and ranked:
        named = _why(ranked, text)
        if named:
            return named
        return SCORE_HELP

    if hit("decision", "decisions", "decided", "chose", "choice", "options"):
        return _decisions(decisions)

    if hit("summary", "summarise", "summarize", "overview", "status", "progress",
           "how many", "stats", "doing"):
        return _summary(stats, ranked, name)

    # --- the app ---
    if hit("score", "scores", "percentage", "percent", "rank", "ranked", "ranking",
           "formula", "math", "urgency", "aging", "importance"):
        return SCORE_HELP

    if hit("matrix", "quadrant", "quadrants", "eisenhower", "drag"):
        return MATRIX_HELP

    if hit("survey", "profile", "questionnaire", "personalise", "personalize"):
        return SURVEY_HELP if has_survey else (
            SURVEY_HELP + " You haven't filled it in yet, so everything is "
            "running on defaults right now."
        )

    if hit("add", "create", "new", "edit", "change", "estimate"):
        return ADD_HELP

    if hit("complete", "done", "finish", "delete", "remove", "tick", "bin"):
        return COMPLETE_HELP

    if hit("dark", "light", "theme", "mode", "colour", "color"):
        return THEME_HELP

    if hit("password", "account", "login", "log", "email", "privacy", "data",
           "export", "secure"):
        return ACCOUNT_HELP

    if hit("explain", "history", "insight", "insights"):
        return EXPLAIN_HELP

    if hit("help", "what can you", "can you"):
        return HELP_MENU

    # Nothing matched. Say so honestly, then be useful with what is certain.
    if ranked:
        return (
            "I'm not sure I follow. Try asking it another way, or ask me "
            f"what to start with - right now that's **{ranked[0].task.title}** "
            f"at {ranked[0].score:.0f}%."
        )
    return "I'm not sure I follow. Try asking it another way.\n\n" + HELP_MENU
