#!/usr/bin/env python
"""Generate the cross-language conformance corpus.

The Python engine is authoritative. This script freezes its behaviour into
`conformance/corpus.json`, which is replayed by BOTH `server/tests/
test_conformance.py` and the TypeScript mirror's `web/tests/conformance.test.ts`.

If the two engines ever disagree, the corpus says which one is wrong.

Run after any deliberate engine change:

    uv run python scripts/generate_corpus.py

then re-run both test suites. A diff in this file that you did not intend is a
behaviour regression.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scoring import (  # noqa: E402
    Event,
    EventType,
    InvalidEvent,
    MatchConfig,
    PlayerRef,
    TeamRoster,
    current_serve_side,
    current_server,
    fold,
    new_match,
    score_call,
    snapshot,
)
from app.scoring.events import Team  # noqa: E402

CORPUS = Path(__file__).resolve().parents[2] / "conformance" / "corpus.json"

WON = Event(type=EventType.RALLY_WON)
LOST = Event(type=EventType.RALLY_LOST)
UNDO = Event(type=EventType.UNDO)


def doubles() -> dict[Team, TeamRoster]:
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


def singles() -> dict[Team, TeamRoster]:
    return {
        "A": TeamRoster(name="Ann", players=[PlayerRef(id="a1", name="Ann")]),
        "B": TeamRoster(name="Cy", players=[PlayerRef(id="b1", name="Cy")]),
    }


def digest(state: Any) -> dict[str, Any]:
    """Compact per-step fingerprint. Small enough to read in a diff, specific
    enough to pinpoint the exact rally where two engines diverge."""
    game = state.current_game
    server = current_server(state)
    return {
        "status": state.status,
        "winner": state.winner,
        "games_won": dict(state.games_won),
        "game": game.number if game else None,
        "score": dict(game.score) if game else None,
        "serving_team": game.serving_team if game else None,
        "server_id": server.id if server else None,
        "server_num": game.server_num if game else None,
        "side": current_serve_side(state),
        "call": score_call(state),
        "ends_swapped": game.ends_swapped if game else None,
    }


def build_case(
    name: str, description: str, config: MatchConfig, teams: dict[Team, TeamRoster],
    events: list[Event],
) -> dict[str, Any]:
    state = new_match(config, teams)
    steps = [digest(state)]
    applied: list[Event] = []
    for event in events:
        applied.append(event)
        try:
            state = fold(config, teams, applied)
        except InvalidEvent as exc:  # a case that deliberately probes a rejection
            steps.append({"error": str(exc)})
            applied.pop()
            continue
        steps.append(digest(state))

    return {
        "name": name,
        "description": description,
        "config": config.model_dump(mode="json"),
        "teams": {k: v.model_dump(mode="json") for k, v in teams.items()},
        "events": [
            e.model_dump(mode="json", exclude_none=True, exclude={"client_event_id",
                                                                  "seq", "actor_id",
                                                                  "created_at"})
            for e in events
        ],
        "steps": steps,
        "final": snapshot(state),
    }


def win_current_game(config, teams, prior, winner: Team) -> list[Event]:
    events = list(prior)
    state = fold(config, teams, events)
    start = state.current_game.number
    for _ in range(500):
        game = state.current_game
        if state.status != "live" or game is None or game.number != start:
            return events
        events.append(WON if game.serving_team == winner else LOST)
        state = fold(config, teams, events)
    raise RuntimeError("game did not finish")


def random_events(rng: random.Random, n: int) -> list[Event]:
    pool = [WON, WON, LOST, LOST, LOST, UNDO]
    return [rng.choice(pool) for _ in range(n)]


def legal_only(config, teams, events: list[Event]) -> list[Event]:
    kept: list[Event] = []
    for event in events:
        candidate = [*kept, event]
        try:
            fold(config, teams, candidate)
        except InvalidEvent:
            continue
        kept = candidate
    return kept


def main() -> None:
    cases: list[dict[str, Any]] = []
    d, s = doubles(), singles()

    proto = MatchConfig(best_of=1, games_to=[11], switch_ends="never")
    cases.append(build_case(
        "prototype_single_game_doubles",
        "One game to 11, matching the original kitchen-pass.jsx model. Locks in "
        "server rotation, the 0-0-2 start and side alternation.",
        proto, d,
        [WON, WON, LOST, LOST, WON, LOST, LOST, WON, WON, WON, LOST, LOST, WON, WON],
    ))

    cases.append(build_case(
        "singles_game_to_11",
        "Singles: score parity drives the serve side and a fault flips serve "
        "immediately (no second server).",
        MatchConfig(format="singles", best_of=1, games_to=[11], switch_ends="never"),
        s,
        [WON, WON, LOST, WON, LOST, LOST, WON, WON, WON],
    ))

    deuce = MatchConfig(best_of=1, games_to=[11], win_by_2=True, switch_ends="never")
    deuce_events = [WON] * 10 + [LOST] + [WON] * 10 + [LOST, LOST] + [WON, LOST, LOST,
                                                                     WON, WON]
    cases.append(build_case(
        "win_by_two_deuce",
        "10-10 and beyond: the game must not end until the lead is two.",
        deuce, d, deuce_events,
    ))

    bo3 = MatchConfig(best_of=3, games_to=[11, 11, 15], switch_ends="deciding_game")
    ev = win_current_game(bo3, d, [], "A")
    ev = win_current_game(bo3, d, ev, "B")
    ev = ev + [WON] * 9
    cases.append(build_case(
        "best_of_three_into_deciding_game",
        "A takes game 1, B game 2, then the decider to 15 with the midpoint end "
        "switch at 8 and the alternating first server.",
        bo3, d, ev,
    ))

    cases.append(build_case(
        "undo_across_game_boundary",
        "Undoing the point that ended game 1 must delete game 2 entirely and "
        "restore the games_won tally.",
        bo3, d, [*win_current_game(bo3, d, [], "A"), UNDO, WON, WON],
    ))

    cases.append(build_case(
        "rally_scoring_with_freeze",
        "Rally scoring to 11 with a freeze at 10: a frozen team wins the serve "
        "but not the point.",
        MatchConfig(scoring="rally", best_of=1, games_to=[11], freeze_at=10,
                    win_by_2=False, switch_ends="never", timeouts_per_game=1),
        d,
        [WON] * 10 + [LOST, LOST, LOST, LOST, WON],
    ))

    cases.append(build_case(
        "timeouts_and_technicals",
        "Timeouts are capped per game and reset between games; technical "
        "warnings are recorded without changing the score.",
        MatchConfig(best_of=3, games_to=[11], timeouts_per_game=2, switch_ends="never"),
        d,
        [
            Event(type=EventType.TIMEOUT, team="A"),
            WON,
            Event(type=EventType.TIMEOUT, team="A"),
            Event(type=EventType.TIMEOUT, team="A"),  # rejected: over the cap
            Event(type=EventType.TECHNICAL_WARNING, team="B"),
            WON,
            Event(type=EventType.TIMEOUT, team="B"),
        ],
    ))

    cases.append(build_case(
        "forfeit_mid_match",
        "A forfeit hands the match to the opponent and abandons the live game.",
        bo3, d, [WON, WON, WON, Event(type=EventType.FORFEIT, team="A")],
    ))

    cases.append(build_case(
        "end_early",
        "Abandoning keeps the score but records no winner.",
        bo3, d, [WON, WON, LOST, WON, Event(type=EventType.END_EARLY)],
    ))

    cases.append(build_case(
        "set_first_server_deciding_game",
        "A deciding-game coin toss overrides the alternating first-server rule.",
        bo3, d,
        [
            *win_current_game(bo3, d, win_current_game(bo3, d, [], "A"), "B"),
            Event(type=EventType.SET_FIRST_SERVER, team="A"),
            WON,
            WON,
        ],
    ))

    # Randomised long matches — the cases nobody would think to write by hand.
    rng = random.Random(20260811)
    random_configs = [
        ("random_doubles_sideout_bo3",
         MatchConfig(best_of=3, games_to=[11, 11, 15], switch_ends="deciding_game"), d),
        ("random_singles_sideout_bo3",
         MatchConfig(format="singles", best_of=3, games_to=[11], switch_ends="every_game"),
         s),
        ("random_doubles_rally_bo1",
         MatchConfig(scoring="rally", best_of=1, games_to=[21], switch_ends="never",
                     timeouts_per_game=1), d),
        ("random_doubles_no_winby2",
         MatchConfig(best_of=5, games_to=[11], win_by_2=False, switch_ends="never",
                     first_server="B", first_server_rule="loser"), d),
    ]
    for name, config, teams in random_configs:
        raw = random_events(rng, 220)
        cases.append(build_case(
            name,
            "Randomly generated legal event log (seeded, reproducible).",
            config, teams, legal_only(config, teams, raw),
        ))

    payload = {
        "version": 1,
        "generated_by": "server/scripts/generate_corpus.py",
        "note": "Authoritative output of the Python engine. Both engines must "
                "reproduce every step digest and final snapshot exactly.",
        "cases": cases,
    }

    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    CORPUS.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    steps = sum(len(c["steps"]) for c in cases)
    print(f"wrote {CORPUS} — {len(cases)} cases, {steps} step digests")


if __name__ == "__main__":
    main()
