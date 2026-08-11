"""The authoritative pickleball scoring engine.

Pure: no I/O, no clock, no randomness. Match state is a fold over an append-only
event log, so undo is a compensating event, offline replay is idempotent, and
any state can be reconstructed from the log alone.

This is a direct descendant of the side-out engine in the `kitchen-pass.jsx`
prototype (`makeGame`/`applyResult`/`serveSide`), extended with best-of-N
matches, end switches, rally scoring, timeouts and forfeits.

A TypeScript mirror of this file lives at `web/src/scoring/engine.ts`. The two
are held together by the golden corpus in `tests/conformance/` — if you change
behaviour here, regenerate the corpus and make the TS suite pass.
"""

from __future__ import annotations

import copy

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .events import (
    Event,
    EventType,
    FirstServerRule,
    Format,
    InvalidEvent,
    ScoringMode,
    Side,
    SwitchEndsRule,
    Team,
    resolve_undos,
)
from .rules import (
    games_needed,
    is_frozen,
    is_game_over,
    next_game_first_server,
    opponent,
    serve_side,
    should_switch_ends,
    start_right_player_is_right,
    target_for_game,
)


class PlayerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Stable id. Ad-hoc players get a generated one at entry time — never key
    #: serve stats off the name, or two players called "Mike" merge into one.
    id: str
    name: str


class TeamRoster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    players: list[PlayerRef]


class MatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Format = "doubles"
    scoring: ScoringMode = "sideout"
    best_of: int = Field(default=3, ge=1)
    #: Target per game; the final entry repeats, so [11, 11, 15] means a third
    #: game to 15 and [11] means every game to 11.
    games_to: list[int] = Field(default_factory=lambda: [11, 11, 15])
    win_by_2: bool = True
    #: Rally scoring only: at this score a team can add points only on its own
    #: serve. None disables the freeze.
    freeze_at: int | None = None
    timeouts_per_game: int = Field(default=2, ge=0)
    switch_ends: SwitchEndsRule = "deciding_game"
    #: Who serves first in game 1 — the result of the coin toss.
    first_server: Team = "A"
    first_server_rule: FirstServerRule = "alternate"

    @model_validator(mode="after")
    def _check(self) -> MatchConfig:
        if self.best_of % 2 == 0:
            raise ValueError("best_of must be odd")
        if not self.games_to or any(t < 1 for t in self.games_to):
            raise ValueError("games_to must be non-empty and positive")
        if self.freeze_at is not None and self.scoring != "rally":
            raise ValueError("freeze_at applies to rally scoring only")
        return self


class GameState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    target: int
    score: dict[Team, int]
    serving_team: Team
    #: Physical index into the serving team's roster (always 0 in singles).
    server_idx: int
    #: 1 or 2 in side-out doubles; always 1 otherwise. A game's first server is
    #: numbered 2 so that the first fault is an immediate side out.
    server_num: int
    #: pos[team] == [right_court_player_idx, left_court_player_idx].
    pos: dict[Team, list[int]]
    timeouts_used: dict[Team, int]
    technicals: dict[Team, int]
    #: True when the teams are on the opposite ends from where they started the
    #: match — flipped between games and again at the midpoint switch.
    ends_swapped: bool
    #: Whether the within-game midpoint end switch has already happened.
    switched_at_midpoint: bool = False
    status: str = "live"
    winner: Team | None = None


class MatchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: MatchConfig
    teams: dict[Team, TeamRoster]
    games: list[GameState]
    games_won: dict[Team, int]
    #: player_id -> points scored by their team while they were serving.
    serve_points: dict[str, int] = Field(default_factory=dict)
    #: player_id -> display name, so stats survive a roster edit.
    serve_names: dict[str, str] = Field(default_factory=dict)
    status: str = "live"  # live | complete | abandoned
    winner: Team | None = None
    ended_early: bool = False
    forfeited_by: Team | None = None

    # ---- derived views -------------------------------------------------
    @property
    def current_game(self) -> GameState | None:
        if not self.games:
            return None
        game = self.games[-1]
        return game if game.status == "live" else None


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _expected_roster_size(fmt: Format) -> int:
    return 2 if fmt == "doubles" else 1


