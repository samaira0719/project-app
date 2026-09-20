"""User Decision-Making Survey - spec-driven; one response per user."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SurveyResponse, User
from ..security import get_current_user
from ..survey_spec import SURVEY_SPEC, validate_answers

router = APIRouter(prefix="/api/survey", tags=["survey"])


@router.get("/spec")
def get_spec() -> list[dict[str, Any]]:
    """The survey definition the frontend renders the wizard from."""
    return SURVEY_SPEC


@router.get("")
def get_survey(user: User = Depends(get_current_user)) -> dict[str, Any]:
    if user.survey is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No survey submitted yet")
    return {"answers": user.survey.answers, "updated_at": user.survey.updated_at}


@router.put("")
def upsert_survey(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    answers = body.get("answers", body)
    if not isinstance(answers, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "answers must be an object")

    cleaned, errors = validate_answers(answers)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "; ".join(errors[:5]))

    if user.survey is None:
        survey = SurveyResponse(user_id=user.id, answers=cleaned)
        db.add(survey)
    else:
        survey = user.survey
        survey.answers = cleaned
    db.commit()
    db.refresh(survey)
    return {"answers": survey.answers, "updated_at": survey.updated_at}
