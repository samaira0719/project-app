"""AI prediction of decision criteria and their weights.

Given a student's survey profile plus a task they are trying to decide, this
module asks an LLM to propose 3-6 criteria and an importance weight (1-5) for
each, with a stated reason grounded in the survey.

The prediction is always returned as `predicted_*` data. The user's final,
possibly edited weights are stored separately by the caller so the two can be
compared later (see `PredictedCriteria.to_record`).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


PROMPT_VERSION = "weights-v2"

# Gemini 3.6 Flash: balanced quality, medium thinking by default.
# Swap to "gemini-3.5-flash-lite" for ~5x cheaper, faster, slightly weaker reasoning.
DEFAULT_MODEL = "gemini-3.6-flash"

# Output cap. Gemini counts thinking tokens toward output, so leave headroom
# above the size of the JSON itself or the response can truncate mid-object.
MAX_OUTPUT_TOKENS = 4096

MIN_CRITERIA = 3
MAX_CRITERIA = 6
NEUTRAL_WEIGHT = 3

# Values that appear in the survey file but carry no meaning.
_PLACEHOLDERS = {"sample", "answer", "returning", "standalone", "test", "n/a", "na", "-"}


# --------------------------------------------------------------------------
# Output schema
# --------------------------------------------------------------------------

class Criterion(BaseModel):
    """One criterion the user should judge their options against."""

    name: str = Field(description="Short criterion name, at most 4 words, e.g. 'Cost'.")
    weight: int = Field(
        ge=1, le=5,
        description="How important this criterion is to THIS person for THIS task. 5 = most important.",
    )
    reason: str = Field(
        description=(
            "One sentence justifying the weight. Cite the specific survey signal used, "
            "or say plainly that the profile gives no signal and the weight is neutral."
        )
    )


class PredictedCriteria(BaseModel):
    """The full prediction for one task."""

    task_type: str = Field(
        description="Short label for the kind of decision, e.g. 'purchase', 'study planning', 'career'."
    )
    criteria: list[Criterion] = Field(
        description=f"Between {MIN_CRITERIA} and {MAX_CRITERIA} criteria, ordered most to least important."
    )

    def to_record(self, *, model: str, profile_used: str) -> dict[str, Any]:
        """Serialise the prediction for storage next to the user's final weights."""
        return {
            "predicted_at": datetime.now(timezone.utc).isoformat(),
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "profile_used": profile_used,
            "task_type": self.task_type,
            "predicted_criteria": [c.model_dump() for c in self.criteria],
        }


# --------------------------------------------------------------------------
# Survey profile
# --------------------------------------------------------------------------

# Survey key -> how that answer should read in the profile. {} is the answer.
_PROFILE_LINES: dict[str, str] = {
    "age": "Age {}.",
    "pending_tasks": "Handles about {} pending tasks per day.",
    "stuck_first": "{} feels stuck deciding what to do first.",
    "decision_quality": "Rates the quality of their own decisions as '{}'.",
    "delay_start": "{} delays starting something because they cannot decide.",
    "regret": "{} wishes afterwards that they had done a different task first.",
    "confidence_after": "Confidence after deciding: {}/5.",
    "many_tasks": "Faced with many tasks, tends to pick: {}.",
    "decide_time": "Typically takes {} to decide what to start.",
    "decision_style": "Decision style: {}.",
    "hard_subject": "Finds {} the hardest subject.",
    "health_conscious": "Health consciousness: {}/5.",
    "exercise_freq": "{} exercises.",
    "sleep_hours": "Sleeps {} hours per day.",
    "unmotivated_freq": "{} feels unmotivated.",
    "motivator": "Most motivated by: {}.",
    "when_unsure": "When unsure, tends to: {}.",
    "career_priority": "In a career, values {} most.",
    "career_approach": "Approaches career decisions by: {}.",
    "decide_first_task": "Self-rated skill at choosing the first task: {}/5.",
    "balance_life": "Self-rated work/life balance: {}/5.",
    "purchase_time": "Spends {} deciding before a purchase.",
    "purchase_influence": "Purchases are influenced most by {}.",
    "social_challenge": "Biggest social decision challenge: {}.",
    "ask_friends_freq": "{} asks friends before social decisions.",
    "choice_factors": "Says these factors matter when choosing: {}.",
    "support_areas": "Wants the most help with: {}.",
    "study_challenges": "Biggest study challenges: {}.",
    "hard_purchases": "Hardest purchase decisions: {}.",
}

# Answers here are the strongest signal for weighting, so lead with them.
_PRIORITY_KEYS = ("choice_factors", "purchase_influence", "career_priority", "motivator", "support_areas")


def _is_meaningful(value: Any) -> bool:
    """Reject blanks, placeholders, and the one-letter typos in the survey data."""
    if isinstance(value, list):
        return any(_is_meaningful(item) for item in value)
    if value is None:
        return False
    text = str(value).strip()
    if not text or text.lower() in _PLACEHOLDERS:
        return False
    # Single characters are meaningful only as a 1-5 rating.
    if len(text) == 1:
        return text.isdigit()
    return True


