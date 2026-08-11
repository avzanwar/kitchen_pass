"""Side-out doubles and singles: the rules the prototype already modelled."""

from __future__ import annotations

import pytest

from app.scoring import (
    InvalidEvent,
    MatchConfig,
    apply_event,
    check_invariants,
    current_serve_side,
    current_server,
    fold,
    new_match,
    score_call,
)
from app.scoring.events import Event, EventType

from .conftest import LOST, UNDO, WON, timeout


def test_game_starts_at_zero_zero_two(single_game, teams):
    state = new_match(single_game, teams)
    assert score_call(state) == "0-0-2"
    assert current_server(state).name == "Ann"
    assert current_serve_side(state) == "R"


def test_singles_call_omits_server_number(singles):
    config = MatchConfig(format="singles", best_of=1, games_to=[11], switch_ends="never")
    state = new_match(config, singles)
    assert score_call(state) == "0-0"
    assert current_serve_side(state) == "R"


def test_serving_team_scores_and_partners_swap(single_game, teams):
    state = fold(single_game, teams, [WON])
    game = state.current_game
    assert game.score == {"A": 1, "B": 0}
    # Same physical server, now delivering from the left.
    assert current_server(state).name == "Ann"
    assert current_serve_side(state) == "L"
    assert score_call(state) == "1-0-2"
    assert game.pos["A"] == [1, 0]


def test_first_fault_of_the_game_is_an_immediate_side_out(single_game, teams):
    """The start-of-game server is numbered 2, so one fault hands over serve."""
    state = fold(single_game, teams, [LOST])
    game = state.current_game
    assert game.serving_team == "B"
    assert game.server_num == 1
    assert current_server(state).name == "Cy"
    assert current_serve_side(state) == "R"


def test_second_server_takes_over_before_side_out(single_game, teams):
    # A faults immediately (side out), then B's first server faults.
    state = fold(single_game, teams, [LOST, LOST])
    game = state.current_game
    assert game.serving_team == "B", "serve stays with B for the second server"
    assert game.server_num == 2
    assert current_server(state).name == "Di"


def test_second_server_serves_from_the_opposite_side(single_game, teams):
    # Side out to B, B scores once (partners swap), then B's server 1 faults.
    state = fold(single_game, teams, [LOST, WON, LOST])
    game = state.current_game
    assert game.score["B"] == 1
    assert game.server_num == 2
    # B scored once so Di is now in the right court and serves from there,
    # even though B's score is odd. Parity governs Cy's position, not the side
    # every serve is taken from.
    assert current_server(state).name == "Di"
    assert current_serve_side(state) == "R"


def test_right_court_player_serves_first_after_a_side_out(single_game, teams):
    # A scores once (Ann -> left, Bo -> right), faults away the serve, and B
    # gives it straight back.
    state = fold(single_game, teams, [WON, LOST, LOST, LOST])
    game = state.current_game
    assert game.serving_team == "A"
    assert game.score["A"] == 1
    assert game.pos["A"] == [1, 0], "Bo is in the right court"
    assert current_server(state).name == "Bo"
    assert current_serve_side(state) == "R"


def test_alignment_invariant_holds_through_a_long_rally_sequence(single_game, teams):
    events = [WON, WON, LOST, LOST, WON, LOST, LOST, WON, WON, WON, LOST, LOST, WON]
    state = fold(single_game, teams, events)
    assert check_invariants(state) == []


def test_win_by_two_extends_the_game(teams):
    config = MatchConfig(best_of=1, games_to=[11], win_by_2=True, switch_ends="never")
    # A serves throughout and reaches 11 while B sits on 10.
    events = [WON] * 10 + [LOST, LOST]  # A to 10, side out, B server 1 faults
    state = fold(config, teams, events)
    assert state.current_game.score == {"A": 10, "B": 0}

    # Drive B to 10 as well, then confirm 11-10 does not end it.
    state2 = fold(config, teams, [WON] * 10 + [LOST] + [WON] * 10 + [LOST, LOST] + [WON])
    assert state2.status == "live"
    assert state2.current_game.score == {"A": 11, "B": 10}

    state3 = apply_event(state2, WON)
    assert state3.status == "complete"
    assert state3.winner == "A"
    assert state3.current_game is None


