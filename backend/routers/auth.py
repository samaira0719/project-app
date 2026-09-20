"""Registration, login and current-user endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import privacy
from ..database import get_db
from ..models import ConsentRecord, User
from ..schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def consent_state(user: User) -> dict:
    """The consent block every auth response carries."""
    version = user.privacy_version or ""
    return {
        "policy_version": version,
        "current_version": privacy.POLICY_VERSION,
        # True when the notice has been revised since this user accepted it,
        # which is the cue for the client to re-present it (GDPR Art. 7(1) -
        # consent covers the text that was actually shown).
        "stale": bool(version) and version != privacy.POLICY_VERSION,
        "accepted_at": user.privacy_accepted_at,
        "personalization": bool(user.consent_personalization),
        "ai_processing": bool(user.consent_ai),
        "age_confirmed": bool(user.age_confirmed),
    }


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        has_survey=user.survey is not None,
        consent=consent_state(user),
    )


def log_consent(
    db: Session, user: User, scope: str, granted: bool, source: str
) -> None:
    """Append one row to the consent ledger. Never updated, never deleted
    while the account lives - it is the account's proof of what it agreed to."""
    db.add(ConsentRecord(
        user_id=user.id,
        scope=scope,
        granted=granted,
        policy_version=privacy.POLICY_VERSION,
        source=source,
    ))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    email = body.email.lower()
    exists = db.scalar(select(User).where(User.email == email))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    consent = body.consent
    # The client echoes back the version of the notice it rendered. A mismatch
    # means the text on screen was not the current one, so the agreement would
    # not be informed - better to make them read the new one than to record a
    # consent against text they never saw.
    if consent.policy_version and consent.policy_version != privacy.POLICY_VERSION:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The privacy notice was updated while you were signing up. "
            "Please reload and read the current version.",
        )

    user = User(
        name=body.name.strip(),
        email=email,
        password_hash=hash_password(body.password),
        privacy_version=privacy.POLICY_VERSION,
        privacy_accepted_at=datetime.now(timezone.utc),
        consent_personalization=bool(consent.personalization),
        consent_ai=bool(consent.ai_processing),
        age_confirmed=bool(consent.age_confirmed),
    )
    db.add(user)
    db.flush()

    log_consent(db, user, "essential", True, "signup")
    log_consent(db, user, "personalization", bool(consent.personalization), "signup")
    log_consent(db, user, "ai_processing", bool(consent.ai_processing), "signup")

    db.commit()
    db.refresh(user)
    return AuthResponse(access_token=create_access_token(user.id), user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return AuthResponse(access_token=create_access_token(user.id), user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)
