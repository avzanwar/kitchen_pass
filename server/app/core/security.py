"""Password hashing and JWT issuance.

A lean implementation rather than `fastapi-users`: the plan named that library,
but it wants ownership of the user model and brings its own DB adapter, which
fights SQLModel. The surface we actually need is small enough that hand-rolling
it keeps the auth path readable and directly testable.

Two token audiences:
* **access** — a signed-in user (organizer, scorekeeper, player).
* **court** — a short-lived, single-match token so a volunteer can score one
  court without an account. It carries no user identity and cannot be used
  anywhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash

from .config import Settings, get_settings

_hasher = PasswordHash.recommended()

ALGORITHM = "HS256"
Audience = Literal["access", "court"]


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(plain, hashed)
    except Exception:
        # A malformed stored hash must read as "wrong password", never a 500.
        return False


def _encode(claims: dict[str, Any], minutes: int, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(
    user_id: str, role: str, settings: Settings | None = None
) -> str:
    settings = settings or get_settings()
    return _encode(
        {"sub": user_id, "role": role, "aud": "access"},
        settings.access_token_minutes,
        settings,
    )


def create_court_token(
    match_id: str, tournament_id: str, settings: Settings | None = None
) -> str:
    """Scoped to exactly one match, so a leaked code cannot touch the rest of
    the event."""
    settings = settings or get_settings()
    return _encode(
        {"sub": f"court:{match_id}", "match_id": match_id,
         "tournament_id": tournament_id, "aud": "court"},
        settings.court_code_minutes,
        settings,
    )


class TokenError(Exception):
    pass


def decode_token(
    token: str, audience: Audience, settings: Settings | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.secret_key, algorithms=[ALGORITHM], audience=audience
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token is not valid") from exc
    return payload
