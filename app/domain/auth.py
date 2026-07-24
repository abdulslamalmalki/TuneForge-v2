"""Persistent curator identities and opaque server-side sessions."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import config
from app.db.models import AuthEvent, AuthSession, UserAccount
from app.domain.extraction_runs import utc_now_iso

ROLE_ADMINISTRATOR = "administrator"
ROLE_REVIEWER = "reviewer"
ROLES = (ROLE_ADMINISTRATOR, ROLE_REVIEWER)
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

_USERNAME = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
_PBKDF2_ITERATIONS = 600_000
_DUMMY_HASH = "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$" + (
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


class AuthError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Principal:
    user_id: int | None
    username: str
    display_name: str
    role: str

    @property
    def is_administrator(self) -> bool:
        return self.role == ROLE_ADMINISTRATOR

    @property
    def initials(self) -> str:
        parts = [part for part in self.display_name.split() if part]
        return "".join(part[0] for part in parts[:2]).upper() or self.username[:2].upper()


LOCAL_PRINCIPAL = Principal(
    user_id=None,
    username="local-reviewer",
    display_name="Local reviewer",
    role=ROLE_ADMINISTRATOR,
)


def user_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(UserAccount)) or 0)


def list_users(session: Session) -> list[UserAccount]:
    return list(session.scalars(select(UserAccount).order_by(UserAccount.id)))


def list_events(session: Session, *, limit: int = 100) -> list[AuthEvent]:
    return list(
        session.scalars(select(AuthEvent).order_by(AuthEvent.id.desc()).limit(max(1, limit)))
    )


def create_first_administrator(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
) -> UserAccount:
    if user_count(session):
        raise AuthError("Initial administrator setup has already been completed.")
    return _create_user(
        session,
        username=username,
        display_name=display_name,
        password=password,
        role=ROLE_ADMINISTRATOR,
        actor="initial-setup",
    )


def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    actor: str,
) -> UserAccount:
    return _create_user(
        session,
        username=username,
        display_name=display_name,
        password=password,
        role=role,
        actor=actor,
    )


def _create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    actor: str,
) -> UserAccount:
    username = normalize_username(username)
    display_name = _validate_display_name(display_name)
    _validate_password(password)
    if role not in ROLES:
        raise AuthError("Choose a valid account role.")
    if session.scalar(select(UserAccount.id).where(UserAccount.username == username)):
        raise AuthError("That username is already in use.")
    now = utc_now_iso()
    user = UserAccount(
        username=username,
        display_name=display_name,
        role=role,
        password_hash=hash_password(password),
        status=STATUS_ACTIVE,
        created_by=actor,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    session.add(user)
    session.flush()
    _event(session, "user_created", actor, user.id, {"role": role})
    session.commit()
    return user


def authenticate(session: Session, username: str, password: str) -> UserAccount | None:
    try:
        normalized = normalize_username(username)
    except AuthError:
        normalized = ""
    user = session.scalar(select(UserAccount).where(UserAccount.username == normalized))
    encoded = user.password_hash if user is not None else _DUMMY_HASH
    valid = verify_password(password, encoded)
    if user is None or not valid or user.status != STATUS_ACTIVE:
        return None
    return user


def create_session(session: Session, user: UserAccount) -> str:
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    now_text = now.isoformat(timespec="seconds")
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=_token_digest(raw_token),
        created_at=now_text,
        expires_at=(now + timedelta(hours=config.AUTH_SESSION_HOURS)).isoformat(timespec="seconds"),
        last_seen_at=now_text,
        revoked_at=None,
    )
    user.last_login_at = now_text
    user.updated_at = now_text
    session.add(auth_session)
    _event(session, "login_succeeded", user.username, user.id, {})
    session.commit()
    return raw_token


def principal_for_token(session: Session, raw_token: str | None) -> Principal | None:
    if not raw_token:
        return None
    row = session.execute(
        select(AuthSession, UserAccount)
        .join(UserAccount, UserAccount.id == AuthSession.user_id)
        .where(AuthSession.token_hash == _token_digest(raw_token))
    ).one_or_none()
    if row is None:
        return None
    auth_session, user = row
    now = datetime.now(UTC)
    if (
        auth_session.revoked_at is not None
        or user.status != STATUS_ACTIVE
        or datetime.fromisoformat(auth_session.expires_at) <= now
    ):
        return None
    last_seen = datetime.fromisoformat(auth_session.last_seen_at)
    if now - last_seen >= timedelta(minutes=5):
        auth_session.last_seen_at = now.isoformat(timespec="seconds")
        session.commit()
    return Principal(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def revoke_session(session: Session, raw_token: str | None, *, actor: str) -> None:
    if not raw_token:
        return
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == _token_digest(raw_token))
    )
    if auth_session is None or auth_session.revoked_at is not None:
        return
    auth_session.revoked_at = utc_now_iso()
    _event(session, "logout", actor, auth_session.user_id, {})
    session.commit()


def disable_user(session: Session, user_id: int, *, actor: Principal) -> UserAccount:
    user = session.get(UserAccount, user_id)
    if user is None:
        raise AuthError("Account not found.")
    if actor.user_id == user.id:
        raise AuthError("You cannot disable your own account.")
    if user.status == STATUS_DISABLED:
        return user
    if user.role == ROLE_ADMINISTRATOR and _active_admin_count(session) <= 1:
        raise AuthError("TuneForge must keep at least one active administrator.")
    now = utc_now_iso()
    user.status = STATUS_DISABLED
    user.updated_at = now
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    _event(session, "user_disabled", actor.username, user.id, {})
    session.commit()
    return user


def reset_password(
    session: Session,
    user_id: int,
    *,
    password: str,
    actor: Principal,
) -> UserAccount:
    user = session.get(UserAccount, user_id)
    if user is None:
        raise AuthError("Account not found.")
    _validate_password(password)
    now = utc_now_iso()
    user.password_hash = hash_password(password)
    user.updated_at = now
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    _event(session, "password_reset", actor.username, user.id, {})
    session.commit()
    return user


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not _USERNAME.fullmatch(value):
        raise AuthError(
            "Username must be 3–32 characters, start with a letter, and use only "
            "letters, numbers, dots, underscores, or hyphens."
        )
    return value


def hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(_PBKDF2_ITERATIONS),
            _b64(salt),
            _b64(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 1_000_000:
            return False
        salt = _unb64(salt_text)
        expected = _unb64(expected_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _validate_password(password: str) -> None:
    if len(password) < 12:
        raise AuthError("Password must contain at least 12 characters.")
    if len(password) > 128:
        raise AuthError("Password cannot exceed 128 characters.")


def _validate_display_name(value: str) -> str:
    value = " ".join(value.split())
    if not 2 <= len(value) <= 100:
        raise AuthError("Display name must contain between 2 and 100 characters.")
    return value


def _active_admin_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(UserAccount)
            .where(
                UserAccount.role == ROLE_ADMINISTRATOR,
                UserAccount.status == STATUS_ACTIVE,
            )
        )
        or 0
    )


def _event(
    session: Session,
    action: str,
    actor: str,
    subject_user_id: int | None,
    detail: dict[str, object],
) -> None:
    session.add(
        AuthEvent(
            action=action,
            actor=actor,
            subject_user_id=subject_user_id,
            detail=json.dumps(detail, sort_keys=True),
            created_at=utc_now_iso(),
        )
    )


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
