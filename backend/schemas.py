"""Pydantic request/response schemas with category-conditional validation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

Category = Literal[
    "Study",
    "Purchases",
    "Travel",
    "Entertainment",
    "Personal",
    "Career",
    "Health",
    "Other",
]


# ---------- Auth ----------

class ConsentIn(BaseModel):
    """The consent block on the sign-up form (see privacy.py for the rules).

    `privacy` is the only mandatory box - it covers the processing the service
    cannot exist without. The other two are separate, optional and default to
    False, because a pre-ticked box is not consent and bundling an optional
    purpose into a mandatory one voids both.
    """

    privacy: bool = False
    personalization: bool = False
    ai_processing: bool = False
    age_confirmed: bool = False
    # Echoed back by the client from GET /api/privacy/notice, so the record
    # says which revision of the text was actually on screen.
    policy_version: str = Field(default="", max_length=32)

    @field_validator("privacy")
    @classmethod
    def must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "Please accept the privacy notice - we cannot create an "
                "account without it"
            )
        return value

    @field_validator("age_confirmed")
    @classmethod
    def must_confirm_age(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "Please confirm your age, or that a parent or guardian agrees"
            )
        return value


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    consent: ConsentIn


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    has_survey: bool = False
    # Current consent state, so the client can reflect it without a second
    # round trip and can prompt when the notice has been revised.
    consent: dict = {}

    model_config = {"from_attributes": True}


# ---------- Privacy & consent ----------

class ConsentUpdate(BaseModel):
    """Grant or withdraw an optional purpose. Omitted fields are left alone."""

    personalization: bool | None = None
    ai_processing: bool | None = None
    # Re-accepting after the notice has been revised.
    policy_version: str | None = Field(default=None, max_length=32)


class ConsentState(BaseModel):
    policy_version: str
    current_version: str
    stale: bool
    accepted_at: datetime | None
    personalization: bool
    ai_processing: bool
    age_confirmed: bool
    history: list[dict] = []


# ---------- Tasks ----------
# (The survey is spec-driven - see survey_spec.py; it needs no static schema.)

class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: Category
    due_date: datetime
    # Study only
    estimated_minutes: int | None = Field(default=None, ge=5, le=6000)
    # Travel only
    travel_from: str | None = Field(default=None, max_length=120)
    travel_to: str | None = Field(default=None, max_length=120)
    travel_date: datetime | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @model_validator(mode="after")
    def enforce_category_fields(self) -> "TaskIn":
        if self.category == "Study":
            if self.estimated_minutes is None:
                raise ValueError("Study tasks need an estimated time to complete")
        else:
            self.estimated_minutes = None
        # Travel needs nothing extra - every non-Study category is title + due date.
        self.travel_from = self.travel_to = self.travel_date = None
        return self


class FactorBreakdown(BaseModel):
    urgency: float
    importance: float
    effort: float
    aging: float
    weights: dict[str, float]


class TaskOut(BaseModel):
    id: int
    title: str
    category: str
    due_date: datetime
    estimated_minutes: int | None
    travel_from: str | None
    travel_to: str | None
    travel_date: datetime | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    score: float | None = None
    rank: int | None = None
    factors: FactorBreakdown | None = None
    reason: str | None = None
    decided_option: str | None = None
    # Eisenhower placement - do | schedule | delegate | eliminate
    quadrant: str | None = None
    urgent: bool | None = None
    important: bool | None = None
    # Where the engine put it, which differs from `quadrant` only when the
    # user has dragged the task somewhere else.
    computed_quadrant: str | None = None
    quadrant_overridden: bool = False

    model_config = {"from_attributes": True}


# ---------- Per-task decisions ----------

CriterionTag = Literal["urgency", "importance", "interest", "effort", "cost", "quality", "other"]


class CriterionIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    weight: int = Field(ge=1, le=5)
    tag: CriterionTag = "other"

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Criterion name cannot be empty")
        return value


class DecideRequest(BaseModel):
    options: list[str] = Field(min_length=2, max_length=8)
    criteria: list[CriterionIn] = Field(min_length=1, max_length=8)
    ratings: dict[str, dict[str, int]]
    # Seconds the user spent deliberating, timed by the browser. Optional so
    # an older client, or one whose timer was never trustworthy, simply
    # contributes no point to the tempo trend instead of a fabricated one.
    deliberation_seconds: float | None = Field(default=None, ge=0, le=86400)

    @field_validator("options")
    @classmethod
    def clean_options(cls, options: list[str]) -> list[str]:
        cleaned = [o.strip()[:80] for o in options if o.strip()]
        if len(cleaned) < 2:
            raise ValueError("Give at least two options to compare")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Options must be unique")
        return cleaned

    @model_validator(mode="after")
    def ratings_cover_everything(self) -> "DecideRequest":
        names = [c.name for c in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("Criteria names must be unique")
        for option in self.options:
            option_ratings = self.ratings.get(option)
            if not option_ratings:
                raise ValueError(f"'{option}' has no ratings yet")
            for name in names:
                rating = option_ratings.get(name)
                if not isinstance(rating, int) or not 1 <= rating <= 5:
                    raise ValueError(f"Rate '{option}' on '{name}' (1-5)")
        return self


class AutoDecideRequest(BaseModel):
    """Stage 2 done by AI: the user sends only options + criteria."""

    options: list[str] = Field(min_length=2, max_length=8)
    criteria: list[CriterionIn] = Field(min_length=1, max_length=8)
    # Optional free text the rater should know (e.g. "Physics test on Friday").
    context: str | None = Field(default=None, max_length=400)
    # See DecideRequest - the setup time counts as deliberation even when
    # AVEX does the rating, because framing the options is the thinking part.
    deliberation_seconds: float | None = Field(default=None, ge=0, le=86400)

    @field_validator("options")
    @classmethod
    def clean_options(cls, options: list[str]) -> list[str]:
        cleaned = [o.strip()[:80] for o in options if o.strip()]
        if len(cleaned) < 2:
            raise ValueError("Give at least two options to compare")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Options must be unique")
        return cleaned

    @field_validator("context")
    @classmethod
    def clean_context(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @model_validator(mode="after")
    def unique_criteria(self) -> "AutoDecideRequest":
        names = [c.name for c in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("Criteria names must be unique")
        return self


class DecisionOut(BaseModel):
    task_id: int
    best: str
    scores: list[dict]
    clarity: float
    criteria: list[dict]
    drivers: list[str]
    personalization_notes: list[str]
    options: list[str]
    ratings: dict[str, dict[str, int]]
    insight: str | None
    insight_source: str
    updated_at: datetime
    # "gemini" = AVEX rated the options, "engine" = neutral fallback, "manual" = the user did.
    # The wire value stays "gemini" so rows written before the rename still load.
    ratings_source: str = "manual"
    # option -> criterion -> one-line justification for the AI's rating.
    rating_reasons: dict[str, dict[str, str]] = {}
    # What the user told the rater, so re-opening a decision keeps their notes.
    context: str | None = None
    # Full re-derivation of the result (see decision_engine.build_audit).
    audit: dict | None = None
    # Behavioural-science correlations for this specific decision.
    behavioral: list[dict] = []
    # Confidence + predicted satisfaction, each with an itemised audit of the
    # points that made it up (see feedback.assess).
    assessment: dict | None = None
    # The user's own verdict once they have given one.
    feedback: dict | None = None


Quadrant = Literal["do", "schedule", "delegate", "eliminate"]


class QuadrantUpdate(BaseModel):
    """Drag-and-drop placement. `None` hands the task back to the engine."""

    quadrant: Quadrant | None = None


class QuadrantOut(BaseModel):
    """One cell of the Eisenhower matrix, with the science behind the cell."""

    key: str          # do | schedule | delegate | eliminate
    title: str
    rule: str
    science: str
    source: str
    task_ids: list[int]


class PrioritizedResponse(BaseModel):
    tasks: list[TaskOut]
    clarity: float
    top_task_id: int | None
    matrix: list[QuadrantOut] = []
    matrix_notes: list[dict] = []


# ---------- Insights ----------

class InsightResponse(BaseModel):
    insight: str
    source: Literal["gemini", "engine"]
    generated_at: datetime


class HistoryEntry(BaseModel):
    id: int
    created_at: datetime
    ranking: list
    clarity: float
    insight: str | None
    insight_source: str

    model_config = {"from_attributes": True}


class BehavioralResponse(BaseModel):
    """How this student's profile lines up with the research literature."""

    correlations: list[dict]
    parameters: list[dict]   # engine parameter, base value, personalised value


