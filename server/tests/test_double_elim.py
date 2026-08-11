"""Double elimination — the fiddliest advancement logic in the project.

The load-bearing checks are the simulations: play a whole bracket out and assert
that everyone leaves with exactly two losses except the champion, and that the
grand-final reset fires exactly when it should.
"""

from __future__ import annotations

import itertools

import pytest

from app.draws import (
    MatchStatus,
    champion,
    double_elimination,
    next_power_of_two,
    playable,
    resolve_draw,
)

FIELDS = [2, 3, 4, 5, 7, 8, 9, 16]


def ids(n: int) -> list[str]:
    return [f"e{i}" for i in range(1, n + 1)]


def play_out(draw, pick):
    """Run the bracket to completion, choosing winners with `pick(a, b)`."""
    winners: dict[str, str] = {}
    for _ in range(len(draw.matches) + 5):
        resolved = resolve_draw(draw, winners)
        ready = playable(resolved)
        if not ready:
            return winners, resolved
        for match in ready:
            winners[match.id] = pick(match.a_entry, match.b_entry)
    raise AssertionError("bracket did not converge")


@pytest.mark.parametrize("n", FIELDS)
def test_bracket_shape(n):
    draw = double_elimination(ids(n))
    size = next_power_of_two(n)
    k = size.bit_length() - 1

    wb = [m for m in draw.matches if m.bracket == "winners"]
    lb = [m for m in draw.matches if m.bracket == "losers"]
    finals = [m for m in draw.matches if m.bracket == "final"]

    assert len(wb) == size - 1
    assert len(lb) == size - 2, "losers bracket holds one fewer match than the winners"
    assert len(finals) == 2, "grand final plus the conditional reset"
    assert draw.rounds("losers") == max(2 * k - 2, 0)
    assert len({m.id for m in draw.matches}) == len(draw.matches)


@pytest.mark.parametrize("n", FIELDS)
def test_every_source_reference_points_at_a_real_match(n):
    draw = double_elimination(ids(n))
    known = {m.id for m in draw.matches}
    for match in draw.matches:
        for slot in (match.a, match.b):
            if slot.source and slot.source.match_id:
                assert slot.source.match_id in known, (
                    f"{match.id} references missing match {slot.source.match_id}"
                )


@pytest.mark.parametrize("n", FIELDS)
def test_dependency_graph_is_acyclic(n):
    """Round numbers are per-bracket, so they are not globally comparable — the
    real invariant is that the dependency graph has a topological order."""
    draw = double_elimination(ids(n))
    deps = {
        m.id: {
            s.source.match_id
            for s in (m.a, m.b)
            if s.source and s.source.match_id
        }
        for m in draw.matches
    }

    resolved_order: list[str] = []
    remaining = dict(deps)
    while remaining:
        free = [mid for mid, need in remaining.items() if not (need - set(resolved_order))]
        assert free, f"cycle among {sorted(remaining)}"
        for mid in sorted(free):
            resolved_order.append(mid)
            del remaining[mid]
    assert len(resolved_order) == len(draw.matches)


@pytest.mark.parametrize("n", FIELDS)
def test_within_a_bracket_dependencies_run_backwards(n):
    draw = double_elimination(ids(n))
    index = {m.id: m for m in draw.matches}
    for match in draw.matches:
        for slot in (match.a, match.b):
            if not (slot.source and slot.source.match_id):
                continue
            parent = index[slot.source.match_id]
            assert parent.id != match.id
            if parent.bracket == match.bracket:
                assert parent.round < match.round, (
                    f"{match.id} depends on {parent.id} in the same bracket "
                    f"but not an earlier round"
                )


@pytest.mark.parametrize("n", FIELDS)
def test_grand_final_sorts_after_every_other_match(n):
    """The losers bracket runs longer than the winners bracket, so the grand
    final's round number has to clear both."""
    draw = double_elimination(ids(n))
    gf = draw.by_id("GF")
    others = [m for m in draw.matches if m.bracket != "final"]
    assert all(m.round < gf.round for m in others)
    assert draw.by_id("GF2").round > gf.round


@pytest.mark.parametrize("n", FIELDS)
def test_top_seed_sweeps_and_no_reset_is_needed(n):
    """If the top seed never loses, they win from the winners bracket and the
    grand-final reset must be skipped."""
    draw = double_elimination(ids(n))
    order = {e: i for i, e in enumerate(ids(n))}
    winners, resolved = play_out(draw, lambda a, b: min([a, b], key=lambda e: order[e]))

    assert resolved["GF"].status is MatchStatus.COMPLETE
    assert resolved["GF"].winner == "e1"
    assert resolved["GF2"].status is MatchStatus.SKIPPED
    assert champion(draw, resolved) == "e1"
    assert "GF2" not in winners


@pytest.mark.parametrize("n", FIELDS)
def test_everyone_but_the_champion_takes_two_losses(n):
    """The defining property of double elimination."""
    draw = double_elimination(ids(n))
    order = {e: i for i, e in enumerate(ids(n))}
    _, resolved = play_out(draw, lambda a, b: min([a, b], key=lambda e: order[e]))

    losses: dict[str, int] = dict.fromkeys(ids(n), 0)
    for match in resolved.values():
        if match.status is MatchStatus.COMPLETE and match.loser:
            losses[match.loser] += 1

    winner = champion(draw, resolved)
    assert winner is not None
    assert losses[winner] == 0
    for entry, count in losses.items():
        if entry != winner:
            assert count == 2, f"{entry} was eliminated with {count} loss(es)"


