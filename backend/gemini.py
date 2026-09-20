"""AVEX - the app's decision assistant, with a deterministic engine fallback.

AVEX is the name students see; Gemini is the model behind it, which is why the
transport here and the `"gemini"` source value stay named after the API. The
key lives in `.env` as GEMINI_API_KEY. If the key is missing or the call fails,
we degrade gracefully to a rule-based explanation built from the scoring
factors, so the product never blocks on the LLM.
"""

from __future__ import annotations

import json
import logging

import httpx

from .config import get_settings
from .Prompts import (
    RATER_SYSTEM_INSTRUCTION,
    build_chat_system,
    RATING_SCHEMA,
    SYSTEM_INSTRUCTION,
    build_decision_prompt,
    build_insight_prompt,
    build_rating_prompt,
)
from .scoring import ScoredTask

logger = logging.getLogger(__name__)

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0]


def _fallback_insight(
    ranked: list[ScoredTask], clarity: float, name: str | None = None
) -> str:
    who = _first_name(name)
    if not ranked:
        return (
            f"Add a couple of tasks{f', {who},' if who else ''} and I'll tell "
            "you which one to start with."
        )
    top = ranked[0]
    opener = f"{who}, start with" if who else "Start with"
    bullets = [
        f'**{opener} "{top.task.title}"** - '
        f"{top.reason[len('Prioritized because '):-1]}."
    ]
    if len(ranked) > 1:
        second = ranked[1]
        bullets.append(
            f'"{second.task.title}" is right behind it '
            f"({round(second.score)}% next to {round(top.score)}%)."
        )
    if clarity >= 0.25:
        bullets.append("It's not a close one, so you can stop weighing it up and just go.")
    elif len(ranked) > 1:
        bullets.append(
            "The top two are neck and neck - if you've already got momentum on "
            "one, that's your tie-break."
        )
    return "\n".join(f"- {b}" for b in bullets)


def _decision_fallback(result: dict, name: str | None = None) -> str:
    who = _first_name(name)
    best = result["best"]
    drivers = result.get("drivers") or []
    scores = result["scores"]
    bullets = [
        f"**Go with {best}**{f', {who}' if who else ''} - it comes out ahead on "
        + " and ".join(d.lower() for d in drivers[:2]) + "."
    ]
    if len(scores) > 1:
        bullets.append(
            f"{scores[1]['option']} was the runner-up "
            f"({round(scores[1]['score'])}% next to {round(scores[0]['score'])}%)."
        )
    if result["clarity"] >= 0.2:
        bullets.append("It's a clear enough gap that I wouldn't second-guess it.")
    else:
        bullets.append(
            "It's close, honestly. Pick the winner, give it 25 proper minutes, "
            "and the doubt sorts itself out."
        )
    for note in result.get("personalization_notes", [])[:1]:
        bullets.append(note + ".")
    return "\n".join(f"- {b}" for b in bullets)


async def generate_decision_insight(
    task, payload: dict, result: dict, survey: dict | None,
    name: str | None = None, ai_allowed: bool = True,
) -> tuple[str, str]:
    """Explain an option-level decision. Returns (text, 'gemini'|'engine').

    `ai_allowed` is the user's AI-processing consent. A False here is treated
    exactly like a missing API key - the engine's own explanation is returned
    and no task text is sent anywhere.
    """
    settings = get_settings()
    if not settings.gemini_api_key or not ai_allowed:
        return _decision_fallback(result, name), "engine"
    text, source = await _call_gemini(
        build_decision_prompt(task, payload, result, survey, name)
    )
    if source == "gemini":
        return text, source
    return _decision_fallback(result, name), "engine"