class TempoResponse(BaseModel):
    """Decision tempo: speed trend + volume trend (see decision_tempo.py).

    `speed` and `confidence` are None until there is enough history, so the
    client must branch on `status` rather than assume the scores are there.
    """

    status: Literal["collecting", "ready"]
    granularity: Literal["day", "week"]
    message: str
    series: list[dict]
    totals: dict
    power_law: dict | None = None
    speed: dict | None = None
    confidence: dict | None = None
    overall: float | None = None


class StatsResponse(BaseModel):
    total_tasks: int
    pending: int
    completed: int
    completion_rate: float
    by_category: dict[str, int]
    avg_score: float
    overdue: int
    due_this_week: int


# ---------- Assistant chat ----------

class ChatTurn(BaseModel):
    """One prior message in the conversation, replayed by the client."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Oldest first. Capped so a long session cannot grow the prompt without
    # bound - the account snapshot is rebuilt fresh on every turn anyway.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class ChatResponse(BaseModel):
    reply: str
    source: str            # "gemini" (= AVEX) | "engine"
    generated_at: datetime


# ---------- Feedback loop ----------

Outcome = Literal["followed", "modified", "rejected"]


class DecisionFeedbackIn(BaseModel):
    """What actually happened after a recommendation was given."""

    outcome: Outcome
    satisfaction: int = Field(ge=1, le=5)
    chosen_option: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class DecisionFeedbackOut(BaseModel):
    outcome: str
    satisfaction: int
    satisfaction_pct: float
    chosen_option: str | None
    note: str | None
    reward: float
    predicted_satisfaction: float | None
    predicted_confidence: float | None
    calibration_error_pts: float | None = None
    learned: bool = False
    # Which multipliers this single piece of feedback actually moved.
    changes: list[dict] = []
    created_at: datetime


class LearningEvent(BaseModel):
    id: int
    kind: str
    task_id: int | None
    payload: dict
    reward: float | None
    credit: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class LearningProfile(BaseModel):
    """Everything the loop has learned, laid open for inspection."""

    enabled: bool
    level: int
    max_level: int
    label: str
    blurb: str
    events: int
    rated_decisions: int
    trust_pct: float
    progress_pct: float
    to_next: int
    # Averages the loop keeps about this user, as percentages.
    avg_satisfaction_pct: float | None
    follow_rate_pct: float | None
    calibration_bias_pts: float
    cumulative_reward: float
    # parameter -> {raw, applied, label, direction, why}
    tags: list[dict] = []
    factors: list[dict] = []
    recent: list[LearningEvent] = []
    method: str = ""
