"""Shared FastAPI dependencies: authentication, roles and resource lookup."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.security import TokenError, decode_token
from app.models import Division, Role, Tournament, User

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def current_user(creds: CredentialsDep, session: SessionDep) -> User:
    if creds is None:
        raise _UNAUTHENTICATED
    try:
        payload = decode_token(creds.credentials, "access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: Role) -> Callable[..., Coroutine[Any, Any, User]]:
    """Dependency factory restricting an endpoint to the given roles."""

    async def _check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of: {', '.join(sorted(r.value for r in roles))}",
            )
        return user

    return _check


async def owned_tournament(
    tournament_id: str, session: SessionDep, user: CurrentUser
) -> Tournament:
    """Fetch a tournament the caller is allowed to modify.

    Returns 404 rather than 403 for someone else's tournament so the endpoint
    does not confirm that an id exists to a user with no access to it.
    """
    tournament = await session.get(Tournament, tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Tournament not found")
    return tournament


OwnedTournament = Annotated[Tournament, Depends(owned_tournament)]


async def owned_division(
    division_id: str, session: SessionDep, user: CurrentUser
) -> Division:
    division = await session.get(Division, division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Division not found")
    tournament = await session.get(Tournament, division.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Division not found")
    return division


OwnedDivision = Annotated[Division, Depends(owned_division)]


async def public_tournament(public_token: str, session: SessionDep) -> Tournament:
    """Unauthenticated lookup for the read-only spectator view."""
    result = await session.exec(
        select(Tournament).where(Tournament.public_token == public_token)
    )
    tournament = result.first()
    if tournament is None:
        raise HTTPException(status_code=404, detail="Tournament not found")
    return tournament
