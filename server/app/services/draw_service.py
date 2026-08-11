"""Turn a generated draw into persisted Match rows, and read standings back.

The pure generators in `app.draws` know nothing about the database. This is the
seam between them: it picks the generator, maps entry ids into slots, writes the
matches, and later reads results back out to compute standings and resolve
advancement.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.draws import (
    Draw,
    MatchResult,
    StandingsTable,
    compute_pool_standings,
    compute_standings,
    double_elimination,
    pool_playoff_draw,
    resolve_draw,
    round_robin_draw,
    single_elimination,
)
from app.draws import MatchStatus as DrawStatus
from app.models import (
    Division,
    DrawKind,
    Entry,
    EntryStatus,
    Game,
    Match,
    MatchStatus,
    Stage,
)


class DrawError(ValueError):
    """The draw cannot be generated as configured."""


def seeded_entries(entries: list[Entry]) -> list[Entry]:
    """Order entries for seeding.

    Explicit seeds first in numeric order, then unseeded entries by name so the
    result is deterministic rather than dependent on insertion order.
    """
    seeded = sorted([e for e in entries if e.seed is not None],
                    key=lambda e: (e.seed or 0, e.name))
    unseeded = sorted([e for e in entries if e.seed is None], key=lambda e: e.name)
    return seeded + unseeded


def build_draw(division: Division, entries: list[Entry]) -> Draw:
    active = [e for e in entries if e.status is not EntryStatus.WITHDRAWN]
    if len(active) < 2:
        raise DrawError(
            f"need at least two active entries to generate a draw, got {len(active)}"
        )

    ids = [e.id for e in seeded_entries(active)]
    config: dict[str, Any] = division.draw_config or {}

    match division.draw_kind:
        case DrawKind.ROUND_ROBIN:
            return round_robin_draw(
                ids,
                pool_count=int(config.get("pool_count", 1)),
                double_round=bool(config.get("double_round", False)),
            )
        case DrawKind.SINGLE_ELIMINATION:
            return single_elimination(
                ids, third_place=bool(config.get("third_place", False))
            )
        case DrawKind.DOUBLE_ELIMINATION:
            return double_elimination(ids)
        case DrawKind.POOL_PLAYOFF:
            return pool_playoff_draw(
                ids,
                pool_count=int(config.get("pool_count", 2)),
                advance_per_pool=int(config.get("advance_per_pool", 2)),
                third_place=bool(config.get("third_place", False)),
                double_round=bool(config.get("double_round", False)),
            )

    raise DrawError(f"unsupported draw kind {division.draw_kind}")


async def generate_and_persist(
    session: AsyncSession, division: Division, *, replace: bool = False
) -> list[Match]:
    """Generate the draw and write its matches.

    Refuses to clobber an existing draw unless `replace` is set, and refuses
    outright once any match has been played — regenerating then would silently
    discard results.
    """
    existing = list(
        (await session.exec(select(Match).where(Match.division_id == division.id))).all()
    )
    if existing and not replace:
        raise DrawError(
            "a draw already exists for this division; pass replace=true to rebuild it"
        )
    if any(m.status is MatchStatus.COMPLETE for m in existing):
        raise DrawError(
            "cannot regenerate a draw once matches have been played"
        )

    entries = list(
        (
            await session.exec(select(Entry).where(Entry.division_id == division.id))
        ).all()
    )
    draw = build_draw(division, entries)

    if existing:
        match_ids = [m.id for m in existing]
        await session.exec(delete(Game).where(col(Game.match_id).in_(match_ids)))
        await session.exec(delete(Match).where(col(Match.division_id) == division.id))
        await session.exec(delete(Stage).where(col(Stage.division_id) == division.id))

    if draw.pools:
        session.add(
            Stage(division_id=division.id, kind="pool", ordinal=0, pools=draw.pools)
        )

    rows: list[Match] = []
    for dm in draw.matches:
        rows.append(
            Match(
                division_id=division.id,
                draw_match_id=dm.id,
                bracket=dm.bracket,
                round=dm.round,
                slot=dm.slot,
                pool=dm.pool,
                label=dm.label,
                conditional=dm.conditional,
                decides_title=dm.decides_title,
                entry_a_id=dm.a.entry_id,
                entry_b_id=dm.b.entry_id,
                source_a=dm.a.source.model_dump() if dm.a.source else None,
                source_b=dm.b.source.model_dump() if dm.b.source else None,
                bye_a=dm.a.bye,
                bye_b=dm.b.bye,
            )
        )
    session.add_all(rows)

    division.draw_generated = True
    session.add(division)
    await session.commit()

    await refresh_statuses(session, division)
    return rows


async def _draw_from_rows(matches: list[Match], pools: dict[str, list[str]]) -> Draw:
    """Rebuild the pure Draw structure from persisted rows."""
    from app.draws import DrawMatch, Slot, Source

    return Draw(
        kind="persisted",
        pools=pools,
        matches=[
            DrawMatch(
                id=m.draw_match_id,
                bracket=m.bracket,  # type: ignore[arg-type]
                round=m.round,
                slot=m.slot,
                pool=m.pool,
                label=m.label,
                conditional=m.conditional,
                condition=None,
                decides_title=m.decides_title,
                a=Slot(
                    entry_id=m.entry_a_id,
                    bye=m.bye_a,
                    source=Source(**m.source_a) if m.source_a else None,
                ),
                b=Slot(
                    entry_id=m.entry_b_id,
                    bye=m.bye_b,
                    source=Source(**m.source_b) if m.source_b else None,
                ),
            )
            for m in matches
        ],
    )


async def match_results(session: AsyncSession, division_id: str) -> list[MatchResult]:
    """Completed matches as standings input, with points summed across games."""
    matches = list(
        (
            await session.exec(select(Match).where(Match.division_id == division_id))
        ).all()
    )
    by_id = {m.id: m for m in matches}
    games = list(
        (
            await session.exec(
                select(Game).where(col(Game.match_id).in_(list(by_id)))
            )
        ).all()
    ) if by_id else []

    totals: dict[str, tuple[int, int, int, int]] = {}
    for game in games:
        pa, pb, ga, gb = totals.get(game.match_id, (0, 0, 0, 0))
        totals[game.match_id] = (
            pa + game.score_a,
            pb + game.score_b,
            ga + (1 if game.winner == "A" else 0),
            gb + (1 if game.winner == "B" else 0),
        )

    results: list[MatchResult] = []
    for match in matches:
        if match.status is not MatchStatus.COMPLETE:
            continue
        if not (match.entry_a_id and match.entry_b_id and match.winner_entry_id):
            continue
        pa, pb, ga, gb = totals.get(match.id, (0, 0, 0, 0))
        results.append(
            MatchResult(
                match_id=match.draw_match_id,
                a_entry=match.entry_a_id,
                b_entry=match.entry_b_id,
                winner=match.winner_entry_id,
                points_a=pa,
                points_b=pb,
                games_a=ga,
                games_b=gb,
                pool=match.pool,
            )
        )
    return results


async def division_pools(session: AsyncSession, division_id: str) -> dict[str, list[str]]:
    stage = (
        await session.exec(select(Stage).where(Stage.division_id == division_id))
    ).first()
    if stage is None or not stage.pools:
        return {}
    return {k: list(v) for k, v in stage.pools.items()}


async def standings_for(
    session: AsyncSession, division: Division
) -> dict[str, StandingsTable]:
    """Standings per pool, or a single table when there are no pools."""
    results = await match_results(session, division.id)
    pools = await division_pools(session, division.id)
    tiebreakers = tuple(division.tiebreakers or ())

    if pools:
        return compute_pool_standings(pools, results, tiebreakers=tiebreakers)  # type: ignore[arg-type]

    entries = list(
        (
            await session.exec(select(Entry).where(Entry.division_id == division.id))
        ).all()
    )
    ids = [e.id for e in seeded_entries(entries)
           if e.status is not EntryStatus.WITHDRAWN]
    return {"": compute_standings(ids, results, tiebreakers=tiebreakers)}  # type: ignore[arg-type]


async def refresh_statuses(session: AsyncSession, division: Division) -> list[Match]:
    """Recompute every match's participants and status from results so far.

    Called after a draw is generated and after any match completes. This is
    where byes settle, brackets advance, and pool finishes feed a playoff.
    """
    matches = list(
        (
            await session.exec(select(Match).where(Match.division_id == division.id))
        ).all()
    )
    if not matches:
        return []

    by_draw_id = {m.draw_match_id: m for m in matches}
    pools = await division_pools(session, division.id)
    draw = await _draw_from_rows(matches, pools)

    winners = {
        m.draw_match_id: m.winner_entry_id
        for m in matches
        if m.status is MatchStatus.COMPLETE and m.winner_entry_id
    }

    ranks: dict[tuple[str, int], str] = {}
    if pools:
        from app.draws import pool_rank_map

        pool_matches = [m for m in matches if m.bracket == "pool"]
        if pool_matches and all(
            m.status in (MatchStatus.COMPLETE, MatchStatus.BYE, MatchStatus.SKIPPED)
            for m in pool_matches
        ):
            tables = await standings_for(session, division)
            ranks = pool_rank_map(tables)

    resolved = resolve_draw(draw, winners, ranks)

    status_map = {
        DrawStatus.PENDING: MatchStatus.PENDING,
        DrawStatus.READY: MatchStatus.READY,
        DrawStatus.COMPLETE: MatchStatus.COMPLETE,
        DrawStatus.BYE: MatchStatus.BYE,
        DrawStatus.SKIPPED: MatchStatus.SKIPPED,
    }

    for draw_id, state in resolved.items():
        row = by_draw_id[draw_id]
        # A live match is owned by its scorekeeper — never stomp it back to READY.
        if row.status is MatchStatus.LIVE:
            continue
        row.entry_a_id = state.a_entry
        row.entry_b_id = state.b_entry
        if state.status is not DrawStatus.COMPLETE:
            row.status = status_map[state.status]
            row.winner_entry_id = state.winner
        session.add(row)

    await session.commit()
    return matches
