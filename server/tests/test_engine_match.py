"""Best-of-N matches, end switches and rally scoring — the rules the prototype
did not model at all."""

from __future__ import annotations

import pytest

from app.scoring import InvalidEvent, MatchConfig, check_invariants, fold, new_match
from app.scoring.events import Event, EventType

from .conftest import LOST, UNDO, WON, win_game

BEST_OF_3 = MatchConfig(best_of=3, games_to=[11, 11, 15], switch_ends="deciding_game")


def test_a_match_is_not_a_single_game(teams):
    state = fold(BEST_OF_3, teams, [WON] * 11)
    assert state.status == "live", "winning one game does not win a best-of-3"
    assert state.games_won == {"A": 1, "B": 0}
    assert len(state.games) == 2
    assert state.current_game.number == 2


def test_match_completes_after_the_required_games(teams):
    events = win_game(BEST_OF_3, teams, [], "A")
    events = win_game(BEST_OF_3, teams, events, "A")
    state = fold(BEST_OF_3, teams, events)
    assert state.status == "complete"
    assert state.winner == "A"
    assert state.games_won == {"A": 2, "B": 0}
    assert len(state.games) == 2, "no dead third game is created"


def test_third_game_uses_its_own_target(teams):
    events = win_game(BEST_OF_3, teams, [], "A")
    events = win_game(BEST_OF_3, teams, events, "B")
    state = fold(BEST_OF_3, teams, events)
    assert state.games_won == {"A": 1, "B": 1}
    assert state.current_game.number == 3
    assert state.current_game.target == 15


def test_best_of_one_completes_on_the_first_game(teams):
    config = MatchConfig(best_of=1, games_to=[11], switch_ends="never")
    state = fold(config, teams, [WON] * 11)
    assert state.status == "complete"
    assert state.winner == "A"


def test_best_of_five_needs_three_games(teams):
    config = MatchConfig(best_of=5, games_to=[11], switch_ends="never")
    events = win_game(config, teams, [], "A")
    events = win_game(config, teams, events, "A")
    assert fold(config, teams, events).status == "live"
    events = win_game(config, teams, events, "A")
    state = fold(config, teams, events)
    assert state.status == "complete"
    assert state.games_won["A"] == 3


def test_even_best_of_is_rejected():
    with pytest.raises(ValueError, match="best_of must be odd"):
        MatchConfig(best_of=2)


def test_teams_change_ends_between_games(teams):
    state = fold(BEST_OF_3, teams, [WON] * 11)
    assert state.games[0].ends_swapped is False
    assert state.games[1].ends_swapped is True


def test_midpoint_switch_only_in_the_deciding_game(teams):
    # Game 1 to 6 — no switch, because game 1 is not the decider.
    state = fold(BEST_OF_3, teams, [WON] * 6)
    assert state.games[0].switched_at_midpoint is False

    # Reach game 3 (the decider) and get to the midpoint of a game to 15.
    events = win_game(BEST_OF_3, teams, [], "A")
    events = win_game(BEST_OF_3, teams, events, "B")
    state = fold(BEST_OF_3, teams, events)
    assert state.current_game.number == 3

    while (game := fold(BEST_OF_3, teams, events).current_game) and max(
        game.score.values()
    ) < 8:
        events.append(WON)
    game3 = fold(BEST_OF_3, teams, events).current_game
    assert game3.number == 3
    assert game3.switched_at_midpoint is True
    assert game3.ends_swapped is True, "game 3 starts unswapped, midpoint flips it"


def test_every_game_switch_rule(teams):
    config = MatchConfig(best_of=3, games_to=[11], switch_ends="every_game")
    state = fold(config, teams, [WON] * 6)
    assert state.games[0].switched_at_midpoint is True


def test_never_switch_rule(teams):
    config = MatchConfig(best_of=3, games_to=[11], switch_ends="never")
    events = win_game(config, teams, [], "A")
    events = win_game(config, teams, events, "B")
    events += [WON] * 8
    state = fold(config, teams, events)
    assert state.current_game.number == 3
    assert state.current_game.switched_at_midpoint is False


def test_midpoint_switch_happens_once(teams):
    config = MatchConfig(best_of=1, games_to=[11], switch_ends="every_game")
    state = fold(config, teams, [WON] * 9)
    game = state.current_game
    assert game.switched_at_midpoint is True
    assert game.ends_swapped is True, "not flipped back on every subsequent point"


def test_midpoint_for_each_target():
    from app.scoring.rules import midpoint

    assert midpoint(11) == 6
    assert midpoint(15) == 8
    assert midpoint(21) == 11


def test_first_server_alternates_between_games_by_default(teams):
    state = fold(BEST_OF_3, teams, [WON] * 11)
    assert state.config.first_server == "A"
    assert state.games[1].serving_team == "B", "game 2 starts with the other team"


def test_first_server_rule_loser(teams):
    config = MatchConfig(best_of=3, games_to=[11], first_server_rule="loser",
                         switch_ends="never")
    state = fold(config, teams, [WON] * 11)  # A wins game 1
    assert state.games[1].serving_team == "B"


def test_first_server_rule_winner(teams):
    config = MatchConfig(best_of=3, games_to=[11], first_server_rule="winner",
                         switch_ends="never")
    state = fold(config, teams, [WON] * 11)  # A wins game 1
    assert state.games[1].serving_team == "A"