def _losers_run_pick(draw, entries, underdog):
    """Winner-picker where `underdog` loses their opening winners-bracket match
    and then wins everything else — the only way to actually reach the grand
    final *from* the losers bracket."""
    order = {e: i for i, e in enumerate(entries)}
    first_wb = {
        m.id for m in draw.matches if m.bracket == "winners" and m.round == 1
    }

    def pick(match_id, a, b):
        if underdog in (a, b):
            if match_id in first_wb:
                return a if b == underdog else b  # underdog drops their opener
            return underdog
        return min([a, b], key=lambda e: order[e])

    return pick


def play_out_by_id(draw, pick):
    winners: dict[str, str] = {}
    for _ in range(len(draw.matches) + 5):
        resolved = resolve_draw(draw, winners)
        ready = playable(resolved)
        if not ready:
            return winners, resolved
        for match in ready:
            winners[match.id] = pick(match.id, match.a_entry, match.b_entry)
    raise AssertionError("bracket did not converge")


@pytest.mark.parametrize("n", [4, 8, 16])
def test_reset_fires_when_the_losers_bracket_team_wins_the_grand_final(n):
    """A team that lost once runs the losers bracket and wins GF1 — the reset
    must be played, and only then can the title be decided."""
    draw = double_elimination(ids(n))
    underdog = ids(n)[-1]
    winners, resolved = play_out_by_id(
        draw, _losers_run_pick(draw, ids(n), underdog)
    )

    assert resolved["GF"].winner == underdog
    assert resolved["GF2"].status is MatchStatus.COMPLETE, (
        "the winners-bracket team lost GF1, so a reset is required"
    )
    assert {resolved["GF2"].a_entry, resolved["GF2"].b_entry} == {
        resolved["GF"].a_entry,
        resolved["GF"].b_entry,
    }
    assert champion(draw, resolved) == underdog


@pytest.mark.parametrize("n", [4, 8])
def test_champion_is_undecided_until_the_reset_is_played(n):
    draw = double_elimination(ids(n))
    underdog = ids(n)[-1]
    pick = _losers_run_pick(draw, ids(n), underdog)

    winners: dict[str, str] = {}
    for _ in range(len(draw.matches) + 5):
        resolved = resolve_draw(draw, winners)
        ready = [m for m in playable(resolved) if m.id != "GF2"]
        if not ready:
            break
        for match in ready:
            winners[match.id] = pick(match.id, match.a_entry, match.b_entry)

    resolved = resolve_draw(draw, winners)
    assert resolved["GF"].winner == underdog
    assert resolved["GF2"].status is MatchStatus.READY
    assert champion(draw, resolved) is None, "title is not settled until the reset"


@pytest.mark.parametrize("n", [4, 8])
def test_all_outcome_orderings_terminate_with_one_champion(n):
    """Fuzz the bracket with many different result patterns — every one must
    resolve to a single champion with a consistent loss ledger."""
    draw = double_elimination(ids(n))
    entries = ids(n)

    for pattern in itertools.islice(itertools.product([0, 1], repeat=12), 0, 4096, 97):
        counter = itertools.cycle(pattern)
        _, resolved = play_out(draw, lambda a, b, c=counter: a if next(c) else b)

        assert champion(draw, resolved) is not None
        losses: dict[str, int] = dict.fromkeys(entries, 0)
        for match in resolved.values():
            if match.status is MatchStatus.COMPLETE and match.loser:
                losses[match.loser] += 1
        assert max(losses.values()) <= 2


def test_two_entry_bracket_degenerates_gracefully():
    """With two entries there is no losers bracket at all; the beaten finalist
    comes straight out of the single winners match."""
    draw = double_elimination(ids(2))
    assert [m for m in draw.matches if m.bracket == "losers"] == []
    gf = draw.by_id("GF")
    assert gf.b.source.kind == "loser"
    assert gf.b.source.match_id == "W-R1-M1"

    resolved = resolve_draw(draw, {"W-R1-M1": "e1", "GF": "e1"})
    assert resolved["GF2"].status is MatchStatus.SKIPPED
    assert champion(draw, resolved) == "e1"

    resolved = resolve_draw(draw, {"W-R1-M1": "e1", "GF": "e2", "GF2": "e2"})
    assert champion(draw, resolved) == "e2"


def test_losers_drop_in_reversed_order():
    """Winners-bracket losers are cross-fed reversed to reduce rematches."""
    draw = double_elimination(ids(8))
    # Losers round 2 absorbs the winners round 2 losers.
    m1 = draw.by_id("L-R2-M1")
    m2 = draw.by_id("L-R2-M2")
    assert m1.b.source.match_id == "W-R2-M2"
    assert m2.b.source.match_id == "W-R2-M1"


def test_needs_two_entries():
    with pytest.raises(ValueError, match="at least two"):
        double_elimination(ids(1))
