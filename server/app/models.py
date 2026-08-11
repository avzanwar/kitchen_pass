"""Database tables.

Kept in one module because SQLModel relationships resolve by class name at
mapper-configuration time; a single module keeps that unambiguous and avoids
import cycles for a schema this size.

Portability: the app targets PostgreSQL in deployment and SQLite in tests, so
JSON columns use a variant that becomes JSONB on Postgres, and no
Postgres-specific column types are used anywhere else.
"""

# NOTE: deliberately no `from __future__ import annotations` here. It turns every
# annotation into a string, and SQLModel's `Relationship` resolves relationship
# targets from the real annotation object — with the future import it fails with
# "seems to be using a generic class as the argument to relationship()".

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlmodel import Field, Relationship, SQLModel


def json_column(*, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    """A JSON column: plain JSON on SQLite, JSONB on Postgres.

    Returns a *new* Column each call — SQLAlchemy Column objects bind to one
    table, so a shared module-level instance would silently break the second
    model that used it.
    """
    return sa.Column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=nullable
    )


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Role(StrEnum):
    ORGANIZER = "organizer"
    SCOREKEEPER = "scorekeeper"
    PLAYER = "player"


class TournamentStatus(StrEnum):
    DRAFT = "draft"
    REGISTRATION = "registration"
    LIVE = "live"
    COMPLETE = "complete"


class DivisionFormat(StrEnum):
    SINGLES = "singles"
    DOUBLES = "doubles"
    MIXED = "mixed"


class DrawKind(StrEnum):
    ROUND_ROBIN = "round_robin"
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"
    POOL_PLAYOFF = "pool_playoff"


class EntryStatus(StrEnum):
    REGISTERED = "registered"
    CHECKED_IN = "checked_in"
    WITHDRAWN = "withdrawn"


class MatchStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    LIVE = "live"
    COMPLETE = "complete"
    BYE = "bye"
    SKIPPED = "skipped"
    ABANDONED = "abandoned"


