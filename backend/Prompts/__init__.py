"""Every prompt sent to AVEX (the assistant) lives here - one module per prompt.

`profile.py` holds the shared student-profile fragment the others lead with.
"""

from .chat import (
    APP_HELP,
    CHAT_SYSTEM_INSTRUCTION,
    build_chat_system,
    build_user_context,
)
from .decision import build_decision_prompt
from .insight import SYSTEM_INSTRUCTION, build_insight_prompt
from .profile import profile_lines
from .rating import RATER_SYSTEM_INSTRUCTION, RATING_SCHEMA, build_rating_prompt

__all__ = [
    "SYSTEM_INSTRUCTION",
    "CHAT_SYSTEM_INSTRUCTION",
    "APP_HELP",
    "build_chat_system",
    "build_user_context",
    "RATER_SYSTEM_INSTRUCTION",
    "RATING_SCHEMA",
    "build_insight_prompt",
    "build_decision_prompt",
    "build_rating_prompt",
    "profile_lines",
]
