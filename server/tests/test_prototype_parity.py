"""Prove the Python engine reproduces the original prototype exactly.

`conformance/parity_jsx.mjs` evaluates the scoring functions straight out of
`kitchen-pass.jsx` and emits a trace. This replays the same rally sequences
through the Python engine and asserts every step matches.

This is the Phase 1 exit criterion: nothing about the existing scoring behaviour
changed on the way into Python. New capabilities (best-of-N, end switches, rally
scoring) are all opt-in via config and are covered by the other suites.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.scoring import (
    Event,
    EventType,
    MatchConfig,
    PlayerRef,
    TeamRoster,
    current_serve_side,
    current_server,
    fold,
    new_match,
    score_call,
)

HARNESS = Path(__file__).resolve().parents[2] / "conformance" / "parity_jsx.mjs"

DOUBLES = {
    "A": TeamRoster(
        name="Ann & Bo",
        players=[PlayerRef(id="a1", name="Ann"), PlayerRef(id="a2", name="Bo")],
    ),
    "B": TeamRoster(
        name="Cy & Di",
        players=[PlayerRef(id="b1", name="Cy"), PlayerRef(id="b2", name="Di")],
    ),
}
SINGLES = {
    "A": TeamRoster(name="Ann", players=[PlayerRef(id="a1", name="Ann")]),
    "B": TeamRoster(name="Cy", players=[PlayerRef(id="b1", name="Cy")]),
}


def _load_trace() -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; cannot run the prototype harness")
    if not HARNESS.exists():
        pytest.skip(f"parity harness missing at {HARNESS}")
    proc = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60, check=False
    )
    if proc.returncode != 0:
        pytest.fail(f"prototype harness failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


TRACE = _load_trace()


def digest(state: Any) -> dict[str, Any]:
    """Mirror of the harness's `digest`, restricted to what the prototype knew
    about — one game, no end switching, no timeouts."""
    game = state.current_game
    done = game is None
    last = state.games[-1]
    return {
        "status": "complete" if done else "live",
        "winner": state.winner,
        "score": dict(last.score),
        "serving_team": None if done else game.serving_team,
        "server_id": None if done else current_server(state).id,
        "server_num": None if done else game.server_num,
        "side": None if done else current_serve_side(state),
        "call": None if done else score_call(state),
        "serve_points": dict(state.serve_points),
    }


@pytest.mark.parametrize("case", TRACE["cases"], ids=lambda c: c["name"])
def test_matches_the_prototype(case: dict[str, Any]) -> None:
    setup = case["setup"]
    config = MatchConfig(
        format=setup["format"],
        scoring="sideout",
        best_of=1,
        games_to=[setup["target"]],
        win_by_2=setup["winBy2"],
        first_server=setup["firstServer"],
        switch_ends="never",
    )
    teams = SINGLES if setup["singles"] else DOUBLES

    state = new_match(config, teams)
    assert digest(state) == case["steps"][0], f"{case['name']}: initial state"

    events: list[Event] = []
    for i, serving_won in enumerate(case["results"]):
        if state.status != "live":
            # The harness stops feeding results once the game is won; so do we.
            assert i + 1 >= len(case["steps"])
            break
        events.append(
            Event(type=EventType.RALLY_WON if serving_won else EventType.RALLY_LOST)
        )
        state = fold(config, teams, events)
        assert digest(state) == case["steps"][i + 1], (
            f"{case['name']} diverged from the prototype at rally {i + 1} "
            f"(serving team {'won' if serving_won else 'lost'})"
        )


def test_harness_covers_both_formats() -> None:
    formats = {c["setup"]["format"] for c in TRACE["cases"]}
    assert formats == {"doubles", "singles"}
    longest = max(len(c["results"]) for c in TRACE["cases"])
    assert longest >= 200, "parity harness should include a long random sequence"
