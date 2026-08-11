"""Property tests.

Hand-written cases check rules we already know about. These check the rules we
*haven't* thought of — they drive thousands of arbitrary legal event sequences
through the fold and assert the structural invariants never break.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.scoring import (
    Event,
    EventType,
    InvalidEvent,
    MatchConfig,
    apply_event,
    check_invariants,
    current_serve_side,
    current_server,
    fold,
    new_match,
)
from app.scoring.events import UNDOABLE, resolve_undos
from app.scoring.rules import games_needed, is_game_over

from .conftest import doubles_teams, singles_teams

CANDIDATE_EVENTS = [
    Event(type=EventType.RALLY_WON),
    Event(type=EventType.RALLY_LOST),
    Event(type=EventType.RALLY_WON),
    Event(type=EventType.RALLY_LOST),
    Event(type=EventType.UNDO),
    Event(type=EventType.TIMEOUT, team="A"),
    Event(type=EventType.TIMEOUT, team="B"),
    Event(type=EventType.TECHNICAL_WARNING, team="A"),
    Event(type=EventType.SET_FIRST_SERVER, team="B"),
]

configs = st.builds(
    MatchConfig,
    format=st.sampled_from(["singles", "doubles"]),
    scoring=st.sampled_from(["sideout", "rally"]),
    best_of=st.sampled_from([1, 3, 5]),
    games_to=st.sampled_from([[11], [11, 11, 15], [15], [21]]),
    win_by_2=st.booleans(),
    timeouts_per_game=st.integers(min_value=0, max_value=2),
    switch_ends=st.sampled_from(["never", "deciding_game", "every_game"]),
    first_server=st.sampled_from(["A", "B"]),
    first_server_rule=st.sampled_from(["alternate", "winner", "loser"]),
)

event_lists = st.lists(st.sampled_from(CANDIDATE_EVENTS), min_size=0, max_size=120)


def legal_prefix(config: MatchConfig, teams, events: list[Event]) -> list[Event]:
    """Keep only the events that apply cleanly, so we exercise the fold with a
    log the transport layer would actually have accepted."""
    kept: list[Event] = []
    state = new_match(config, teams)
    for event in events:
        if event.type is EventType.UNDO:
            candidate = [*kept, event]
            try:
                state = fold(config, teams, candidate)
            except InvalidEvent:
                continue
            kept = candidate
            continue
        try:
            state = apply_event(state, event)
        except InvalidEvent:
            continue
        kept.append(event)
    return kept


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(config=configs, events=event_lists)
def test_invariants_hold_for_any_legal_log(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)
    assert check_invariants(state) == [], state.model_dump()


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_fold_is_deterministic(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    assert fold(config, teams, kept).model_dump() == fold(config, teams, kept).model_dump()


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_undo_is_a_left_inverse_of_the_last_undoable_event(config, events):
    """Appending UNDO must land exactly on the state before the last undoable
    event — including when that event ended a game or the whole match."""
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    effective = resolve_undos(kept)
    if not any(e.type in UNDOABLE for e in effective):
        return

    last = max(i for i, e in enumerate(effective) if e.type in UNDOABLE)
    expected = fold(config, teams, [*effective[:last], *effective[last + 1:]])
    actual = fold(config, teams, [*kept, Event(type=EventType.UNDO)])
    assert actual.model_dump() == expected.model_dump()


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_scores_never_decrease_within_a_game(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)

    seen: dict[int, dict[str, int]] = {}
    state = new_match(config, teams)
    for i in range(len(kept) + 1):
        state = fold(config, teams, kept[:i])
        for game in state.games:
            prior = seen.get(game.number)
            if prior is not None and not any(e.type is EventType.UNDO for e in kept[:i]):
                assert game.score["A"] >= prior["A"]
                assert game.score["B"] >= prior["B"]
            seen[game.number] = dict(game.score)


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_exactly_one_server_while_live(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)

    if state.status != "live":
        assert current_server(state) is None
        return

    server = current_server(state)
    game = state.current_game
    assert server is not None
    assert server in state.teams[game.serving_team].players
    assert current_serve_side(state) in ("R", "L")


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_match_ends_exactly_when_a_team_has_enough_games(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)
    if state.ended_early or state.forfeited_by:
        return

    need = games_needed(config.best_of)
    reached = [t for t in ("A", "B") if state.games_won[t] >= need]
    if reached:
        assert state.status == "complete"
        assert state.winner == reached[0]
    else:
        assert state.status == "live"


@settings(max_examples=200, deadline=None)
@given(config=configs, events=event_lists)
def test_a_live_game_never_satisfies_a_win_condition(config, events):
    teams = doubles_teams() if config.format == "doubles" else singles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)
    game = state.current_game
    if game is None:
        return
    for team, other in (("A", "B"), ("B", "A")):
        assert not is_game_over(
            game.score[team], game.score[other], game.target, config.win_by_2
        )


@settings(max_examples=200, deadline=None)
@given(events=event_lists)
def test_sideout_serve_points_account_for_every_point(events):
    """In side-out scoring only the serving team can score, so serve-point
    attribution must sum to the total points on the board."""
    config = MatchConfig(scoring="sideout", format="doubles", best_of=3, games_to=[11])
    teams = doubles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)

    total = sum(g.score["A"] + g.score["B"] for g in state.games)
    assert sum(state.serve_points.values()) == total


@settings(max_examples=100, deadline=None)
@given(events=event_lists)
def test_doubles_alignment_matches_score_parity(events):
    """The rulebook invariant: the player who served first in the game stands in
    the right court exactly when their team's score is even."""
    config = MatchConfig(scoring="sideout", format="doubles", best_of=3, games_to=[11])
    teams = doubles_teams()
    kept = legal_prefix(config, teams, events)
    state = fold(config, teams, kept)

    for game in state.games:
        for team in ("A", "B"):
            starter_on_right = game.pos[team][0] == 0
            assert starter_on_right == (game.score[team] % 2 == 0)
