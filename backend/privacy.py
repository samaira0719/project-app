"""Data-protection notice, consent scopes and the rules that back them.

One source of truth for both sides of the wire: the backend validates against
these scopes and the sign-up screen renders its consent block from the same
payload (GET /api/privacy/notice), so the text a user agreed to and the text
the server enforces can never drift apart.

Design follows the consent rules that actually bind a product like this one
(GDPR Arts. 4(11), 6, 7, 13, 15-22; the UK GDPR; India's DPDP Act 2023
ss. 5-6; and the CCPA/CPRA notice-at-collection duty):

  * Freely given - declining an optional scope must still leave a working
    product. Every optional scope here has a real, documented fallback.
  * Specific and granular - one checkbox per purpose, never one blanket
    "I agree". Bundling personalization into the terms box would void it.
  * Informed - what is collected, why, on what legal basis, who it reaches,
    how long it is kept, and what rights the user has, all shown *before*
    the account is created, not linked from a footer afterwards.
  * Unambiguous, opt-in - checkboxes render unticked; a pre-ticked box is
    not consent (Planet49, C-673/17).
  * Withdrawable as easily as it was given - PATCH /api/privacy/consent
    flips any optional scope back off at any time, and the learning engine
    stops reading that user's history the moment it does.
  * Demonstrable - every grant and withdrawal is appended to consent_records
    with the exact policy version, so the account can prove what it agreed
    to and when (Art. 7(1)).
  * Age - the product is aimed at 14-25s. Anyone under 16 is asked to
    confirm they have a parent or guardian's permission (Art. 8).

Bumping POLICY_VERSION re-prompts everyone: the /api/privacy/consent
response reports `stale` when a user's stored version is behind this one.
"""

from __future__ import annotations

from typing import Any

# Date-stamped so a support ticket can name the exact text that was shown.
POLICY_VERSION = "2026-10-04"

# Scope keys the API will accept. "essential" is not optional - without it
# there is no account to store anything in, so it is refused at registration
# rather than stored as a preference.
REQUIRED_SCOPES = ("essential",)
OPTIONAL_SCOPES = ("personalization", "ai_processing")
ALL_SCOPES = REQUIRED_SCOPES + OPTIONAL_SCOPES