async def _call_gemini(user_text: str) -> tuple[str, str]:
    """POST to Gemini with model fallbacks. Returns (text, 'gemini') or ('', 'engine')."""
    settings = get_settings()
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        # Generous cap: on "thinking" Gemini models reasoning tokens count
        # against maxOutputTokens, so a small cap truncates the answer.
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }

    # Primary model first; on overload/not-found fall back to alternates.
    models = [settings.gemini_model]
    for alternate in ("gemini-2.5-flash", "gemini-flash-lite-latest"):
        if alternate not in models:
            models.append(alternate)

    async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
        for model in models:
            try:
                response = await client.post(
                    GEMINI_ENDPOINT.format(model=model),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text:
                    return text, "gemini"
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                logger.warning("Gemini call failed on %s: %s", model, exc)

    logger.warning("All Gemini models failed; using engine fallback")
    return "", "engine"


async def generate_insight(
    ranked: list[ScoredTask], survey: dict | None, clarity: float,
    name: str | None = None, ai_allowed: bool = True,
) -> tuple[str, str]:
    """Return (insight_text, source) where source is 'gemini' or 'engine'."""
    settings = get_settings()
    if not settings.gemini_api_key or not ranked or not ai_allowed:
        return _fallback_insight(ranked, clarity, name), "engine"
    text, source = await _call_gemini(
        build_insight_prompt(ranked, survey, clarity, name)
    )
    if source == "gemini":
        return text, source
    return _fallback_insight(ranked, clarity, name), "engine"


# ---------- Stage 2 automation: Gemini rates every option itself ----------


def _normalise_ai_ratings(
    data: dict, options: list[str], criteria: list[dict]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, str]]] | None:
    """Coerce Gemini's JSON into the engine's ratings shape. None if unusable."""
    by_option = {}
    for entry in data.get("options") or []:
        name = str(entry.get("option", "")).strip().lower()
        if name:
            by_option[name] = entry.get("ratings") or []

    ratings: dict[str, dict[str, int]] = {}
    reasons: dict[str, dict[str, str]] = {}
    for option in options:
        rows = by_option.get(option.strip().lower())
        if rows is None:
            return None
        by_criterion = {
            str(row.get("criterion", "")).strip().lower(): row for row in rows
        }
        ratings[option] = {}
        reasons[option] = {}
        for criterion in criteria:
            row = by_criterion.get(criterion["name"].strip().lower())
            if row is None:
                return None
            try:
                value = int(round(float(row.get("rating"))))
            except (TypeError, ValueError):
                return None
            ratings[option][criterion["name"]] = min(5, max(1, value))
            reasons[option][criterion["name"]] = str(row.get("why", "")).strip()[:120]
    return ratings, reasons


def _neutral_ratings(
    options: list[str], criteria: list[dict]
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, str]]]:
    ratings = {o: {c["name"]: 3 for c in criteria} for o in options}
    reasons = {o: {c["name"]: "" for c in criteria} for o in options}
    return ratings, reasons


async def generate_option_ratings(
    task, options: list[str], criteria: list[dict], survey: dict | None,
    context: str | None = None, name: str | None = None,
    ai_allowed: bool = True,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, str]], str]:
    """Have Gemini rate every option on every criterion.

    Returns (ratings, per-rating reasons, 'gemini'|'engine'). On any failure the
    caller still gets a valid (neutral) grid so the flow never dead-ends - the
    'engine' source tells the UI to invite the student to adjust it by hand.
    """
    settings = get_settings()
    if not settings.gemini_api_key or not ai_allowed:
        return (*_neutral_ratings(options, criteria), "engine")

    data = await _call_gemini_json(
        RATER_SYSTEM_INSTRUCTION,
        build_rating_prompt(task, options, criteria, survey, context, name),
        RATING_SCHEMA,
    )
    if data is not None:
        normalised = _normalise_ai_ratings(data, options, criteria)
        if normalised is not None:
            return (*normalised, "gemini")
        logger.warning("Gemini rating JSON did not cover every option/criterion")

    return (*_neutral_ratings(options, criteria), "engine")


async def _call_gemini_json(system: str, user_text: str, schema: dict) -> dict | None:
    """Structured-output Gemini call. Returns the parsed object, or None."""
    settings = get_settings()
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }

    models = [settings.gemini_model]
    for alternate in ("gemini-2.5-flash", "gemini-flash-lite-latest"):
        if alternate not in models:
            models.append(alternate)

    async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
        for model in models:
            try:
                response = await client.post(
                    GEMINI_ENDPOINT.format(model=model),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                logger.warning("Gemini JSON call failed on %s: %s", model, exc)

    logger.warning("All Gemini models failed for structured ratings")
    return None


# ---------- The assistant bubble: a multi-turn conversation ----------


async def chat_reply(
    context: str, history: list[dict], message: str
) -> tuple[str, str]:
    """Answer one chat turn. Returns (reply, 'gemini'|'engine').

    `context` is the student's live account snapshot, `history` is the prior
    turns as [{"role": "user"|"assistant", "content": str}], oldest first.
    An empty reply with source 'engine' tells the caller to use its own
    deterministic answer instead.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return "", "engine"
    return await _call_gemini_chat(build_chat_system(context), history, message)


async def _call_gemini_chat(
    system: str, history: list[dict], message: str
) -> tuple[str, str]:
    """Multi-turn Gemini call with model fallbacks."""
    settings = get_settings()

    contents = []
    for turn in history:
        role = "model" if turn.get("role") == "assistant" else "user"
        text = (turn.get("content") or "").strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        # Same generous cap as the other calls: on "thinking" models the
        # reasoning tokens count against maxOutputTokens, so a small cap
        # truncates the visible answer.
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 2048},
    }

    models = [settings.gemini_model]
    for alternate in ("gemini-2.5-flash", "gemini-flash-lite-latest"):
        if alternate not in models:
            models.append(alternate)

    async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
        for model in models:
            try:
                response = await client.post(
                    GEMINI_ENDPOINT.format(model=model),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text:
                    return text, "gemini"
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                logger.warning("Gemini chat call failed on %s: %s", model, exc)

    logger.warning("All Gemini models failed for chat; using engine fallback")
    return "", "engine"
