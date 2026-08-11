"""Pure rule predicates, kept separate from the fold so they can be unit-tested
and pointed at in a rules dispute.

Rule references are to the USA Pickleball Official Rulebook. Where a rule varies
between sanctioning bodies (notably who serves first in games 2 and 3) the
behaviour is configurable rather than hard-coded — see `MatchConfig`.
"""

from __future__ import annotations

from .events import Format, ScoringMode, Side, SwitchEndsRule, Team

OTHER: dict[Team, Team] = {"A": "B", "B": "A"}


def opponent(team: Team) -> Team:
    return OTHER[team]


def target_for_game(games_to: list[int], game_number: int) -> int:
    """Target score for game N (1-indexed). The last entry repeats if the list
    is shorter than `best_of` — so `[11]` means every game is to 11."""
    if not games_to:
        raise ValueError("games_to must not be empty")
    return games_to[min(game_number - 1, len(games_to) - 1)]


def games_needed(best_of: int) -> int:
    """Games required to win the match. Best of 3 -> 2, best of 5 -> 3."""
    return best_of // 2 + 1


def is_game_over(scorer: int, other: int, target: int, win_by_2: bool) -> bool:
    return scorer >= target and (not win_by_2 or scorer - other >= 2)


def midpoint(target: int) -> int:
    """Score at which teams switch ends (Rule 12.A.2): 6 in a game to 11, 8 to
    15, 11 to 21."""
    return (target + 1) // 2


def should_switch_ends(
    rule: SwitchEndsRule, *, is_deciding_game: bool, already_switched: bool, high_score: int,
    target: int,
) -> bool:
    if already_switched:
        return False
    if rule == "never":
        return False
    if rule == "deciding_game" and not is_deciding_game:
        return False
    return high_score >= midpoint(target)


def serve_side(
    *, score_of_serving_team: int, server_idx: int, pos: list[int], fmt: Format,
    scoring: ScoringMode,
) -> Side:
    """Which service court the current server delivers from.

    Singles and rally scoring: score parity — even score serves from the right
    (Rule 4.B.4).

    Doubles side-out scoring: the server serves from wherever they are standing.
    Partners swap sides after each point their team scores, so `pos` is the
    authority. Note this is *not* the same as score parity: at an even score the
    second server is on the left, and the start-of-game "0-0-2" server is on the
    right despite being numbered 2. Parity constrains the position of the
    game's first server (see `start_right_player_is_right`), not the side every
    serve is taken from.
    """
    if fmt == "singles" or scoring == "rally":
        return "R" if score_of_serving_team % 2 == 0 else "L"
    return "R" if server_idx == pos[0] else "L"


def start_right_player_is_right(pos: list[int], score: int) -> bool:
    """The invariant that keeps doubles alignment honest: the player who served
    first at 0-0 stands in the right court exactly when their team's score is
    even. If this ever goes false, positions have desynced from the score."""
    return (pos[0] == 0) == (score % 2 == 0)


def next_game_first_server(
    rule: str, *, previous_first_server: Team, previous_winner: Team | None
) -> Team:
    if rule == "winner" and previous_winner is not None:
        return previous_winner
    if rule == "loser" and previous_winner is not None:
        return opponent(previous_winner)
    return opponent(previous_first_server)


def is_frozen(scorer_score: int, freeze_at: int | None) -> bool:
    """Rally-scoring freeze: a team at the freeze score can only add points on
    its own serve, so a won rally as receiver wins the serve but not a point."""
    return freeze_at is not None and scorer_score >= freeze_at
