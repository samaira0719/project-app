"""Stage 2 automation: prompt + response schema for AVEX rating every option."""

from __future__ import annotations

from .profile import profile_lines, student_line

RATER_SYSTEM_INSTRUCTION = (
    "You are AVEX, the rating engine inside 'Decide Well', an app that helps students "
    "decide what to do first. The student has listed their options and the criteria "
    "that matter. Your job is to score EVERY option on EVERY criterion from 1 to 5 "
    "(1 = very weak on this criterion, 5 = very strong), so the student never has to "
    "fill the rating grid themselves.\n"
    "How to rate:\n"
    "- Use general real-world knowledge about the options (typical difficulty, cost, "
    "time, quality, how exam-heavy a subject usually is, etc.).\n"
    "- Use the student's decision-making survey profile for anything subjective: "
    "their motivator, what they value, how they cope with many tasks, what they "
    "struggle with, and where they want support.\n"
    "- Follow each criterion's hint about what a 5 means.\n"
    "- Spread the scores. Do not give everything a 3; genuine differences between "
    "options are the whole point.\n"
    "- When you have no hard facts (you cannot know the student's exact exam "
    "timetable), do NOT default to a flat middle score: infer from what is typical "
    "for these specific options and from anything the student wrote in their notes. "
    "Say so briefly in 'why'.\n"
    "- No two options may end up with an identical set of ratings. If they look tied, "
    "break the tie on the strongest real difference between them.\n"
    "- 'why' must be one short clause (max 12 words) justifying that number. "
    "Write it the way a friend would say it out loud - 'exam's closest', 'costs "
    "the least effort', 'you said you hate group work'. Plain and human, no "
    "report-speak, no 'this option demonstrates'.\n"
    "- Plain keyboard characters only in 'why': hyphens rather than em dashes, "
    "straight quotes rather than curly ones.\n"
    "Return JSON only, matching the requested schema exactly, covering every option "
    "and every criterion with no omissions."
)

RATING_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "option": {"type": "string"},
                    "ratings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "criterion": {"type": "string"},
                                "rating": {"type": "integer"},
                                "why": {"type": "string"},
                            },
                            "required": ["criterion", "rating", "why"],
                        },
                    },
                },
                "required": ["option", "ratings"],
            },
        }
    },
    "required": ["options"],
}


def build_rating_prompt(
    task, options: list[str], criteria: list[dict], survey: dict | None,
    context: str | None = None, name: str | None = None,
) -> str:
    lines: list[str] = student_line(name)
    if survey:
        lines.extend(profile_lines(survey))
        lines.append("")
    lines.append(
        f"Decision: '{task.title}' (category {task.category}, "
        f"due {task.due_date:%Y-%m-%d %H:%M})."
    )
    lines.append("Options to rate:")
    for option in options:
        lines.append(f"- {option}")
    lines.append("Criteria (with what a 5 means, and how much the student says it counts):")
    for criterion in criteria:
        hint = criterion.get("hint") or "5 = strongly in favour"
        lines.append(
            f"- {criterion['name']} [{criterion.get('tag', 'other')}] - {hint} "
            f"(importance to the student: {criterion['weight']}/5)"
        )
    if context:
        lines.append(f"\nThe student's own notes about this decision: {context}")
    lines.append(
        "\nRate every option on every criterion. Use the exact option and criterion "
        "names given above."
    )
    return "\n".join(lines)
