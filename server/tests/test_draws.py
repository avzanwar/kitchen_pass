"""Draw generation: seeding, byes, bracket shape and advancement."""

from __future__ import annotations

import pytest

from app.draws import (
    MatchStatus,
    UnknownWinner,
    champion,
    next_power_of_two,
    playable,
    pool_label,
    resolve_draw,
    round_robin_draw,
    round_robin_rounds,
    seed_order,
    seed_slots,
    single_elimination,
    snake_pools,
)

FIELDS = [2, 3, 4, 5, 7, 8, 9, 16]


def ids(n: int) -> list[str]:
    return [f"e{i}" for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (1, [1]),
        (2, [1, 2]),
        (4, [1, 4, 2, 3]),
        (8, [1, 8, 4, 5, 2, 7, 3, 6]),
        (16, [1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11]),
    ],
)
def test_seed_order_matches_standard_brackets(size, expected):
    assert seed_order(size) == expected


def test_seed_order_rejects_non_power_of_two():
    with pytest.raises(ValueError, match="power of two"):
        seed_order(6)


@pytest.mark.parametrize("n", FIELDS)
def test_seed_order_pairs_sum_to_size_plus_one(n):
    """The defining property of a standard bracket: every first-round pairing
    sums to size+1, so 1 plays the bottom seed, 2 plays the second-bottom."""
    size = next_power_of_two(n)
    order = seed_order(size)
    for i in range(0, size, 2):
        assert order[i] + order[i + 1] == size + 1


@pytest.mark.parametrize("n", FIELDS)
def test_seed_slots_gives_byes_to_the_top_seeds(n):
    slots = seed_slots(ids(n))
    size = next_power_of_two(n)
    assert len(slots) == size
    assert sorted(s for s in slots if s) == sorted(ids(n))

    byes = size - n
    got_a_bye = []
    for i in range(0, size, 2):
        a, b = slots[i], slots[i + 1]
        if a is None:
            got_a_bye.append(b)
        elif b is None:
            got_a_bye.append(a)
    assert len(got_a_bye) == byes
    # Byes go to the strongest seeds available.
    assert set(got_a_bye) == set(ids(n)[:byes])


def test_pool_label_sequence():
    assert [pool_label(i) for i in range(4)] == ["A", "B", "C", "D"]
    assert pool_label(25) == "Z"
    assert pool_label(26) == "AA"


def test_snake_pools_balance_the_seeds():
    pools = snake_pools(ids(8), 2)
    assert pools == {"A": ["e1", "e4", "e5", "e8"], "B": ["e2", "e3", "e6", "e7"]}


def test_snake_pools_uneven_field():
    pools = snake_pools(ids(7), 3)
    assert pools == {"A": ["e1", "e6", "e7"], "B": ["e2", "e5"], "C": ["e3", "e4"]}
    assert sum(len(v) for v in pools.values()) == 7


def test_snake_pools_rejects_too_many_pools():
    with pytest.raises(ValueError, match="cannot split"):
        snake_pools(ids(3), 4)


# ---------------------------------------------------------------------------
# Round robin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9])
def test_round_robin_pairs_everyone_exactly_once(n):
    rounds = round_robin_rounds(ids(n))
    pairs = [
        frozenset((a, b))
        for rnd in rounds
        for a, b in rnd
        if a is not None and b is not None
    ]
    assert len(pairs) == len(set(pairs)), "a pairing was repeated"
    assert len(pairs) == n * (n - 1) // 2
    assert len(rounds) == (n if n % 2 else n - 1)


@pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9])
def test_round_robin_nobody_plays_twice_in_a_round(n):
    for rnd in round_robin_rounds(ids(n)):
        appearing = [x for pair in rnd for x in pair if x is not None]
        assert len(appearing) == len(set(appearing))


