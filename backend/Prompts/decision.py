"""Prompt for explaining a completed option-level decision."""

from __future__ import annotations

from .profile import profile_lines, student_line


def build_decision_prompt(
    task, payload: dict, result: dict, survey: dict | None,
    name: str | None = None,
) -> str:
    lines: list[str] = student_line(name)
    if survey:
        lines.extend(profile_lines(survey))
    lines.append(
        f"\nThe student opened their task '{task.title}' ({task.category}) and ran a "
        "criteria-based decision between these options:"
    )
    for entry in result["scores"]:
        option = entry["option"]
        ratings = payload["ratings"].get(option, {})
        rating_text = ", ".join(f"{name}: {value}/5" for name, value in ratings.items())
        lines.append(f"- {option} - final score {entry['score']:.0f}% ({rating_text})")
    lines.append(
        "Criteria weights (after adapting to the student's survey): "
        + ", ".join(
            f"{c['name']} = {c['effective_weight']}" for c in result["criteria"]
        )
    )
    if result["personalization_notes"]:
        lines.append("Personalization applied: " + "; ".join(result["personalization_notes"]))
    lines.append(
        f"The engine recommends **{result['best']}** (clarity {result['clarity']:.0%}). "
        "Explain why this is the right call for them, mention the runner-up "
        "briefly, and end with one line on how to start."
    )
    return "\n".join(lines)
