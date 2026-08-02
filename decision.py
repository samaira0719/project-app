"""Weighted decision helper and decision-history support."""

import json
import os
import time
from datetime import datetime


HISTORY_FILE = "decision_history.json"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as history_file:
        return json.load(history_file)


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as history_file:
        json.dump(history, history_file, indent=2)


def _get_rating(prompt):
    while True:
        try:
            rating = int(input(prompt))
            if 1 <= rating <= 5:
                return rating
        except ValueError:
            pass
        print("Please enter an integer from 1 to 5.")


def _get_positive_integer(prompt):
    while True:
        try:
            value = int(input(prompt))
            if value >= 1:
                return value
        except ValueError:
            pass
        print("Please enter a whole number of at least 1.")


def check_pending_feedback():
    """Ask about the most recent decision that has no satisfaction rating."""
    history = load_history()
    for decision in reversed(history):
        if decision.get("satisfaction") is None:
            prompt = (
                "Last time you chose '" + str(decision.get("best", "the selected option"))
                + "' — how satisfied were you with that decision? (1-5) "
            )
            decision["satisfaction"] = _get_rating(prompt)
            save_history(history)
            return decision
    return None


def run_decision():
    """Run one weighted decision and add it to the decision history."""
    start_time = time.time()
    criteria = {}
    for _ in range(_get_positive_integer("how many criteria matter? ")):
        name = input("name of the criteria: ").strip()
        criteria[name] = _get_rating("how important is this criteria (1-5)? ")

    options = {}
    for _ in range(_get_positive_integer("number of options? ")):
        option_name = input("enter your option: ").strip()
        total = 0
        for criterion, weight in criteria.items():
            total += _get_rating(
                "rate '" + option_name + "' on '" + criterion + "' (1-5): "
            ) * weight
        options[option_name] = total

    print("\nScores:")
    for option_name, score in options.items():
        print(option_name + ": " + str(score))

    best = max(options, key=options.get)
    best_possible = 5 * sum(criteria.values())
    quality = options[best] / best_possible if best_possible else 0
    certainty = _get_rating("how sure are you (1-5)? ") / 5
    scores = sorted(options.values(), reverse=True)
    runner_up = scores[1] if len(scores) > 1 else 0
    clarity = (scores[0] - runner_up) / scores[0] if scores[0] else 0
    dci = (quality + certainty + clarity) / 3

    print("\nBest option:", best)
    print("Quality score:", round(quality * 100), "%")
    print("Certainty:", round(certainty * 100), "%")
    print("Clarity:", round(clarity * 100), "%")

    result = {
        "criteria": criteria,
        "options": options,
        "best": best,
        "decision_seconds": time.time() - start_time,
        "quality": quality,
        "certainty": certainty,
        "clarity": clarity,
        "dci": dci,
    }
    history = load_history()
    history_entry = result.copy()
    history_entry.update({
        "id": max((entry.get("id", 0) for entry in history), default=0) + 1,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "satisfaction": None,
    })
    history.append(history_entry)
    save_history(history)
    return result


if __name__ == "__main__":
    run_decision()
