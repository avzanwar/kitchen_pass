"""Request and response models.

Separate from the ORM tables so the wire format is deliberate: clients never see
`hashed_password`, and ids are server-assigned rather than accepted from input.
These are also what `openapi-typescript` turns into the frontend's client.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import (
    CourtStatus,
    DivisionFormat,
    DrawKind,
    EntryStatus,
    MatchStatus,
    Role,
    TournamentStatus,
)


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(Base):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = ""
    role: Role = Role.ORGANIZER


class LoginRequest(Base):
    email: EmailStr
    password: str


class TokenResponse(Base):
    access_token: str
    token_type: str = "bearer"


class UserOut(Base):
    id: str
    email: str
    display_name: str
    role: Role
    is_active: bool


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


class PlayerIn(Base):
    name: str = Field(min_length=1, max_length=80)
    avatar: dict[str, Any] | None = None
    rating: float | None = Field(default=None, ge=0, le=8)


class PlayerOut(Base):
    id: str
    name: str
    avatar: dict[str, Any] | None = None
    rating: float | None = None


# ---------------------------------------------------------------------------
# Tournaments and courts
# ---------------------------------------------------------------------------


class TournamentIn(Base):
    name: str = Field(min_length=1, max_length=120)
    starts_on: str | None = None
    ends_on: str | None = None
    timezone: str = "UTC"


class TournamentUpdate(Base):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    starts_on: str | None = None
    ends_on: str | None = None
    timezone: str | None = None
    status: TournamentStatus | None = None


class TournamentOut(Base):
    id: str
    name: str
    slug: str
    owner_id: str
    starts_on: str | None
    ends_on: str | None
    timezone: str
    status: TournamentStatus
    public_token: str


class CourtIn(Base):
    name: str = Field(min_length=1, max_length=60)
    sort_order: int = 0


class CourtOut(Base):
    id: str
    tournament_id: str
    name: str
    status: CourtStatus
    sort_order: int


# ---------------------------------------------------------------------------
# Divisions and entries
# ---------------------------------------------------------------------------


class DivisionIn(Base):
    name: str = Field(min_length=1, max_length=120)
    format: DivisionFormat = DivisionFormat.DOUBLES
    skill_bracket: str | None = None
    age_bracket: str | None = None
    draw_kind: DrawKind = DrawKind.ROUND_ROBIN
    draw_config: dict[str, Any] = Field(default_factory=dict)
    match_config: dict[str, Any] = Field(default_factory=dict)
    tiebreakers: list[str] | None = None


class DivisionOut(Base):
    id: str
    tournament_id: str
    name: str
    format: DivisionFormat
    skill_bracket: str | None
    age_bracket: str | None
    draw_kind: DrawKind
    draw_config: dict[str, Any]
    match_config: dict[str, Any]
    tiebreakers: list[str]
    draw_generated: bool


class EntryIn(Base):
    #: 1 player for singles, 2 for doubles/mixed. Order is preserved and matters
    #: to the scoring engine: index 0 starts the game in the right court.
    player_ids: list[str] = Field(min_length=1, max_length=2)
    name: str | None = None
    seed: int | None = Field(default=None, ge=1)


class EntryOut(Base):
    id: str
    division_id: str
    name: str
    seed: int | None
    status: EntryStatus
    players: list[PlayerOut]


class EntryStatusUpdate(Base):
    status: EntryStatus


# ---------------------------------------------------------------------------
# Draws, matches, standings
# ---------------------------------------------------------------------------


class MatchOut(Base):
    id: str
    division_id: str
    draw_match_id: str
    bracket: str
    round: int
    slot: int
    pool: str | None
    label: str | None
    status: MatchStatus
    entry_a_id: str | None
    entry_b_id: str | None
    entry_a_name: str | None = None
    entry_b_name: str | None = None
    a_label: str = "?"
    b_label: str = "?"
    court_id: str | None
    winner_entry_id: str | None
    decides_title: bool


class StandingOut(Base):
    entry_id: str
    entry_name: str
    rank: int
    played: int
    wins: int
    losses: int
    games_won: int
    games_lost: int
    points_for: int
    points_against: int
    point_diff: int
    unresolved_tie: bool
    decided_by: str | None


class StandingsOut(Base):
    pool: str | None
    rows: list[StandingOut]


class DrawOut(Base):
    division_id: str
    kind: str
    pools: dict[str, list[str]]
    matches: list[MatchOut]
