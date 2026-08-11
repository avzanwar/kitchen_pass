from __future__ import annotations

import pytest

from app.scoring import Event, EventType, MatchConfig, PlayerRef, TeamRoster, fold
from app.scoring.events import Team

WON = Event(type=EventType.RALLY_WON)
LOST = Event(type=EventType.RALLY_LOST)
UNDO = Event(type=EventType.UNDO)


def timeout(team: Team) -> Event:
    return Event(type=EventType.TIMEOUT, team=team)


def win_game(config, teams, prior, winner: Team) -> list[Event]:
    """Extend `prior` with the events needed for `winner` to take the current game.

    RALLY_WON/RALLY_LOST are relative to whoever currently holds serve, and the
    first server alternates between games — so hand-written event lists silently
    mean different things in game 2 than in game 1. This drives to an outcome
    instead, which is what the tests actually care about.
    """
    events = list(prior)
    state = fold(config, teams, events)
    assert state.current_game is not None, "match is already over"
    start = state.current_game.number

    for _ in range(500):
        game = state.current_game
        if state.status != "live" or game is None or game.number != start:
            return events
        events.append(WON if game.serving_team == winner else LOST)
        state = fold(config, teams, events)
    raise AssertionError("game did not finish within 500 rallies")


def doubles_teams() -> dict[Team, TeamRoster]:
    return {
        "A": TeamRoster(
            name="Ann & Bo",
            players=[PlayerRef(id="a1", name="Ann"), PlayerRef(id="a2", name="Bo")],
        ),
        "B": TeamRoster(
            name="Cy & Di",
            players=[PlayerRef(id="b1", name="Cy"), PlayerRef(id="b2", name="Di")],
        ),
    }


def singles_teams() -> dict[Team, TeamRoster]:
    return {
        "A": TeamRoster(name="Ann", players=[PlayerRef(id="a1", name="Ann")]),
        "B": TeamRoster(name="Cy", players=[PlayerRef(id="b1", name="Cy")]),
    }


@pytest.fixture
def teams() -> dict[Team, TeamRoster]:
    return doubles_teams()


@pytest.fixture
def singles() -> dict[Team, TeamRoster]:
    return singles_teams()


@pytest.fixture
def single_game() -> MatchConfig:
    """One game to 11, matching the original prototype's model."""
    return MatchConfig(best_of=1, games_to=[11], switch_ends="never")


# API fixtures (engine, session, client, organizer) live in conftest_api.
from .conftest_api import *  # noqa: E402,F401,F403