def test_deciding_game_coin_toss_override(teams):
    events = win_game(BEST_OF_3, teams, [], "A")
    events = win_game(BEST_OF_3, teams, events, "B")
    state = fold(BEST_OF_3, teams, events)
    assert state.current_game.number == 3
    flipped = fold(BEST_OF_3, teams,
                   [*events, Event(type=EventType.SET_FIRST_SERVER, team="A")])
    assert flipped.current_game.serving_team == "A"


def test_undo_across_a_game_boundary(teams):
    """The hard case: undoing the point that ended game 1 must delete game 2."""
    full = win_game(BEST_OF_3, teams, [], "A")
    before = fold(BEST_OF_3, teams, full[:-1])
    after = fold(BEST_OF_3, teams, [*full, UNDO])
    assert after.model_dump() == before.model_dump()
    assert len(after.games) == 1
    assert after.games_won == {"A": 0, "B": 0}


def test_undo_across_a_match_boundary(teams):
    config = MatchConfig(best_of=3, games_to=[11], switch_ends="never")
    events = win_game(config, teams, [], "A")
    events = win_game(config, teams, events, "A")
    assert fold(config, teams, events).status == "complete"
    reopened = fold(config, teams, [*events, UNDO])
    assert reopened.status == "live"
    assert reopened.winner is None
    assert reopened.games_won == {"A": 1, "B": 0}


def test_timeouts_reset_each_game(teams):
    events = win_game(BEST_OF_3, teams, [Event(type=EventType.TIMEOUT, team="A")], "A")
    state = fold(BEST_OF_3, teams, events)
    assert state.games[0].timeouts_used["A"] == 1
    assert state.games[1].timeouts_used["A"] == 0


# ---------------------------------------------------------------------------
# Rally scoring
# ---------------------------------------------------------------------------

RALLY = MatchConfig(scoring="rally", best_of=1, games_to=[21], freeze_at=None,
                    switch_ends="never", timeouts_per_game=1)


def test_rally_scoring_awards_a_point_on_every_rally(teams):
    # A serves and wins (A=1); A loses the next rally so B scores and takes the
    # serve (B=1); B then loses, so A scores again (A=2) and serves.
    state = fold(RALLY, teams, [WON, LOST, LOST])
    game = state.current_game
    assert game.score == {"A": 2, "B": 1}
    assert game.serving_team == "A"


def test_rally_scoring_has_no_second_server(teams):
    state = fold(RALLY, teams, [LOST])
    game = state.current_game
    assert game.serving_team == "B"
    assert game.server_num == 1


def test_rally_serve_side_follows_score_parity(teams):
    from app.scoring import current_serve_side

    state = fold(RALLY, teams, [])
    assert current_serve_side(state) == "R"
    state = fold(RALLY, teams, [WON])
    assert current_serve_side(state) == "L"
    state = fold(RALLY, teams, [WON, WON])
    assert current_serve_side(state) == "R"


def test_freeze_at_blocks_scoring_off_serve(teams):
    config = MatchConfig(scoring="rally", best_of=1, games_to=[11], freeze_at=10,
                         win_by_2=False, switch_ends="never")
    # A serves to 10, then hands the rally to B repeatedly.
    state = fold(config, teams, [WON] * 10)
    assert state.current_game.score["A"] == 10

    # B wins rallies: B scores normally, A is frozen and cannot.
    state = fold(config, teams, [WON] * 10 + [LOST])
    assert state.current_game.score == {"A": 10, "B": 1}
    assert state.current_game.serving_team == "B"

    # Now A wins a rally as receiver — frozen, so serve only, no point.
    state = fold(config, teams, [WON] * 10 + [LOST, LOST])
    assert state.current_game.score == {"A": 10, "B": 1}
    assert state.current_game.serving_team == "A"

    # A must score on its own serve to finish.
    state = fold(config, teams, [WON] * 10 + [LOST, LOST, WON])
    assert state.status == "complete"
    assert state.winner == "A"


def test_freeze_at_is_rejected_for_sideout_scoring():
    with pytest.raises(ValueError, match="rally scoring only"):
        MatchConfig(scoring="sideout", freeze_at=10)


def test_rally_invariants_hold(teams):
    events = [WON, LOST, LOST, WON, WON, LOST, WON, LOST, LOST, LOST, WON]
    state = fold(RALLY, teams, events)
    assert check_invariants(state) == []


def test_games_to_shorter_than_best_of_repeats_the_last_target(teams):
    config = MatchConfig(best_of=3, games_to=[11], switch_ends="never")
    state = fold(config, teams, [WON] * 11)
    assert state.games[1].target == 11


def test_empty_games_to_is_rejected():
    with pytest.raises(ValueError, match="games_to"):
        MatchConfig(games_to=[])


def test_no_live_game_after_completion(teams):
    config = MatchConfig(best_of=1, games_to=[11], switch_ends="never")
    state = fold(config, teams, [WON] * 11)
    assert state.current_game is None
    with pytest.raises(InvalidEvent):
        fold(config, teams, [WON] * 12)


def test_new_match_has_clean_invariants(teams):
    assert check_invariants(new_match(BEST_OF_3, teams)) == []
