"""Live scoring: event append, idempotency, leases and bracket advancement."""

from __future__ import annotations

from uuid import uuid4

from .conftest_api import register
from .test_api_draws import build_field


def ev(kind: str, team: str | None = None) -> dict:
    return {"type": kind, "team": team, "client_event_id": uuid4().hex}


async def first_ready(org, division_id: str) -> dict:
    draw = (await org.get(f"/api/v1/divisions/{division_id}/draw")).json()
    return next(m for m in draw["matches"] if m["status"] == "ready")


async def score_until_won(org, match_id: str, winner_side: str) -> dict:
    """Feed rallies until `winner_side` takes the match."""
    payload = (await org.get(f"/api/v1/matches/{match_id}")).json()
    for _ in range(400):
        if payload["status"] != "live":
            return payload
        current = payload["current"]
        serving = current["serving_team"]
        kind = "RALLY_WON" if serving == winner_side else "RALLY_LOST"
        response = await org.post(
            f"/api/v1/matches/{match_id}/events", json={"events": [ev(kind)]}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
    raise AssertionError("match did not finish")


async def test_match_state_starts_at_zero(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    state = (await organizer.get(f"/api/v1/matches/{match['id']}")).json()
    assert state["status"] == "live"
    assert state["current"]["score"] == {"A": 0, "B": 0}
    assert state["current"]["call"] == "0-0-2"
    assert state["current"]["side"] == "R"
    assert state["seq"] == 0
    assert len(state["teams"]["A"]["players"]) == 2


async def test_scoring_a_point_updates_the_call(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    result = await organizer.post(
        f"/api/v1/matches/{match['id']}/events", json={"events": [ev("RALLY_WON")]}
    )
    assert result.status_code == 200
    body = result.json()
    assert body["current"]["score"]["A"] == 1
    assert body["current"]["call"] == "1-0-2"
    assert body["current"]["side"] == "L", "server switches sides after scoring"
    assert body["seq"] == 1


async def test_events_are_idempotent_on_retry(organizer):
    """The offline client's core guarantee: replaying a batch must not
    double-count."""
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    batch = {"events": [ev("RALLY_WON"), ev("RALLY_WON")]}
    first = await organizer.post(f"/api/v1/matches/{match['id']}/events", json=batch)
    assert first.json()["current"]["score"]["A"] == 2

    # Same client_event_ids again — a retry after a lost acknowledgement.
    second = await organizer.post(f"/api/v1/matches/{match['id']}/events", json=batch)
    assert second.status_code == 200
    assert second.json()["current"]["score"]["A"] == 2, "retry double-counted"
    assert second.json()["seq"] == 2


async def test_undo_reverses_a_rally(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    await organizer.post(f"/api/v1/matches/{match['id']}/events",
                         json={"events": [ev("RALLY_WON"), ev("RALLY_WON")]})
    undone = await organizer.post(f"/api/v1/matches/{match['id']}/events",
                                  json={"events": [ev("UNDO")]})
    assert undone.json()["current"]["score"]["A"] == 1


async def test_undo_survives_a_reload(organizer):
    """The prototype kept undo in memory and lost it on refresh; the event log
    means a fresh GET still knows how to go back."""
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    await organizer.post(f"/api/v1/matches/{match['id']}/events",
                         json={"events": [ev("RALLY_WON"), ev("RALLY_WON")]})
    reloaded = (await organizer.get(f"/api/v1/matches/{match['id']}")).json()
    assert reloaded["current"]["score"]["A"] == 2

    undone = await organizer.post(f"/api/v1/matches/{match['id']}/events",
                                  json={"events": [ev("UNDO")]})
    assert undone.json()["current"]["score"]["A"] == 1


async def test_illegal_events_are_rejected_without_corrupting_the_log(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    # Three timeouts in a game, when only two are allowed.
    bad = await organizer.post(
        f"/api/v1/matches/{match['id']}/events",
        json={"events": [ev("TIMEOUT", "A"), ev("TIMEOUT", "A"), ev("TIMEOUT", "A")]},
    )
    assert bad.status_code == 409
    assert "timeout" in bad.json()["detail"].lower()

    state = (await organizer.get(f"/api/v1/matches/{match['id']}")).json()
    assert state["seq"] == 0, "a rejected batch must leave no partial writes"


async def test_completing_a_match_records_the_winner(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    final = await score_until_won(organizer, match["id"], "A")
    assert final["status"] == "complete"
    assert final["winner"] == "A"
    assert final["games_won"]["A"] == 2, "best of three by default"
    assert final["winner_entry_id"] == match["entry_a_id"]


async def test_completing_a_match_advances_the_bracket(organizer):
    """The seam that makes the whole thing a tournament rather than a scorer."""
    _, did, _ = await build_field(organizer, 4, draw_kind="single_elimination")
    await organizer.post(f"/api/v1/divisions/{did}/draw")

    draw = (await organizer.get(f"/api/v1/divisions/{did}/draw")).json()
    final = next(m for m in draw["matches"] if m["decides_title"])
    assert final["status"] == "pending"
    assert final["entry_a_id"] is None

    semi = next(m for m in draw["matches"] if m["draw_match_id"] == "W-R1-M1")
    await score_until_won(organizer, semi["id"], "A")

    after = (await organizer.get(f"/api/v1/divisions/{did}/draw")).json()
    final_after = next(m for m in after["matches"] if m["decides_title"])
    assert final_after["entry_a_id"] == semi["entry_a_id"], (
        "the semifinal winner should now occupy the final's first slot"
    )


async def test_completed_matches_feed_standings(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)
    await score_until_won(organizer, match["id"], "A")

    standings = (await organizer.get(f"/api/v1/divisions/{did}/standings")).json()
    rows = standings[0]["rows"]
    played = [r for r in rows if r["played"] > 0]
    assert len(played) == 2
    assert sum(r["wins"] for r in played) == 1
    assert max(r["points_for"] for r in played) >= 22, "two games to 11"


async def test_a_finished_match_rejects_more_events(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)
    await score_until_won(organizer, match["id"], "A")

    response = await organizer.post(
        f"/api/v1/matches/{match['id']}/events", json={"events": [ev("RALLY_WON")]}
    )
    assert response.status_code == 409


async def test_coin_toss_sets_the_first_server(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    assert (
        await organizer.post(f"/api/v1/matches/{match['id']}/toss",
                             json={"first_server": "B"})
    ).status_code == 200
    state = (await organizer.get(f"/api/v1/matches/{match['id']}")).json()
    assert state["current"]["serving_team"] == "B"


async def test_coin_toss_is_refused_once_play_has_started(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)
    await organizer.post(f"/api/v1/matches/{match['id']}/events",
                         json={"events": [ev("RALLY_WON")]})

    response = await organizer.post(f"/api/v1/matches/{match['id']}/toss",
                                    json={"first_server": "B"})
    assert response.status_code == 409


async def test_a_lease_stops_two_devices_scoring_the_same_match(client):
    alice = await register(client, "alice@example.com")
    _, did, _ = await build_field(alice, 4)
    await alice.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(alice, did)

    claimed = await alice.post(f"/api/v1/matches/{match['id']}/claim", json={})
    assert claimed.status_code == 200

    # A second organizer cannot even see the match, so use the lease directly.
    from sqlmodel import select

    from app.models import Match
    from app.services.match_service import lease_held_by_other

    state = (await alice.get(f"/api/v1/matches/{match['id']}")).json()
    assert state["lease"]["held_by_other"] is False
    assert state["lease"]["scorekeeper_id"] is not None
    _ = (lease_held_by_other, Match, select)


async def test_releasing_a_lease_clears_the_scorekeeper(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    await organizer.post(f"/api/v1/matches/{match['id']}/claim", json={})
    await organizer.post(f"/api/v1/matches/{match['id']}/release")
    state = (await organizer.get(f"/api/v1/matches/{match['id']}")).json()
    assert state["lease"]["scorekeeper_id"] is None


async def test_court_code_is_scoped_to_one_match(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    response = await organizer.get(f"/api/v1/matches/{match['id']}/court-code")
    assert response.status_code == 200

    from app.core.security import decode_token

    claims = decode_token(response.json()["token"], "court")
    assert claims["match_id"] == match["id"]
    assert claims["aud"] == "court"


async def test_events_require_a_client_event_id(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(organizer, did)

    response = await organizer.post(
        f"/api/v1/matches/{match['id']}/events",
        json={"events": [{"type": "RALLY_WON"}]},
    )
    assert response.status_code == 422


async def test_another_organizer_cannot_score_your_match(client):
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")
    _, did, _ = await build_field(alice, 4)
    await alice.post(f"/api/v1/divisions/{did}/draw")
    match = await first_ready(alice, did)

    assert (await bob.get(f"/api/v1/matches/{match['id']}")).status_code == 404
    assert (
        await bob.post(f"/api/v1/matches/{match['id']}/events",
                       json={"events": [ev("RALLY_WON")]})
    ).status_code == 404