def _new_game(config: MatchConfig, number: int, first_server: Team) -> GameState:
    doubles_sideout = config.format == "doubles" and config.scoring == "sideout"
    return GameState(
        number=number,
        target=target_for_game(config.games_to, number),
        score={"A": 0, "B": 0},
        serving_team=first_server,
        server_idx=0,
        server_num=2 if doubles_sideout else 1,
        pos={"A": [0, 1], "B": [0, 1]},
        timeouts_used={"A": 0, "B": 0},
        technicals={"A": 0, "B": 0},
        # Teams change ends between games (Rule 12.A).
        ends_swapped=(number - 1) % 2 == 1,
    )


def new_match(config: MatchConfig, teams: dict[Team, TeamRoster]) -> MatchState:
    need = _expected_roster_size(config.format)
    for side in ("A", "B"):
        if side not in teams:
            raise ValueError(f"missing roster for team {side}")
        if len(teams[side].players) != need:
            raise ValueError(
                f"team {side} needs {need} player(s) for {config.format}, "
                f"got {len(teams[side].players)}"
            )
    ids = [p.id for side in ("A", "B") for p in teams[side].players]
    if len(set(ids)) != len(ids):
        raise ValueError("the same player id appears more than once in the match")

    return MatchState(
        config=config,
        teams=teams,
        games=[_new_game(config, 1, config.first_server)],
        games_won={"A": 0, "B": 0},
    )


# ---------------------------------------------------------------------------
# Derived helpers (safe to call from the UI)
# ---------------------------------------------------------------------------


def current_server(state: MatchState) -> PlayerRef | None:
    game = state.current_game
    if game is None:
        return None
    roster = state.teams[game.serving_team].players
    if state.config.format == "singles":
        return roster[0]
    return roster[game.server_idx]


def current_serve_side(state: MatchState) -> Side | None:
    game = state.current_game
    if game is None:
        return None
    return serve_side(
        score_of_serving_team=game.score[game.serving_team],
        server_idx=game.server_idx,
        pos=game.pos[game.serving_team],
        fmt=state.config.format,
        scoring=state.config.scoring,
    )


def score_call(state: MatchState) -> str | None:
    """What the server calls out before serving: "5-3-2" in side-out doubles,
    "5-3" in singles and rally scoring."""
    game = state.current_game
    if game is None:
        return None
    serving = game.serving_team
    receiving = opponent(serving)
    base = f"{game.score[serving]}-{game.score[receiving]}"
    if state.config.format == "doubles" and state.config.scoring == "sideout":
        return f"{base}-{game.server_num}"
    return base


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def _require_team(event: Event) -> Team:
    if event.team is None:
        raise InvalidEvent(f"{event.type} requires a team")
    return event.team


def _award_point(state: MatchState, game: GameState, team: Team, *, by_server: bool) -> None:
    game.score[team] += 1
    if by_server:
        server = current_server(state)
        if server is not None:
            state.serve_points[server.id] = state.serve_points.get(server.id, 0) + 1
            state.serve_names[server.id] = server.name
    # The scoring team's partners switch sides (Rule 4.B.5). This is what keeps
    # `pos` in step with score parity.
    if state.config.format == "doubles":
        right, left = game.pos[team]
        game.pos[team] = [left, right]


def _maybe_switch_ends(state: MatchState, game: GameState) -> None:
    if should_switch_ends(
        state.config.switch_ends,
        is_deciding_game=_is_deciding_game(state),
        already_switched=game.switched_at_midpoint,
        high_score=max(game.score["A"], game.score["B"]),
        target=game.target,
    ):
        game.switched_at_midpoint = True
        game.ends_swapped = not game.ends_swapped


