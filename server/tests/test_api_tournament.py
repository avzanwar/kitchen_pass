"""Tournament, court, player, division and entry endpoints — including the
isolation rules that stop one organizer seeing another's data."""

from __future__ import annotations

import pytest

from .conftest_api import register


async def make_players(org, names: list[str]) -> list[str]:
    ids = []
    for name in names:
        response = await org.post("/api/v1/players", json={"name": name})
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


async def make_tournament(org, name: str = "Spring Open") -> str:
    response = await org.post("/api/v1/tournaments", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def make_division(org, tid: str, **kw) -> str:
    body = {"name": "4.0 Mixed Doubles", "format": "doubles", **kw}
    response = await org.post(f"/api/v1/tournaments/{tid}/divisions", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


async def test_player_crud(organizer):
    created = await organizer.post(
        "/api/v1/players",
        json={"name": "Ann Lee", "rating": 4.0,
              "avatar": {"type": "emoji", "value": "🏓"}},
    )
    assert created.status_code == 201
    player_id = created.json()["id"]
    assert created.json()["avatar"]["value"] == "🏓"

    listed = await organizer.get("/api/v1/players")
    assert [p["name"] for p in listed.json()] == ["Ann Lee"]

    updated = await organizer.patch(
        f"/api/v1/players/{player_id}", json={"name": "Ann Lee-Smith", "rating": 4.5}
    )
    assert updated.json()["name"] == "Ann Lee-Smith"

    assert (await organizer.delete(f"/api/v1/players/{player_id}")).status_code == 204
    assert (await organizer.get(f"/api/v1/players/{player_id}")).status_code == 404


async def test_players_are_scoped_to_their_owner(client):
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")

    (player_id,) = await make_players(alice, ["Ann"])
    assert (await bob.get("/api/v1/players")).json() == []
    assert (await bob.get(f"/api/v1/players/{player_id}")).status_code == 404
    assert (await bob.delete(f"/api/v1/players/{player_id}")).status_code == 404


async def test_player_search_filters_by_name(organizer):
    await make_players(organizer, ["Ann Lee", "Bob Chen", "Anna Diaz"])
    found = await organizer.get("/api/v1/players", params={"search": "ann"})
    assert sorted(p["name"] for p in found.json()) == ["Ann Lee", "Anna Diaz"]


async def test_rating_is_range_checked(organizer):
    response = await organizer.post(
        "/api/v1/players", json={"name": "Ann", "rating": 99}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tournaments and courts
# ---------------------------------------------------------------------------


async def test_tournament_crud_and_slug_uniqueness(organizer):
    first = await organizer.post("/api/v1/tournaments", json={"name": "Spring Open"})
    second = await organizer.post("/api/v1/tournaments", json={"name": "Spring Open"})
    assert first.json()["slug"] == "spring-open"
    assert second.json()["slug"] == "spring-open-2"
    assert first.json()["public_token"] != second.json()["public_token"]
    assert first.json()["status"] == "draft"


async def test_tournaments_are_scoped_to_their_owner(client):
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")
    tid = await make_tournament(alice)

    assert (await bob.get("/api/v1/tournaments")).json() == []
    assert (await bob.get(f"/api/v1/tournaments/{tid}")).status_code == 404
    assert (
        await bob.patch(f"/api/v1/tournaments/{tid}", json={"name": "Hijacked"})
    ).status_code == 404
    assert (await bob.delete(f"/api/v1/tournaments/{tid}")).status_code == 404


async def test_courts_crud_and_duplicate_names(organizer):
    tid = await make_tournament(organizer)
    assert (
        await organizer.post(f"/api/v1/tournaments/{tid}/courts", json={"name": "Court 1"})
    ).status_code == 201
    dupe = await organizer.post(
        f"/api/v1/tournaments/{tid}/courts", json={"name": "court 1"}
    )
    assert dupe.status_code == 409, "court names must be unique within a tournament"

    await organizer.post(
        f"/api/v1/tournaments/{tid}/courts", json={"name": "Court 2", "sort_order": 2}
    )
    listed = await organizer.get(f"/api/v1/tournaments/{tid}/courts")
    assert [c["name"] for c in listed.json()] == ["Court 1", "Court 2"]


# ---------------------------------------------------------------------------
# Divisions and entries
# ---------------------------------------------------------------------------


async def test_division_defaults(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    division = (await organizer.get(f"/api/v1/divisions/{did}")).json()
    assert division["draw_kind"] == "round_robin"
    assert division["tiebreakers"] == ["head_to_head", "point_diff", "points_allowed"]
    assert division["draw_generated"] is False


async def test_doubles_entry_requires_two_players(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    players = await make_players(organizer, ["Ann", "Bo"])

    too_few = await organizer.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": players[:1]}
    )
    assert too_few.status_code == 422
    assert "needs 2 player" in too_few.json()["detail"]

    ok = await organizer.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": players}
    )
    assert ok.status_code == 201
    assert ok.json()["name"] == "Ann & Bo"
    assert [p["name"] for p in ok.json()["players"]] == ["Ann", "Bo"]


async def test_singles_entry_requires_one_player(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid, format="singles")
    players = await make_players(organizer, ["Ann", "Bo"])

    assert (
        await organizer.post(
            f"/api/v1/divisions/{did}/entries", json={"player_ids": players}
        )
    ).status_code == 422
    assert (
        await organizer.post(
            f"/api/v1/divisions/{did}/entries", json={"player_ids": players[:1]}
        )
    ).status_code == 201


async def test_a_player_cannot_be_entered_twice_in_one_division(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    ann, bo, cy = await make_players(organizer, ["Ann", "Bo", "Cy"])

    await organizer.post(f"/api/v1/divisions/{did}/entries",
                         json={"player_ids": [ann, bo]})
    clash = await organizer.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": [ann, cy]}
    )
    assert clash.status_code == 409, (
        "Ann would be scheduled onto two courts at once"
    )


async def test_an_entry_cannot_pair_a_player_with_themselves(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    (ann,) = await make_players(organizer, ["Ann"])
    response = await organizer.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": [ann, ann]}
    )
    assert response.status_code == 422


async def test_cannot_enter_another_organizers_player(client):
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")
    alice_players = await make_players(alice, ["Ann", "Bo"])

    tid = await make_tournament(bob)
    did = await make_division(bob, tid)
    response = await bob.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": alice_players}
    )
    assert response.status_code == 404


async def test_deleting_a_registered_player_is_refused(organizer):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    ann, bo = await make_players(organizer, ["Ann", "Bo"])
    await organizer.post(f"/api/v1/divisions/{did}/entries",
                         json={"player_ids": [ann, bo]})

    response = await organizer.delete(f"/api/v1/players/{ann}")
    assert response.status_code == 409
    assert "registered in a division" in response.json()["detail"]


@pytest.mark.parametrize("status", ["checked_in", "withdrawn"])
async def test_entry_status_transitions(organizer, status):
    tid = await make_tournament(organizer)
    did = await make_division(organizer, tid)
    ann, bo = await make_players(organizer, ["Ann", "Bo"])
    entry = (
        await organizer.post(
            f"/api/v1/divisions/{did}/entries", json={"player_ids": [ann, bo]}
        )
    ).json()

    response = await organizer.patch(
        f"/api/v1/entries/{entry['id']}/status", json={"status": status}
    )
    assert response.status_code == 200
    assert response.json()["status"] == status