def test_win_by_two_disabled_ends_on_target(teams):
    config = MatchConfig(best_of=1, games_to=[11], win_by_2=False, switch_ends="never")
    state = fold(config, teams, [WON] * 10 + [LOST] + [WON] * 10 + [LOST, LOST] + [WON])
    assert state.status == "complete"
    assert state.winner == "A"
    assert state.games[0].score == {"A": 11, "B": 10}


def test_serve_points_are_attributed_per_player_id(single_game, teams):
    # Ann serves 2 points, side out, Cy serves 3.
    state = fold(single_game, teams, [WON, WON, LOST, WON, WON, WON])
    assert state.serve_points == {"a1": 2, "b1": 3}
    assert state.serve_names == {"a1": "Ann", "b1": "Cy"}


def test_events_after_completion_are_rejected(teams):
    config = MatchConfig(best_of=1, games_to=[2], win_by_2=False, switch_ends="never")
    state = fold(config, teams, [WON, WON])
    assert state.status == "complete"
    with pytest.raises(InvalidEvent, match="no further events"):
        apply_event(state, WON)


def test_timeouts_are_capped(single_game, teams):
    state = fold(single_game, teams, [timeout("A"), timeout("A")])
    assert state.current_game.timeouts_used == {"A": 2, "B": 0}
    with pytest.raises(InvalidEvent, match="used all 2 timeouts"):
        apply_event(state, timeout("A"))


def test_timeout_requires_a_team(single_game, teams):
    with pytest.raises(InvalidEvent, match="requires a team"):
        fold(single_game, teams, [Event(type=EventType.TIMEOUT)])


def test_end_early_abandons_without_a_winner(single_game, teams):
    state = fold(single_game, teams, [WON, WON, Event(type=EventType.END_EARLY)])
    assert state.status == "abandoned"
    assert state.ended_early is True
    assert state.winner is None
    assert state.games[0].score == {"A": 2, "B": 0}


def test_forfeit_awards_the_match_to_the_opponent(single_game, teams):
    state = fold(single_game, teams, [WON, Event(type=EventType.FORFEIT, team="A")])
    assert state.status == "complete"
    assert state.winner == "B"
    assert state.forfeited_by == "A"


def test_undo_reverses_the_last_rally(single_game, teams):
    before = fold(single_game, teams, [WON, WON])
    after = fold(single_game, teams, [WON, WON, WON, UNDO])
    assert after.model_dump() == before.model_dump()


def test_consecutive_undos_walk_back_multiple_events(single_game, teams):
    before = fold(single_game, teams, [WON])
    after = fold(single_game, teams, [WON, WON, LOST, UNDO, UNDO])
    assert after.model_dump() == before.model_dump()


def test_undo_on_an_empty_log_is_a_no_op(single_game, teams):
    assert fold(single_game, teams, [UNDO]).model_dump() == (
        new_match(single_game, teams).model_dump()
    )


def test_undo_can_reopen_a_completed_game(teams):
    config = MatchConfig(best_of=1, games_to=[2], win_by_2=False, switch_ends="never")
    state = fold(config, teams, [WON, WON, UNDO])
    assert state.status == "live"
    assert state.current_game.score == {"A": 1, "B": 0}


def test_set_first_server_before_the_first_rally(single_game, teams):
    state = fold(single_game, teams, [Event(type=EventType.SET_FIRST_SERVER, team="B")])
    assert state.current_game.serving_team == "B"
    assert current_server(state).name == "Cy"


def test_set_first_server_is_rejected_once_play_has_started(single_game, teams):
    with pytest.raises(InvalidEvent, match="before the first rally"):
        fold(single_game, teams, [WON, Event(type=EventType.SET_FIRST_SERVER, team="B")])


def test_duplicate_player_ids_are_rejected(single_game, teams):
    teams["B"].players[0].id = "a1"
    with pytest.raises(ValueError, match="more than once"):
        new_match(single_game, teams)


def test_roster_size_must_match_format(single_game, singles):
    with pytest.raises(ValueError, match="needs 2 player"):
        new_match(single_game, singles)
