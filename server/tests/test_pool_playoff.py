"""Pool play into a playoff bracket — the standard sanctioned event shape.

The integration test at the bottom runs a whole 16-team event: pools, standings,
qualification, bracket, champion.
"""

from __future__ import annotations

import pytest

from app.draws import (
    MatchResult,
    MatchStatus,
    champion,
    compute_pool_standings,
    playable,
    pool_playoff_draw,
    pool_rank_map,
    resolve_draw,
)


def ids(n: int) -> list[str]:
    return [f"e{i}" for i in range(1, n + 1)]


def test_draw_contains_both_stages():
    draw = pool_playoff_draw(ids(8), pool_count=2, advance_per_pool=2)
    pool_matches = [m for m in draw.matches if m.bracket == "pool"]
    playoff = [m for m in draw.matches if m.bracket != "pool"]

    assert len(pool_matches) == 2 * (4 * 3 // 2), "two pools of four"
    assert len(playoff) == 3, "four qualifiers -> semis plus a final"
    assert set(draw.pools) == {"A", "B"}


def test_playoff_slots_reference_pool_finishes():
    draw = pool_playoff_draw(ids(8), pool_count=2, advance_per_pool=2)
    playoff_rounds = [m.round for m in draw.matches if m.bracket == "winners"]
    first_round = [
        m for m in draw.matches
        if m.bracket == "winners" and m.round == min(playoff_rounds)
    ]
    pairings = {
        (m.a.source.describe(), m.b.source.describe()) for m in first_round
    }
    assert pairings == {("A1", "B2"), ("B1", "A2")}, (
        "pool winners must be cross-seeded against the other pool's runner-up"
    )


def test_no_first_round_playoff_rematch_of_a_pool_match():
    for pool_count in (2, 4):
        draw = pool_playoff_draw(ids(16), pool_count=pool_count, advance_per_pool=2)
        first_round = [
            m for m in draw.matches
            if m.bracket == "winners" and m.round == min(
                x.round for x in draw.matches if x.bracket == "winners"
            )
        ]
        for match in first_round:
            if match.a.source and match.b.source:
                assert match.a.source.pool != match.b.source.pool, (
                    f"{match.id} replays a pool match in the first playoff round"
                )


def test_playoff_rounds_sort_after_pool_rounds():
    draw = pool_playoff_draw(ids(8), pool_count=2, advance_per_pool=2)
    last_pool = max(m.round for m in draw.matches if m.bracket == "pool")
    first_playoff = min(m.round for m in draw.matches if m.bracket != "pool")
    assert first_playoff > last_pool


def test_playoff_pads_with_byes_when_qualifiers_are_not_a_power_of_two():
    draw = pool_playoff_draw(ids(9), pool_count=3, advance_per_pool=2)
    # 6 qualifiers -> an 8-slot bracket with 2 byes.
    first_round = [m for m in draw.matches if m.bracket == "winners" and m.round == 4]
    byes = sum(1 for m in first_round if m.a.bye or m.b.bye)
    assert byes == 2


def test_rejects_pools_too_small_to_advance():
    with pytest.raises(ValueError, match="must advance"):
        pool_playoff_draw(ids(4), pool_count=2, advance_per_pool=3)


def test_rejects_a_single_qualifier():
    with pytest.raises(ValueError, match="at least two qualifiers"):
        pool_playoff_draw(ids(4), pool_count=1, advance_per_pool=1)


def test_full_event_pools_through_to_a_champion():
    """End to end: 16 entries, 4 pools of 4, top 2 advance to an 8-team bracket.

    Pool results are decided by seed, so the qualifiers and the champion are
    predictable and the whole pipeline can be asserted.
    """
    entries = ids(16)
    seed_rank = {e: i for i, e in enumerate(entries)}
    draw = pool_playoff_draw(entries, pool_count=4, advance_per_pool=2)

    # --- play the pools: the better seed always wins 11-5 ------------------
    results: list[MatchResult] = []
    winners: dict[str, str] = {}
    for match in draw.matches:
        if match.bracket != "pool":
            continue
        a, b = match.a.entry_id, match.b.entry_id
        winner = min([a, b], key=lambda e: seed_rank[e])
        winners[match.id] = winner
        results.append(
            MatchResult(
                match_id=match.id, a_entry=a, b_entry=b, winner=winner,
                points_a=11 if winner == a else 5,
                points_b=11 if winner == b else 5,
                games_a=1 if winner == a else 0,
                games_b=0 if winner == a else 1,
                pool=match.pool,
            )
        )

    tables = compute_pool_standings(draw.pools, results)
    assert all(len(t.rows) == 4 for t in tables.values())
    assert not any(row.unresolved_tie for t in tables.values() for row in t.rows)

    ranks = pool_rank_map(tables)
    qualifiers = {ranks[(p, r)] for p in draw.pools for r in (1, 2)}
    # Snake seeding puts seeds 1-8 as the top two in each pool.
    assert qualifiers == set(entries[:8])

    # --- play the bracket --------------------------------------------------
    for _ in range(20):
        resolved = resolve_draw(draw, winners, ranks)
        ready = [m for m in playable(resolved) if m.bracket != "pool"]
        if not ready:
            break
        for match in ready:
            winners[match.id] = min(
                [match.a_entry, match.b_entry], key=lambda e: seed_rank[e]
            )

    resolved = resolve_draw(draw, winners, ranks)
    assert champion(draw, resolved) == "e1"
    assert all(
        m.status in (MatchStatus.COMPLETE, MatchStatus.BYE, MatchStatus.SKIPPED)
        for m in resolved.values()
    )


def test_bracket_stays_pending_until_pools_finish():
    """A playoff slot must not resolve off a half-finished pool."""
    draw = pool_playoff_draw(ids(8), pool_count=2, advance_per_pool=2)
    resolved = resolve_draw(draw, {}, {})
    playoff = [m for m in resolved.values() if m.bracket != "pool"]
    assert all(m.status is MatchStatus.PENDING for m in playoff)
    assert all(m.a_entry is None and m.b_entry is None for m in playoff)
    # Pool matches, by contrast, are immediately playable.
    assert len([m for m in resolved.values() if m.status is MatchStatus.READY]) == 12