def _is_deciding_game(state: MatchState) -> bool:
    need = games_needed(state.config.best_of)
    return state.games_won["A"] == need - 1 and state.games_won["B"] == need - 1


def _complete_game(state: MatchState, game: GameState, winner: Team) -> None:
    game.status = "complete"
    game.winner = winner
    state.games_won[winner] += 1

    if state.games_won[winner] >= games_needed(state.config.best_of):
        state.status = "complete"
        state.winner = winner
        return

    nxt = next_game_first_server(
        state.config.first_server_rule,
        previous_first_server=_game_first_server(state, game),
        previous_winner=winner,
    )
    state.games.append(_new_game(state.config, game.number + 1, nxt))


def _game_first_server(state: MatchState, game: GameState) -> Team:
    """Who served first in the given game. Reconstructed rather than stored so
    the state stays minimal; game 1 is config, later games follow the rule."""
    if game.number == 1:
        return state.config.first_server
    server: Team = state.config.first_server
    for n in range(2, game.number + 1):
        prev = state.games[n - 2]
        server = next_game_first_server(
            state.config.first_server_rule,
            previous_first_server=server,
            previous_winner=prev.winner,
        )
    return server


def _apply_rally_won(state: MatchState, game: GameState) -> None:
    team = game.serving_team
    _award_point(state, game, team, by_server=True)
    _maybe_switch_ends(state, game)
    if is_game_over(game.score[team], game.score[opponent(team)], game.target,
                    state.config.win_by_2):
        _complete_game(state, game, team)


def _apply_rally_lost(state: MatchState, game: GameState) -> None:
    serving = game.serving_team
    receiving = opponent(serving)

    if state.config.scoring == "rally":
        # Receiver wins the rally: they take the serve, and score unless frozen.
        if not is_frozen(game.score[receiving], state.config.freeze_at):
            _award_point(state, game, receiving, by_server=False)
        game.serving_team = receiving
        game.server_num = 1
        # The correct server is whoever now stands in the court that score
        # parity dictates.
        if state.config.format == "doubles":
            even = game.score[receiving] % 2 == 0
            game.server_idx = game.pos[receiving][0 if even else 1]
        else:
            game.server_idx = 0
        _maybe_switch_ends(state, game)
        if is_game_over(game.score[receiving], game.score[serving], game.target,
                        state.config.win_by_2):
            _complete_game(state, game, receiving)
        return

    # Side-out scoring: no point changes hands, only the serve.
    if state.config.format == "singles":
        game.serving_team = receiving
        game.server_idx = 0
        game.server_num = 1
        return

    if game.server_num == 1:
        # Second server of the same team takes over.
        game.server_num = 2
        game.server_idx = 1 - game.server_idx
        return

    # Second server faulted: side out. The player in the right court serves
    # first for the incoming team (Rule 4.B.6).
    game.serving_team = receiving
    game.server_num = 1
    game.server_idx = game.pos[receiving][0]


