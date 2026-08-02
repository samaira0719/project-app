"""Run the survey and weighted-decision workflow."""

import decision
import survey


def main():
    decision.check_pending_feedback()
    survey.take_survey()
    result = decision.run_decision()

    print("\n===== FINAL SUMMARY =====")
    print("Survey responses collected:", len(survey.survey_db))
    print("Best decision:", result["best"])
    print("Quality score:", round(result["quality"] * 100), "%")
    print("User certainty:", round(result["certainty"] * 100), "%")
    print("Clarity of winner:", round(result["clarity"] * 100), "%")


if __name__ == "__main__":
    main()