def summarize_profile(survey: dict[str, Any] | None) -> str:
    """Turn one raw survey response into readable prose for the prompt.

    Blank, placeholder, and junk answers are dropped, so a half-finished survey
    produces a short profile rather than a misleading one.
    """
    if not survey:
        return ""

    def render(key: str) -> str | None:
        if key not in _PROFILE_LINES:
            return None
        value = survey.get(key)
        if not _is_meaningful(value):
            return None
        if isinstance(value, list):
            usable = [str(v).strip() for v in value if _is_meaningful(v)]
            if not usable:
                return None
            value = ", ".join(usable)
        return _PROFILE_LINES[key].format(str(value).strip())

    ordered_keys = list(_PRIORITY_KEYS) + [k for k in _PROFILE_LINES if k not in _PRIORITY_KEYS]
    lines = [line for line in (render(key) for key in ordered_keys) if line]
    return "\n".join("- " + line for line in lines)


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

_
_NO_PROFILE = (
    "(No survey answers available for this student. Weight every criterion by what "
    "the task itself demands and say in each reason that no profile was available.)"
)

_NO_OPTIONS = "(No options listed yet - propose criteria for the task in general.)"


def build_prompt() -> ChatPromptTemplate:
    """The prompt template, partially filled with the fixed rule values."""
    return ChatPromptTemplate.from_messages(
        [("system", _SYSTEM_PROMPT), ("human", _HUMAN_PROMPT)]
    ).partial(
        min_criteria=str(MIN_CRITERIA),
        max_criteria=str(MAX_CRITERIA),
        neutral_weight=str(NEUTRAL_WEIGHT),
    )


def build_model(model: str = DEFAULT_MODEL, **kwargs: Any) -> BaseChatModel:
    """A Gemini chat model configured for structured output.

    Note on determinism: Gemini 3.x deprecated `temperature`, `top_p`, and
    `top_k` - the API ignores them now and will reject them in a future model
    generation. So this deliberately does NOT set them. langchain-google-genai
    only drops `temperature` automatically when it was never assigned, so
    passing `temperature=0` here would put a deprecated parameter back on the
    wire. Determinism instead comes from the explicit rules in the system
    prompt, which is what Google recommends in its place.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Gemini API key found. Set GOOGLE_API_KEY (or GEMINI_API_KEY) "
            "before predicting weights. Get one at https://aistudio.google.com/apikey"
        )
    kwargs.pop("temperature", None)  # see docstring
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        max_retries=3,
        **kwargs,
    )


def build_chain(llm: BaseChatModel | None = None, model: str = DEFAULT_MODEL):
    """Prompt -> model -> validated PredictedCriteria."""
    llm = llm or build_model(model)
    # Gemini supports native JSON-schema constrained decoding, which is more
    # reliable here than emulating structure through function calling.
    return build_prompt() | llm.with_structured_output(
        PredictedCriteria, method="json_schema"
    )


# --------------------------------------------------------------------------
# Post-processing
# --------------------------------------------------------------------------

def _clean(prediction: PredictedCriteria) -> PredictedCriteria:
    """Enforce the count, weight range, and uniqueness the prompt asked for.

    The model is generally well behaved, but this runs downstream of a network
    call and the frontend should never receive a malformed weight set.
    """
    seen: set[str] = set()
    kept: list[Criterion] = []
    for criterion in prediction.criteria:
        name = " ".join(criterion.name.split()).strip(" .:-")
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        kept.append(
            Criterion(
                name=name,
                weight=max(1, min(5, int(criterion.weight))),
                reason=criterion.reason.strip(),
            )
        )

    kept.sort(key=lambda c: c.weight, reverse=True)
    kept = kept[:MAX_CRITERIA]
    if not kept:
        raise ValueError("The model returned no usable criteria.")
    return PredictedCriteria(task_type=prediction.task_type.strip(), criteria=kept)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def predict_weights(
    task: str,
    options: Sequence[str] | None = None,
    survey: dict[str, Any] | None = None,
    *,
    llm: BaseChatModel | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[PredictedCriteria, dict[str, Any]]:
    """Predict criteria and weights for one task.

    Returns the prediction and a record dict ready to store alongside the
    user's final weights.

    `llm` is injectable so tests can run without a network call.
    """
    if not task or not task.strip():
        raise ValueError("A task description is required.")

    profile = summarize_profile(survey)
    chain = build_chain(llm=llm, model=model)
    prediction = chain.invoke(
        {
            "profile": profile or _NO_PROFILE,
            "task": task.strip(),
            "options": _format_options(options),
        }
    )
    cleaned = _clean(prediction)
    return cleaned, cleaned.to_record(
        model=model, profile_used=profile or "(none)"
    )


def _format_options(options: Iterable[str] | None) -> str:
    usable = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not usable:
        return _NO_OPTIONS
    return "\n".join(f"{i}. {name}" for i, name in enumerate(usable, start=1))


if __name__ == "__main__":
    import json

    with open("survey_responses.json", encoding="utf-8") as fh:
        latest = json.load(fh)[-1]

    result, record = predict_weights(
        task="Which laptop should I buy for school?",
        options=["Budget Chromebook", "Mid-range Windows laptop", "MacBook Air"],
        survey=latest,
    )
    print(f"Task type: {result.task_type}\n")
    for item in result.criteria:
        print(f"{item.name} (weight {item.weight}) - {item.reason}")