CONSENT_SCOPES: list[dict[str, Any]] = [
    {
        "key": "essential",
        "title": "Account & core service",
        "required": True,
        "summary": (
            "Create your account and run the decision engine on the tasks you enter."
        ),
        "collects": [
            "Your name and email address",
            "A one-way hash of your password (never the password itself)",
            "The tasks, deadlines and categories you add",
            "Your decision-making survey answers",
            "The options, criteria and ratings inside each decision",
            "Approximate visible time in the app, using a temporary browser-tab session ID and your IP address (or a one-way hash if configured)",
        ],
        "purpose": (
            "To sign you in, store your work, and compute your priority "
            "ranking and decision scores. The maths runs on our own server."
        ),
        "legal_basis": "Performance of a contract - GDPR Art. 6(1)(b)",
        "retention": (
            "Kept while your account exists. Deleting your account erases all "
            "of it immediately and irreversibly."
        ),
        "if_declined": (
            "There is no account and no service without this, so it cannot be "
            "switched off separately - close the account instead."
        ),
    },
    {
        "key": "personalization",
        "title": "Learn from my decisions to improve my results",
        "required": False,
        "summary": (
            "Let the app remember how your past decisions turned out and tune "
            "its weighting to you over time."
        ),
        "collects": [
            "Which recommendation you were given and whether you followed it",
            "The satisfaction rating you give a decision afterwards",
            "Signals the app can already see - tasks completed, decisions "
            "re-run, ratings you corrected, tasks you moved between quadrants",
            "The questions you ask the assistant, so answers improve",
        ],
        "purpose": (
            "To run the feedback loop: each outcome becomes a small reward "
            "signal that nudges how much urgency, importance, cost, interest "
            "and effort count for you specifically. Nothing here is used to "
            "profile you for anyone else, and it never leaves your account."
        ),
        "legal_basis": "Consent - GDPR Art. 6(1)(a); DPDP Act 2023 s. 6",
        "retention": (
            "Learning events are kept for 24 months, then deleted "
            "automatically. You can clear them yourself at any time."
        ),
        "if_declined": (
            "Everything still works. You get the survey-tuned engine and the "
            "same confidence scoring, just without the part that adapts to "
            "your own history - and no outcome data is recorded at all."
        ),
        "withdrawal": (
            "Turning this off stops collection, freezes what was learned, and "
            "lets you erase the learning history in one click."
        ),
    },
    {
        "key": "ai_processing",
        "title": "Let AVEX (Google Gemini) read my task context",
        "required": False,
        # Not a separate box on the sign-up form: it is disclosed in the
        # full notice, switched on with the account so AVEX works from the
        # first decision, and can be turned off any time in the Profile tab.
        "at_signup": False,
        "summary": (
            "Send the wording of your tasks and options to Google's Gemini "
            "API so AVEX can rate options and explain your ranking."
        ),
        "collects": [
            "Task titles, categories and deadlines",
            "The options and criteria in a decision you run",
            "A summary of your survey profile",
            "The messages you send the assistant",
        ],
        "purpose": (
            "To generate the written explanations and the automatic option "
            "ratings. This is the only processing that leaves our server."
        ),
        "legal_basis": "Consent - GDPR Art. 6(1)(a)",
        "recipients": (
            "Google LLC (Gemini API), acting as a processor. Transfers outside "
            "the EEA/UK rely on Standard Contractual Clauses. Prompts are sent "
            "per request and we store only the text that comes back."
        ),
        "retention": (
            "The generated explanation is stored with your decision. We keep "
            "no separate copy of the prompt."
        ),
        "if_declined": (
            "AVEX is on when the account is created so explanations work from "
            "your first decision. Switch it off in the Profile tab and no task "
            "text leaves this server - the built-in rule-based engine writes "
            "the explanations instead."
        ),
        "withdrawal": "Turning this off takes effect on your very next request.",
    },
]


# Shown as the "what this means in practice" body of the notice.
DATA_PRACTICES: list[dict[str, str]] = [
    {
        "title": "We never sell or share your data",
        "body": (
            "No advertising, no data brokers, no third-party analytics. The "
            "only external processor is the one named above, and only if you "
            "allow it."
        ),
    },
    {
        "title": "Your data is fenced to your account",
        "body": (
            "Every query is scoped to your user id. No other user, and no "
            "model shared between users, can read your tasks."
        ),
    },
    {
        "title": "Decisions stay yours",
        "body": (
            "The engine recommends; it never acts for you. Every score can be "
            "opened in the Audit panel and re-derived by hand, which is also "
            "your Art. 22 safeguard against unexplained automated decisions."
        ),
    },
    {
        "title": "Security",
        "body": (
            "Passwords are stored as PBKDF2-SHA256 hashes with 390,000 "
            "iterations and a per-user salt. Sessions use signed, expiring "
            "tokens."
        ),
    },
    {
        "title": "Basic usage measurement",
        "body": (
            "While the app is visible, it sends a heartbeat about every 20 seconds "
            "to estimate active time and diagnose service usage. The server uses "
            "your IP address (or a salted one-way hash when configured) and a "
            "temporary tab session ID. Active session state is held in memory "
            "until inactivity or a server restart. Lifecycle logs go to the "
            "hosting platform's normal logs and follow that platform's retention settings."
        ),
    },
]