class CourtStatus(StrEnum):
    OPEN = "open"
    IN_USE = "in_use"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=new_id, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    display_name: str = ""
    role: Role = Field(default=Role.ORGANIZER)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Player(SQLModel, table=True):
    __tablename__ = "players"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True)
    #: {"type": "initials"|"emoji"|"photo", "value": ..., "color": ...} —
    #: the same shape the prototype already stores.
    avatar: dict[str, Any] | None = Field(default=None, sa_column=json_column())
    #: DUPR or club rating, when known. Used for seeding.
    rating: float | None = None
    #: Set when this player has an account of their own.
    user_id: str | None = Field(default=None, foreign_key="users.id", index=True)
    owner_id: str | None = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Tournament(SQLModel, table=True):
    __tablename__ = "tournaments"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    slug: str = Field(index=True, unique=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    starts_on: str | None = None
    ends_on: str | None = None
    timezone: str = "UTC"
    status: TournamentStatus = Field(default=TournamentStatus.DRAFT)
    #: Unguessable token behind the public read-only standings page.
    public_token: str = Field(default_factory=new_id, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)

    divisions: list["Division"] = Relationship(
        back_populates="tournament",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    courts: list["Court"] = Relationship(
        back_populates="tournament",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Court(SQLModel, table=True):
    __tablename__ = "courts"

    id: str = Field(default_factory=new_id, primary_key=True)
    tournament_id: str = Field(foreign_key="tournaments.id", index=True)
    name: str
    status: CourtStatus = Field(default=CourtStatus.OPEN)
    sort_order: int = 0

    tournament: Tournament | None = Relationship(back_populates="courts")


class Division(SQLModel, table=True):
    __tablename__ = "divisions"

    id: str = Field(default_factory=new_id, primary_key=True)
    tournament_id: str = Field(foreign_key="tournaments.id", index=True)
    name: str
    format: DivisionFormat = Field(default=DivisionFormat.DOUBLES)
    skill_bracket: str | None = None
    age_bracket: str | None = None

    draw_kind: DrawKind = Field(default=DrawKind.ROUND_ROBIN)
    #: Draw options: pool_count, advance_per_pool, third_place, double_round.
    draw_config: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column(nullable=False)
    )
    #: MatchConfig for the scoring engine, minus the per-match first server.
    match_config: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column(nullable=False)
    )
    tiebreakers: list[str] = Field(
        default_factory=lambda: ["head_to_head", "point_diff", "points_allowed"],
        sa_column=json_column(nullable=False),
    )
    draw_generated: bool = False

    tournament: Tournament | None = Relationship(back_populates="divisions")
    entries: list["Entry"] = Relationship(
        back_populates="division",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    matches: list["Match"] = Relationship(
        back_populates="division",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class EntryPlayer(SQLModel, table=True):
    """Link table. `position` is the roster index the scoring engine uses, so
    it must be stable — 0 is the player who starts the game in the right court.
    """

    __tablename__ = "entry_players"

    entry_id: str = Field(foreign_key="entries.id", primary_key=True)
    player_id: str = Field(foreign_key="players.id", primary_key=True)
    position: int = 0


class Entry(SQLModel, table=True):
    __tablename__ = "entries"

    id: str = Field(default_factory=new_id, primary_key=True)
    division_id: str = Field(foreign_key="divisions.id", index=True)
    name: str
    seed: int | None = None
    status: EntryStatus = Field(default=EntryStatus.REGISTERED)
    created_at: datetime = Field(default_factory=utcnow)

    division: Division | None = Relationship(back_populates="entries")
    players: list[Player] = Relationship(link_model=EntryPlayer)


class Stage(SQLModel, table=True):
    __tablename__ = "stages"

    id: str = Field(default_factory=new_id, primary_key=True)
    division_id: str = Field(foreign_key="divisions.id", index=True)
    kind: str
    ordinal: int = 0
    #: Pool label -> entry ids, when this stage is pool play.
    pools: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column(nullable=False)
    )


class Match(SQLModel, table=True):
    __tablename__ = "matches"
    __table_args__ = (
        sa.UniqueConstraint("division_id", "draw_match_id", name="uq_match_draw_slot"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    division_id: str = Field(foreign_key="divisions.id", index=True)
    stage_id: str | None = Field(default=None, foreign_key="stages.id", index=True)

    #: Id from the generated draw ("W-R1-M3"), unique within the division. This
    #: is what source references point at.
    draw_match_id: str = Field(index=True)
    bracket: str = "pool"
    round: int = 1
    slot: int = 1
    pool: str | None = None
    label: str | None = None
    conditional: bool = False
    decides_title: bool = False

    entry_a_id: str | None = Field(default=None, foreign_key="entries.id")
    entry_b_id: str | None = Field(default=None, foreign_key="entries.id")
    #: Unresolved slot references, as emitted by the draw generator.
    source_a: dict[str, Any] | None = Field(default=None, sa_column=json_column())
    source_b: dict[str, Any] | None = Field(default=None, sa_column=json_column())
    bye_a: bool = False
    bye_b: bool = False

    court_id: str | None = Field(default=None, foreign_key="courts.id", index=True)
    scheduled_at: datetime | None = None
    status: MatchStatus = Field(default=MatchStatus.PENDING, index=True)
    winner_entry_id: str | None = Field(default=None, foreign_key="entries.id")

    #: Exactly one device holds a match at a time. Turning a distributed merge
    #: problem into a lease is what lets the offline client stay simple.
    scorekeeper_id: str | None = Field(default=None, foreign_key="users.id")
    lease_expires_at: datetime | None = None

    #: Who serves first, decided by the coin toss before the match starts.
    first_server: str = "A"
    created_at: datetime = Field(default_factory=utcnow)

    division: Division | None = Relationship(back_populates="matches")
    games: list["Game"] = Relationship(
        back_populates="match",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Game(SQLModel, table=True):
    """Denormalised per-game scores.

    The rally log is authoritative — these rows are a projection of it, written
    when a match's events are appended, so that standings and public views can
    be queried without folding every event.
    """

    __tablename__ = "games"
    __table_args__ = (
        sa.UniqueConstraint("match_id", "game_number", name="uq_game_number"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    match_id: str = Field(foreign_key="matches.id", index=True)
    game_number: int
    target: int
    score_a: int = 0
    score_b: int = 0
    status: str = "live"
    winner: str | None = None

    match: Match | None = Relationship(back_populates="games")


class RallyEvent(SQLModel, table=True):
    """The append-only scoring log — the source of truth for a match.

    Two unique constraints carry the sync guarantees:
    * `(match_id, client_event_id)` makes a retried offline batch idempotent.
    * `(match_id, seq)` detects gaps and reordering, so a client that missed an
      acknowledgement resyncs instead of double-counting.
    """

    __tablename__ = "rally_events"
    __table_args__ = (
        sa.UniqueConstraint("match_id", "client_event_id", name="uq_event_client_id"),
        sa.UniqueConstraint("match_id", "seq", name="uq_event_seq"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    match_id: str = Field(foreign_key="matches.id", index=True)
    seq: int
    client_event_id: str
    type: str
    team: str | None = None
    payload: dict[str, Any] | None = Field(default=None, sa_column=json_column())
    actor_id: str | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
