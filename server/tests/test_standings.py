"""Standings and the tiebreaker chain.

The prototype ranked on wins then raw point differential. USA Pickleball
resolves head-to-head first, so these tests pin the sanctioned order and the
awkward cases it exists to handle — especially three-way ties.
"""

from __future__ import annotations

from app.draws import (
    MatchResult,
    compute_pool_standings,
    compute_standings,
    pool_rank_map,
)


def result(a, b, winner, pa, pb, *, pool=None, mid=None):
    return MatchResult(
        match_id=mid or f"{a}v{b}",
        a_entry=a,
        b_entry=b,
        winner=winner,
        points_a=pa,
        points_b=pb,
        games_a=1 if winner == a else 0,
        games_b=1 if winner == b else 0,
        pool=pool,
    )


def order(table):
    return [row.entry_id for row in table.rows]


def test_wins_are_the_primary_sort():
    results = [
        result("a", "b", "a", 11, 5),
        result("a", "c", "a", 11, 7),
        result("b", "c", "b", 11, 9),
    ]
    table = compute_standings(["a", "b", "c"], results)
    assert order(table) == ["a", "b", "c"]
    assert [r.wins for r in table.rows] == [2, 1, 0]
    assert [r.rank for r in table.rows] == [1, 2, 3]


def test_head_to_head_beats_point_differential():
    """b won the head-to-head but has a far worse differential. USA Pickleball
    puts b first; the prototype's wins-then-differential sort would say c."""
    results = [
        result("a", "b", "a", 11, 2),
        result("a", "c", "a", 11, 5),
        result("d", "a", "d", 11, 9),
        result("d", "b", "d", 11, 3),
        result("c", "d", "c", 11, 1),
        result("b", "c", "b", 12, 10),   # b wins the head-to-head narrowly
    ]
    table = compute_standings(["a", "b", "c", "d"], results)

    b = next(r for r in table.rows if r.entry_id == "b")
    c = next(r for r in table.rows if r.entry_id == "c")
    assert b.wins == c.wins == 1
    assert c.point_diff > b.point_diff, "c has by far the better differential"
    assert b.rank < c.rank, "but b won head-to-head, so b places higher"
    assert b.decided_by == "head_to_head"


def test_point_differential_breaks_a_two_way_tie_with_no_head_to_head():
    """Different pools' worth of results: if the tied teams never met, the
    head-to-head criterion is skipped rather than silently scoring 0-0."""
    results = [
        result("a", "c", "a", 11, 4),
        result("b", "d", "b", 11, 8),
        result("c", "d", "c", 11, 6),
        result("a", "d", "a", 11, 3),
        result("b", "c", "b", 11, 9),
    ]
    # a and b are both 2-0 and have not played each other.
    table = compute_standings(["a", "b", "c", "d"], results)
    assert order(table)[:2] == ["a", "b"]
    assert table.rows[0].decided_by == "point_diff"


def test_points_allowed_breaks_a_tie_when_differential_is_level():
    # a: +11-5, then 7-11  -> for 18, against 16, diff +2
    # b: +11-3, then 5-11  -> for 16, against 14, diff +2
    results = [
        result("a", "c", "a", 11, 5),
        result("b", "c", "b", 11, 3),
        result("a", "d", "d", 7, 11),
        result("b", "d", "d", 5, 11),
    ]
    table = compute_standings(["a", "b", "c", "d"], results)
    a = next(r for r in table.rows if r.entry_id == "a")
    b = next(r for r in table.rows if r.entry_id == "b")
    assert a.wins == b.wins
    assert a.point_diff == b.point_diff, "differential is level, so it cannot decide"
    assert b.points_against < a.points_against
    assert b.rank < a.rank, "b allowed fewer points"
    assert b.decided_by == "points_allowed"


def test_three_way_tie_resolved_by_head_to_head_mini_league():
    """a, b, c each 1-1 against each other. Head-to-head within the tied group
    is itself a tie, so it falls through to differential."""
    results = [
        result("a", "b", "a", 11, 9),
        result("b", "c", "b", 11, 4),
        result("c", "a", "c", 11, 8),
    ]
    table = compute_standings(["a", "b", "c"], results)
    assert [r.wins for r in table.rows] == [1, 1, 1]
    # Every entry won exactly one head-to-head, so it cannot separate them and
    # differential decides: b (20-15) +5, a (19-20) -1, c (15-19) -4.
    assert order(table) == ["b", "a", "c"]
    assert all(r.decided_by == "point_diff" for r in table.rows)


