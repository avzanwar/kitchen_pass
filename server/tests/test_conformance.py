"""Replay the golden corpus against the Python engine.

This is the regression guard: any unintended behaviour change shows up as a
corpus mismatch here before it reaches the API. The same file is replayed by
`web/tests/conformance.test.ts`, which is what keeps the TypeScript mirror
honest.

If a failure here is an *intended* change, regenerate with
`uv run python scripts/generate_corpus.py` and review the diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.scoring import (
    Event,
    InvalidEvent,
    MatchConfig,
    TeamRoster,
    current_serve_side,
    current_server,
    fold,
    new_match,
    score_call,
    snapshot,
)

CORPUS_PATH = Path(__file__).resolve().parents[2] / "conformance" / "corpus.json"


def load_corpus() -> dict[str, Any]:
    if not CORPUS_PATH.exists():
        pytest.skip(f"corpus not generated at {CORPUS_PATH}")
    return json.loads(CORPUS_PATH.read_text())


CORPUS = load_corpus()


def digest(state: Any) -> dict[str, Any]:
    """Must stay identical to `scripts/generate_corpus.py:digest`."""
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


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["name"])
def test_corpus_case(case: dict[str, Any]) -> None:
    config = MatchConfig(**case["config"])
    teams = {k: TeamRoster(**v) for k, v in case["teams"].items()}
    events = [Event(**e) for e in case["events"]]

    state = new_match(config, teams)
    assert digest(state) == case["steps"][0], f"{case['name']}: initial state"

    applied: list[Event] = []
    for i, event in enumerate(events):
        expected = case["steps"][i + 1]
        applied.append(event)
        try:
            state = fold(config, teams, applied)
        except InvalidEvent as exc:
            assert "error" in expected, (
                f"{case['name']} step {i + 1}: engine rejected {event.type} "
                f"but the corpus expects it to succeed ({exc})"
            )
            assert str(exc) == expected["error"]
            applied.pop()
            continue

        assert "error" not in expected, (
            f"{case['name']} step {i + 1}: engine accepted {event.type} "
            f"but the corpus expects it to be rejected"
        )
        assert digest(state) == expected, (
            f"{case['name']} diverged at step {i + 1} (event {event.type})"
        )

    assert snapshot(state) == case["final"], f"{case['name']}: final snapshot"


def test_corpus_covers_the_important_shapes() -> None:
    """Guard against someone trimming the corpus down to nothing useful."""
    names = {c["name"] for c in CORPUS["cases"]}
    required = {
        "prototype_single_game_doubles",
        "singles_game_to_11",
        "win_by_two_deuce",
        "best_of_three_into_deciding_game",
        "undo_across_game_boundary",
        "rally_scoring_with_freeze",
        "forfeit_mid_match",
        "end_early",
    }
    assert required <= names, f"corpus is missing {required - names}"
    assert sum(len(c["steps"]) for c in CORPUS["cases"]) > 500
