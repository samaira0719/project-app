"""Shared prompt fragment: the student's survey profile.

Every prompt in this package leads with these lines, so the model always sees
who it is talking about before it sees the task at hand.
"""

from __future__ import annotations


def student_line(name: str | None) -> list[str]:
    """The one line that lets the model address the student by name."""
    first = (name or "").strip().split(" ")[0]
    return [f"You're talking to {first}."] if first else []


def profile_lines(survey: dict) -> list[str]:
    """Compress the User Decision-Making Survey into prompt-ready lines."""
    pick = survey.get
    lines = ["Student profile from their decision-making survey:"]
    basics = []
    if pick("age"):
        basics.append(f"age {pick('age')}")
    if pick("pending_tasks"):
        basics.append(f"{pick('pending_tasks')} pending tasks on a normal day")
    if basics:
        lines.append("- " + ", ".join(basics))
    if pick("many_tasks"):
        lines.append(f"- with many tasks they usually: {pick('many_tasks').lower()}")
    if pick("decision_style"):
        lines.append(f"- decision style: {pick('decision_style').lower()}")
    if pick("motivator"):
        lines.append(f"- most motivated by: {pick('motivator').lower()}")
    struggles = [
        label for key, label in [
            ("stuck_first", "feeling stuck on what to do first"),
            ("delay_start", "delaying starts out of indecision"),
            ("regret", "regretting task order afterwards"),
        ] if pick(key) in ("Often", "Very Often")
    ]
    if struggles:
        lines.append("- struggles with: " + ", ".join(struggles))
    if pick("support_areas"):
        lines.append("- wants help deciding about: " + ", ".join(pick("support_areas")))
    if pick("choice_factors"):
        lines.append("- values when choosing: " + ", ".join(pick("choice_factors")))
    if pick("career_priority"):
        lines.append(f"- career priority: {pick('career_priority').lower()}")
    lines.extend(_branch_lines(survey))
    return lines


def _branch_lines(survey: dict) -> list[str]:
    """Detail from the areas the student asked for help with.

    These come from the branch sections of the survey, so they are only
    present for the areas this student actually picked. Each one is a fact the
    rater cannot guess - when they start, how long they last, what drains
    them - which is exactly what it needs to score options realistically.
    """
    pick = survey.get
    lines: list[str] = []

    # --- studies ---
    if pick("deadline_start"):
        lines.append(f"- typically starts work: {pick('deadline_start').lower()}")
    if pick("study_peak"):
        lines.append(f"- focuses best in the {pick('study_peak').lower()}")
    if pick("study_session"):
        lines.append(f"- can concentrate for {pick('study_session').lower()} before needing a break")

    # --- health / energy ---
    if pick("energy_dip") and pick("energy_dip") != "It doesn't really crash":
        lines.append(f"- energy crashes {pick('energy_dip').lower()}")
    if pick("health_blocker") and "Nothing really" not in (pick("health_blocker") or []):
        lines.append("- what blocks self-care: " + ", ".join(pick("health_blocker")).lower())

    # --- motivation ---
    if pick("motivation_dip"):
        lines.append("- loses motivation when: " + ", ".join(pick("motivation_dip")).lower())
    if pick("accountability") in ("Much more", "A bit more"):
        lines.append("- follows through more when someone else knows the plan")
    if (pick("restart_effort") or 0) >= 4:
        lines.append("- finds it very hard to restart once they have stopped")

    # --- time ---
    if pick("plan_horizon"):
        lines.append(f"- plans the day: {pick('plan_horizon').lower()}")
    if pick("overcommit") in ("Often", "Very Often"):
        lines.append("- often says yes to more than actually fits")
    if pick("time_leak"):
        lines.append("- time disappears into: " + ", ".join(pick("time_leak")).lower())

    # --- money ---
    if pick("purchase_regret") in ("Often", "Very Often"):
        lines.append("- often regrets purchases afterwards")
    if pick("budget_clarity") is not None and pick("budget_clarity") <= 2:
        lines.append("- does not have a clear picture of what they can afford")

    # --- career / travel / social ---
    if pick("career_clarity") is not None and pick("career_clarity") <= 2:
        lines.append("- is not yet clear on what they want next, career-wise")
    if pick("career_blocker"):
        lines.append("- career decisions held up by: " + ", ".join(pick("career_blocker")).lower())
    if pick("travel_protect"):
        lines.append(f"- on a trip, protects {pick('travel_protect').lower()} first")
    if pick("travel_lead_time"):
        lines.append(f"- plans trips {pick('travel_lead_time').lower()} ahead")
    if pick("social_recharge"):
        lines.append(f"- recharges with: {pick('social_recharge').lower()}")
    if pick("social_guilt") in ("Often", "Very Often"):
        lines.append("- often goes out when they would rather rest")

    return lines