def test_three_way_tie_partially_resolved_then_rebroken():
    """One team wins the mini-league outright; the remaining two are re-broken
    by the next criterion rather than left in arbitrary order."""
    results = [
        result("a", "b", "a", 11, 5),
        result("a", "c", "a", 11, 5),
        result("b", "c", "b", 11, 10),
        result("a", "d", "d", 5, 11),
        result("b", "d", "d", 5, 11),
        result("c", "d", "d", 5, 11),
    ]
    table = compute_standings(["a", "b", "c", "d"], results)
    assert order(table)[0] == "d", "d is 3-0"
    rest = order(table)[1:]
    assert rest[0] == "a", "a swept the other two head-to-head"
    assert rest[1] == "b", "b beat c"


def test_head_to_head_is_skipped_when_the_group_has_not_all_played():
    """a and b are tied but never met, while both beat c. Head-to-head cannot
    apply, so the chain must move on instead of treating it as 0-0."""
    results = [
        result("a", "c", "a", 11, 2),
        result("b", "c", "b", 11, 7),
    ]
    table = compute_standings(["a", "b", "c"], results)
    assert order(table)[:2] == ["a", "b"]
    assert table.rows[0].decided_by == "point_diff"


def test_genuinely_unbreakable_ties_are_flagged_not_hidden():
    """Two teams identical on every criterion. Order stays deterministic, but
    both are marked so an organizer knows to flip a coin."""
    results = [
        result("a", "c", "a", 11, 5),
        result("b", "c", "b", 11, 5),
    ]
    table = compute_standings(["a", "b", "c"], results)
    a = next(r for r in table.rows if r.entry_id == "a")
    b = next(r for r in table.rows if r.entry_id == "b")
    assert a.unresolved_tie and b.unresolved_tie
    assert a.decided_by is None

    # Deterministic across runs.
    again = compute_standings(["a", "b", "c"], results)
    assert order(table) == order(again)


def test_standings_ignore_matches_outside_the_group():
    results = [
        result("a", "b", "a", 11, 5),
        result("x", "y", "x", 11, 0),  # another pool entirely
    ]
    table = compute_standings(["a", "b"], results)
    assert [r.played for r in table.rows] == [1, 1]
    assert table.rows[0].points_for == 11


def test_unplayed_entries_rank_last_with_zero_record():
    results = [result("a", "b", "a", 11, 5)]
    table = compute_standings(["a", "b", "c"], results)
    c = next(r for r in table.rows if r.entry_id == "c")
    assert c.played == 0
    assert c.rank == 3


def test_custom_tiebreaker_chain_is_honoured():
    """An organizer running best-of-3 may prefer games won over raw points."""
    results = [
        MatchResult(match_id="m1", a_entry="a", b_entry="c", winner="a",
                    points_a=22, points_b=20, games_a=2, games_b=1),
        MatchResult(match_id="m2", a_entry="b", b_entry="c", winner="b",
                    points_a=22, points_b=8, games_a=2, games_b=0),
    ]
    by_games = compute_standings(["a", "b", "c"], results,
                                 tiebreakers=("game_diff", "point_diff"))
    assert order(by_games)[:2] == ["b", "a"]
    assert by_games.rows[0].decided_by == "game_diff"


def test_pool_standings_and_rank_map_feed_a_playoff():
    pools = {"A": ["a1", "a2", "a3"], "B": ["b1", "b2", "b3"]}
    results = [
        result("a1", "a2", "a1", 11, 4, pool="A"),
        result("a1", "a3", "a1", 11, 6, pool="A"),
        result("a2", "a3", "a2", 11, 7, pool="A"),
        result("b1", "b2", "b1", 11, 2, pool="B"),
        result("b1", "b3", "b1", 11, 5, pool="B"),
        result("b2", "b3", "b2", 11, 9, pool="B"),
    ]
    tables = compute_pool_standings(pools, results)
    assert order(tables["A"]) == ["a1", "a2", "a3"]
    assert order(tables["B"]) == ["b1", "b2", "b3"]

    ranks = pool_rank_map(tables)
    assert ranks[("A", 1)] == "a1"
    assert ranks[("B", 2)] == "b2"


def test_rank_map_omits_unresolved_ties():
    """A bracket must not be seeded off a placement nobody has decided."""
    pools = {"A": ["a1", "a2", "a3"]}
    results = [
        result("a1", "a3", "a1", 11, 5, pool="A"),
        result("a2", "a3", "a2", 11, 5, pool="A"),
    ]
    tables = compute_pool_standings(pools, results)
    assert all(
        row.unresolved_tie for row in tables["A"].rows if row.entry_id in {"a1", "a2"}
    )
    ranks = pool_rank_map(tables)
    assert ("A", 1) not in ranks
    assert ("A", 2) not in ranks