@pytest.mark.parametrize("n", [3, 5, 7, 9])
def test_odd_fields_spread_the_byes_evenly(n):
    """With an odd field one entry sits out each round. Nobody should sit out
    twice while someone else never does."""
    sits_out: dict[str, int] = {e: 0 for e in ids(n)}
    for rnd in round_robin_rounds(ids(n)):
        # An entry drawn against the odd-field placeholder does not play.
        playing = {x for a, b in rnd if a and b for x in (a, b)}
        for entry in ids(n):
            if entry not in playing:
                sits_out[entry] += 1
    assert set(sits_out.values()) == {1}


def test_round_robin_draw_with_pools():
    draw = round_robin_draw(ids(8), pool_count=2)
    assert set(draw.pools) == {"A", "B"}
    assert len(draw.matches) == 2 * (4 * 3 // 2)
    assert {m.pool for m in draw.matches} == {"A", "B"}
    assert len({m.id for m in draw.matches}) == len(draw.matches)


def test_double_round_robin_doubles_the_schedule():
    single = round_robin_draw(ids(4))
    double = round_robin_draw(ids(4), double_round=True)
    assert len(double.matches) == 2 * len(single.matches)


def test_round_robin_needs_two_entries():
    with pytest.raises(ValueError, match="at least two"):
        round_robin_draw(ids(1))


# ---------------------------------------------------------------------------
# Single elimination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", FIELDS)
def test_single_elimination_shape(n):
    draw = single_elimination(ids(n))
    size = next_power_of_two(n)
    assert len(draw.matches) == size - 1
    assert draw.rounds("winners") == size.bit_length() - 1
    assert len({m.id for m in draw.matches}) == len(draw.matches)


@pytest.mark.parametrize("n", FIELDS)
def test_single_elimination_produces_one_champion(n):
    """Play the whole bracket with the higher seed always winning; seed 1 must
    take it, and every entry must appear exactly once in round 1."""
    draw = single_elimination(ids(n))
    order = {e: i for i, e in enumerate(ids(n))}

    winners: dict[str, str] = {}
    for _ in range(len(draw.matches) + 2):
        resolved = resolve_draw(draw, winners)
        ready = playable(resolved)
        if not ready:
            break
        for match in ready:
            winners[match.id] = min(
                [match.a_entry, match.b_entry], key=lambda e: order[e]
            )

    resolved = resolve_draw(draw, winners)
    assert champion(draw, resolved) == "e1"

    appearances = [
        e
        for m in resolved.values()
        if m.round == 1
        for e in (m.a_entry, m.b_entry)
        if e
    ]
    assert sorted(appearances) == sorted(ids(n))


def test_byes_complete_without_being_played():
    draw = single_elimination(ids(5))
    resolved = resolve_draw(draw, {})
    byes = [m for m in resolved.values() if m.status is MatchStatus.BYE]
    assert len(byes) == 3, "8-slot bracket with 5 entries has 3 byes"
    assert all(m.winner is not None and m.loser is None for m in byes)
    # Nothing to schedule for a bye.
    assert all(m.id not in {r.id for r in playable(resolved)} for m in byes)


def test_third_place_match_is_generated():
    draw = single_elimination(ids(4), third_place=True)
    third = draw.by_id("W-3P")
    assert third.bracket == "final"
    assert third.a.source.kind == "loser"
    assert third.b.source.kind == "loser"

    resolved = resolve_draw(draw, {"W-R1-M1": "e1", "W-R1-M2": "e2"})
    assert resolved["W-3P"].a_entry == "e4"
    assert resolved["W-3P"].b_entry == "e3"


def test_recording_a_winner_who_did_not_play_is_rejected():
    draw = single_elimination(ids(4))
    with pytest.raises(UnknownWinner, match="did not play"):
        resolve_draw(draw, {"W-R1-M1": "e2"})


def test_unknown_match_id_is_rejected():
    draw = single_elimination(ids(4))
    with pytest.raises(UnknownWinner, match="not part of this draw"):
        resolve_draw(draw, {"nope": "e1"})


def test_bracket_needs_two_entries():
    with pytest.raises(ValueError, match="at least two"):
        single_elimination(ids(1))