YOUR_RIGHTS: list[dict[str, str]] = [
    {"right": "Access & portability",
     "how": "Download everything held about you as JSON, any time.",
     "where": "GET /api/privacy/export"},
    {"right": "Rectification",
     "how": "Edit or retake your survey, and edit or delete any task.",
     "where": "Profile tab"},
    {"right": "Erasure",
     "how": "Delete the account and every row attached to it, irreversibly.",
     "where": "DELETE /api/privacy/account"},
    {"right": "Withdraw consent",
     "how": "Flip any optional purpose off; it stops immediately.",
     "where": "PATCH /api/privacy/consent"},
    {"right": "Restrict learning",
     "how": "Erase the feedback history while keeping your account.",
     "where": "DELETE /api/privacy/learning-data"},
    {"right": "Object to automated decisions",
     "how": ("Every recommendation shows its full derivation and can be "
             "overridden by dragging, re-rating, or ignoring it."),
     "where": "Audit panel"},
]

AGE_NOTICE = (
    "Decidly is built for ages 14-25. If you are under 14, please confirm "
    "a parent or guardian is happy for you to use it before you continue."
)

SUMMARY_LINE = (
    "We collect only what the engine needs to rank your tasks and explain "
    "itself. The learning purpose below is optional and the app works without "
    "it; AVEX (Google Gemini) explanations are on by default and can be "
    "switched off any time from the Profile tab. Basic visible-time usage "
    "measurement uses a temporary tab ID and IP address (or its configured hash)."
)

# Decidly is a personal student project. The full notice says so, and
# carries the user-testing terms from the consent form, so a tester reads the
# same thing on screen as on paper.
PROJECT_CONTEXT: dict[str, Any] = {
    "title": "About this project",
    "lines": [
        "Decidly - Student Decision Lab is a personal student project "
        "built to help students organise their work and think decisions "
        "through. It is not a commercial product.",
        "It is not affiliated with, endorsed by, reviewed by or certified by "
        "any school, examination board, university, government department, "
        "counsellor, psychologist, career advisor or technology company, "
        "including Google.",
        "Recommendations are an organisational and educational aid only. They "
        "are not academic, career, admissions, medical, mental-health, "
        "financial, legal or other professional advice. The app recommends; "
        "it never acts for you, and you can always ignore or override it.",
        "AI-written explanations can be incomplete or wrong. For important "
        "decisions, talk to a parent, teacher or qualified person.",
    ],
    "testing_title": "If you are helping us test",
    "testing": [
        "You may use sample or made-up tasks, a nickname and a test email "
        "address. Entering real information is never required.",
        "Please do not enter identity numbers (Aadhaar, PAN, passport, school "
        "ID), phone numbers, addresses, passwords of other accounts, OTPs, "
        "bank or payment details, health or mental-health information, "
        "private family matters, or personal information about other people.",
        "Individual task text, names, email addresses and survey answers are "
        "never shown in project reports, screenshots, videos or "
        "presentations. Screenshots use demo accounts only.",
        "Usage data may appear in anonymised, combined form only - for example "
        "average decision time or how often recommendations were followed.",
        "Participation is voluntary. You can stop at any time, skip any "
        "question, download everything held about you, and delete your "
        "account and data from the Profile tab.",
        "Data is never sold, shared publicly, used for advertising or for "
        "marketing. The only outside service that may receive data is "
        "Google's Gemini API, and only while AVEX is switched on.",
    ],
}

# Learning events older than this are pruned on write (see feedback.py).
LEARNING_RETENTION_DAYS = 730


def notice() -> dict[str, Any]:
    """The whole notice, as the sign-up screen renders it."""
    return {
        "version": POLICY_VERSION,
        "summary": SUMMARY_LINE,
        "scopes": CONSENT_SCOPES,
        "practices": DATA_PRACTICES,
        "rights": YOUR_RIGHTS,
        "age_notice": AGE_NOTICE,
        "project": PROJECT_CONTEXT,
        "optional_scopes": list(OPTIONAL_SCOPES),
        "retention_days": LEARNING_RETENTION_DAYS,
    }
