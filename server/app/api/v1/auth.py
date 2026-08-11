"""Registration, login and identity."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, session: SessionDep) -> TokenResponse:
    email = body.email.lower()
    existing = (
        await session.exec(select(User).where(func.lower(User.email) == email))
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="That email is already registered")

    user = User(
        email=email,
        hashed_password=hash_password(body.password),
        display_name=body.display_name or email.split("@")[0],
        role=body.role,
    )
    session.add(user)
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.role.value))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    email = body.email.lower()
    user = (
        await session.exec(select(User).where(func.lower(User.email) == email))
    ).first()

    # Same response whether the account is missing, disabled or the password is
    # wrong, so this endpoint cannot be used to enumerate registered emails.
    if user is None or not user.is_active or not verify_password(
        body.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    return TokenResponse(access_token=create_access_token(user.id, user.role.value))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
