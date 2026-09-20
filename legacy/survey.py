"""Student decision-making survey with saved responses."""

import json
import os


SURVEY_FILE = "survey_responses.json"


questions = [
    ("age", "What is your age? (14-24)"),
    ("gender", "Gender? (Male/Female/Other)"),
    ("pending_tasks", "Pending tasks per day? (1-2 / 2-5 / 5-7 / 7+)"),
    ("stuck_first", "How often do you feel stuck deciding what to do first? (Never/Rarely/Sometimes/Often/Very Often)"),
    ("decision_quality", "Quality of your decisions? (Very Bad/Bad/Average/Good/Very Good)"),
    ("delay_start", "How often do you delay starting because you can't decide? (Never/Rarely/Sometimes/Often/Very Often)"),
    ("regret", "How often do you wish you'd done a different task first? (Never/Rarely/Sometimes/Often/Very Often)"),
    ("confidence_after", "Confidence after a decision? (1-5)"),
    ("many_tasks", "With many tasks you usually: (Easiest / Urgent / Whatever / Freeze)"),
    ("decide_time", "Time to decide what to start? (Under 1 min / 1-5 min / 5-15 min / More)"),
    ("decision_style", "Your decision style? (Quick / Compare / Postpone / Ask others)"),
    ("hard_subject", "Hardest subject? (Science/Maths/Social/Languages/Business)"),
    ("health_conscious", "Are you health conscious? (1-5)"),
    ("exercise_freq", "How often do you exercise? (Never/Rarely/Sometimes/Often/Very Often)"),
    ("sleep_hours", "Sleep hours per day? (<4 / 4-6 / 6-8 / 8+)"),
    ("unmotivated_freq", "How often do you feel unmotivated? (Never/Rarely/Sometimes/Often/Very Often)"),
    ("motivator", "What motivates you most? (Reward/Deadline/Fear/Goal/Other)"),
    ("when_unsure", "When unsure you usually: (Decide/Ask/Postpone/Over-research)"),
    ("career_priority", "In career, what matters most? (Salary/Passion/Stability/Growth)"),
    ("career_approach", "How do you approach career decisions? (Plan/Opportunities/Others/Avoid)"),
    ("decide_first_task", "How good at deciding what task to do first? (1-5)"),
    ("balance_life", "Can you balance work and personal life? (1-5)"),
    ("purchase_time", "Time spent deciding before a purchase? (<5min / 5-30min / 1-2hr / More)"),
    ("purchase_influence", "What influences purchases most? (Price/Quality/Brand/Reviews/Discounts)"),
    ("social_challenge", "Biggest social decision challenge? (FOMO/Peer pressure/Timings/Transport/Anxiety/Unsure)"),
    ("ask_friends_freq", "How often do you ask friends before social decisions? (Never/Rarely/Sometimes/Often/Very Often)"),
]

multi_questions = [
    ("choice_factors", "Important factors when choosing?",
     ["Cost", "Convenience", "Quality", "Fun", "Long-term", "Time", "Social approval", "Personal growth", "Risk"]),
    ("support_areas", "Areas you need most help with?",
     ["Studies", "Health", "Motivation", "Confidence", "Career", "Travel", "Time", "Purchases", "Social", "Entertainment"]),
    ("study_challenges", "Biggest study challenges?",
     ["Time management", "Distractions", "Motivation", "Stress", "Procrastination"]),
    ("hard_purchases", "Hardest purchase decisions?",
     ["Clothes", "Electronics", "Food", "Beauty", "Books", "Subscriptions", "Other"]),
]

def load_responses():
    """Return previously saved survey responses, if there are any."""
    if not os.path.exists(SURVEY_FILE):
        return []
    with open(SURVEY_FILE, "r", encoding="utf-8") as survey_file:
        return json.load(survey_file)


def save_responses(responses):
    """Save all survey responses in readable JSON."""
    with open(SURVEY_FILE, "w", encoding="utf-8") as survey_file:
        json.dump(responses, survey_file, indent=2)


survey_db = load_responses()

def ask_single(text):
    return input(text + "\n> ").strip()

def ask_multi(text, options):
    print(text)
    for i, opt in enumerate(options, start=1):
        print(str(i) + ". " + opt)
    raw = input("Pick numbers (comma separated): ").strip()
    picked = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            num = int(part)
            if 1 <= num <= len(options):
                picked.append(options[num - 1])
    return picked

def take_survey():
    answers = {}
    for key, text in questions:
        answers[key] = ask_single(text)
    for key, text, options in multi_questions:
        answers[key] = ask_multi(text, options)
    survey_db.append(answers)
    save_responses(survey_db)
    return answers

if __name__ == "__main__":
    take_survey()
    print(survey_db)