def apply_event(state: MatchState, event: Event) -> MatchState:
    """Apply one event, returning a new state. Never mutates the input."""
    if event.type is EventType.UNDO:
        raise InvalidEvent("UNDO must be resolved by resolve_undos before folding")

    nxt = state.model_copy(deep=True)

    if nxt.status != "live":
        raise InvalidEvent(f"match is {nxt.status}; no further events accepted")

    if event.type is EventType.END_EARLY:
        nxt.status = "abandoned"
        nxt.ended_early = True
        for live_game in nxt.games:
            if live_game.status == "live":
                live_game.status = "abandoned"
        return nxt

    if event.type is EventType.FORFEIT:
        team = _require_team(event)
        nxt.status = "complete"
        nxt.winner = opponent(team)
        nxt.forfeited_by = team
        for live_game in nxt.games:
            if live_game.status == "live":
                live_game.status = "abandoned"
        return nxt

    game = nxt.current_game
    if game is None:
        raise InvalidEvent("no live game")

    match event.type:
        case EventType.RALLY_WON:
            _apply_rally_won(nxt, game)
        case EventType.RALLY_LOST:
            _apply_rally_lost(nxt, game)
        case EventType.TIMEOUT:
            team = _require_team(event)
            if game.timeouts_used[team] >= nxt.config.timeouts_per_game:
                raise InvalidEvent(
                    f"team {team} has used all {nxt.config.timeouts_per_game} timeouts"
                )
            game.timeouts_used[team] += 1
        case EventType.TECHNICAL_WARNING:
            team = _require_team(event)
            game.technicals[team] += 1
        case EventType.SET_FIRST_SERVER:
            team = _require_team(event)
            if game.score["A"] != 0 or game.score["B"] != 0:
                raise InvalidEvent("first server can only be set before the first rally")
            game.serving_team = team
            game.server_idx = 0
            game.server_num = 2 if (
                nxt.config.format == "doubles" and nxt.config.scoring == "sideout"
            ) else 1
        case _:  # pragma: no cover - exhaustive above
            raise InvalidEvent(f"unhandled event type {event.type}")

    return nxt


def fold(
    config: MatchConfig, teams: dict[Team, TeamRoster], events: list[Event]
) -> MatchState:
    """Replay an event log into match state. This is the only way state is
    produced — there is no partial-update path to drift out of sync."""
    state = new_match(config, teams)
    for event in resolve_undos(events):
        state = apply_event(state, event)
    return state


# ---------------------------------------------------------------------------
# Invariants — asserted in tests, and cheap enough to call in a debug endpoint
# ---------------------------------------------------------------------------


def check_invariants(state: MatchState) -> list[str]:
    """Return a list of violated invariants (empty means healthy)."""
    problems: list[str] = []
    cfg = state.config

    for game in state.games:
        for team in ("A", "B"):
            if game.score[team] < 0:
                problems.append(f"game {game.number}: negative score for {team}")
            if sorted(game.pos[team]) != [0, 1]:
                problems.append(f"game {game.number}: pos[{team}] is not a permutation")
            if (
                cfg.format == "doubles"
                and cfg.scoring == "sideout"
                and not start_right_player_is_right(game.pos[team], game.score[team])
            ):
                problems.append(
                    f"game {game.number}: {team} alignment desynced from score "
                    f"(pos={game.pos[team]}, score={game.score[team]})"
                )
            if game.timeouts_used[team] > cfg.timeouts_per_game:
                problems.append(f"game {game.number}: {team} over timeout limit")

        if cfg.format == "singles" and game.server_idx != 0:
            problems.append(f"game {game.number}: singles server_idx must be 0")
        if game.server_num not in (1, 2):
            problems.append(f"game {game.number}: server_num out of range")

        over_a = is_game_over(game.score["A"], game.score["B"], game.target, cfg.win_by_2)
        over_b = is_game_over(game.score["B"], game.score["A"], game.target, cfg.win_by_2)
        if game.status == "live" and (over_a or over_b):
            problems.append(f"game {game.number}: still live but a win condition is met")
        if game.status == "complete" and not (over_a or over_b):
            problems.append(f"game {game.number}: complete but no win condition is met")

    live = [g for g in state.games if g.status == "live"]
    if len(live) > 1:
        problems.append("more than one live game")
    if state.status == "live" and not live:
        problems.append("match is live but has no live game")
    if state.status == "complete" and state.winner is None:
        problems.append("match complete with no winner")

    need = games_needed(cfg.best_of)
    if state.games_won["A"] > need or state.games_won["B"] > need:
        problems.append("games_won exceeds what the match format allows")
    if len(state.games) > cfg.best_of:
        problems.append("more games played than best_of allows")

    return problems


def snapshot(state: MatchState) -> dict[str, object]:
    """Stable, comparable dict used by the cross-language conformance corpus."""
    return copy.deepcopy(state.model_dump(mode="json"))
