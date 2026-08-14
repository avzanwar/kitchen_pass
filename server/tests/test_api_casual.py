"""Pickup games: setup, scoring through the normal match endpoints, and the
guards that keep casual data out of the tournament UI.

The load-bearing idea under test is that a casual match is a *real* match. So
most of these drive it through `/matches/{id}/events` — the same endpoint a
tournament match uses — rather than through anything casual-specific.
"""

from __future__ import annotations

import pytest

from .conftest_api import ApiUser, register


async def make_player(org: ApiUser, name: str) -> str:
    response = await org.post("/api/v1/players", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def doubles(a: list[dict], b: list[dict], **kw) -> dict:
    body: dict = {"format": "doubles", "a": {"players": a}, "b": {"players": b}}
    body.update(kw)
    return body


async def start(org: ApiUser, body: dict):
    return await org.post("/api/v1/casual/matches", json=body)


async def play_out(org: ApiUser, match_id: str, tag: str = "t") -> dict:
    """Score the match to completion, always giving the serving team the rally."""
    state = (await org.get(f"/api/v1/matches/{match_id}")).json()
    n = 0
    while state["status"] == "live" and n < 400:
        n += 1
        serving = state["current"]["serving_team"]
        response = await org.post(
            f"/api/v1/matches/{match_id}/events",
            json={"events": [{
                "type": "RALLY_WON" if serving == "A" else "RALLY_LOST",
                "team": None, "client_event_id": f"{tag}-{n}",
            }]},
        )
        assert response.status_code == 200, response.text
        state = response.json()
    return state


@pytest.fixture
async def four(organizer: ApiUser) -> list[str]:
    return [await make_player(organizer, n)
            for n in ("Ivo Novak", "Priya Raman", "Sam Whitfield", "Nina Roth")]


# ---------------------------------------------------------------------------
# Setting one up
# ---------------------------------------------------------------------------


async def test_a_pickup_game_starts_from_saved_players(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["a_name"] == "Ivo & Priya"
    assert body["b_name"] == "Sam & Nina"
    assert body["status"] == "ready"
    assert (body["target"], body["best_of"], body["scoring"]) == (11, 1, "sideout")


async def test_typed_names_start_a_game_without_touching_the_roster(
    organizer: ApiUser,
) -> None:
    response = await start(organizer, doubles(
        [{"name": "Mike"}, {"name": "Dave"}],
        [{"name": "Ann"}, {"name": "Jo"}],
    ))
    assert response.status_code == 201, response.text
    assert response.json()["a_name"] == "Mike & Dave"

    # The roster is "people I play with", not everyone who ever held a paddle.
    assert (await organizer.get("/api/v1/players")).json() == []
    guests = (await organizer.get("/api/v1/players?include_guests=true")).json()
    assert {g["name"] for g in guests} == {"Mike", "Dave", "Ann", "Jo"}
    assert all(g["is_guest"] for g in guests)


async def test_two_players_called_mike_never_merge(organizer: ApiUser) -> None:
    """The prototype bug, pinned.

    `keyOf` at kitchen-pass.jsx:24 fell back to a lowercased name, so two
    players called "Mike" shared one serve-stat bucket. Guests get real ids and
    are never matched by name.
    """
    response = await start(organizer, doubles(
        [{"name": "Mike"}, {"name": "Dave"}],
        [{"name": "Mike"}, {"name": "Jo"}],
    ))
    assert response.status_code == 201, response.text
    body = response.json()

    a_mike = next(p for p in body["a_players"] if p["name"] == "Mike")
    b_mike = next(p for p in body["b_players"] if p["name"] == "Mike")
    assert a_mike["id"] != b_mike["id"]

    # A's Mike serves first at 0-0-2 and wins the rally, so the serve point is
    # credited to him alone — under the prototype's name-keyed stats both Mikes
    # would have shared the bucket.
    state = (await organizer.post(
        f"/api/v1/matches/{body['match_id']}/events",
        json={"events": [{"type": "RALLY_WON", "team": None, "client_event_id": "m1"}]},
    )).json()
    assert state["current"]["server_id"] == a_mike["id"]
    assert state["serve_points"] == {a_mike["id"]: 1}
    assert b_mike["id"] not in state["serve_points"]


async def test_a_saved_player_and_a_guest_can_share_a_team(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, doubles(
        [{"player_id": four[0]}, {"name": "Mike"}],
        [{"player_id": four[2]}, {"name": "Dave"}],
    ))
    assert response.status_code == 201, response.text
    assert response.json()["a_name"] == "Ivo & Mike"
    assert len((await organizer.get("/api/v1/players")).json()) == 4


async def test_singles_takes_one_player_a_side(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, {
        "format": "singles",
        "a": {"players": [{"player_id": four[0]}]},
        "b": {"players": [{"player_id": four[2]}]},
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "singles"
    assert (len(body["a_players"]), len(body["b_players"])) == (1, 1)


async def test_doubles_with_one_player_a_side_is_refused(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, doubles(
        [{"player_id": four[0]}], [{"player_id": four[2]}],
    ))
    assert response.status_code == 422
    assert "2 player" in response.json()["detail"]


async def test_a_player_cannot_be_on_both_teams(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[0]}, {"player_id": four[3]}],
    ))
    assert response.status_code == 422
    assert "both teams" in response.json()["detail"]


async def test_a_slot_needs_a_name_or_an_id_but_not_both(
    organizer: ApiUser, four: list[str]
) -> None:
    for slot in ({"player_id": four[0], "name": "Mike"}, {}):
        response = await start(organizer, doubles(
            [slot, {"name": "Dave"}], [{"name": "Ann"}, {"name": "Jo"}],
        ))
        assert response.status_code == 422, response.text


async def test_someone_elses_player_is_not_usable(
    organizer: ApiUser, four: list[str], client
) -> None:
    intruder = await register(client, "intruder@example.com")
    response = await start(intruder, doubles(
        [{"player_id": four[0]}, {"name": "Dave"}],
        [{"name": "Ann"}, {"name": "Jo"}],
    ))
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Scoring — through the ordinary match endpoints
# ---------------------------------------------------------------------------


async def test_a_pickup_game_scores_and_completes(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))).json()
    match_id = body["match_id"]

    state = (await organizer.get(f"/api/v1/matches/{match_id}")).json()
    assert state["current"]["call"] == "0-0-2"
    assert state["current"]["side"] == "R"
    assert state["teams"]["A"]["name"] == "Ivo & Priya"

    final = await play_out(organizer, match_id)
    assert final["status"] == "complete"
    assert final["winner"] == "A"
    assert final["games_won"]["A"] == 1


async def test_undo_works_on_a_pickup_game(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))).json()
    match_id = body["match_id"]

    scored = await organizer.post(
        f"/api/v1/matches/{match_id}/events",
        json={"events": [{"type": "RALLY_WON", "team": None, "client_event_id": "u1"}]},
    )
    assert scored.json()["current"]["score"]["A"] == 1

    undone = await organizer.post(
        f"/api/v1/matches/{match_id}/events",
        json={"events": [{"type": "UNDO", "team": None, "client_event_id": "u2"}]},
    )
    assert undone.json()["current"]["score"]["A"] == 0


async def test_the_coin_toss_decides_who_serves_first(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
        first_server="B",
    ))).json()
    state = (await organizer.get(f"/api/v1/matches/{body['match_id']}")).json()
    assert state["current"]["serving_team"] == "B"


async def test_best_of_three_needs_two_games(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
        best_of=3, target=11,
    ))).json()
    assert body["best_of"] == 3

    final = await play_out(organizer, body["match_id"])
    assert final["status"] == "complete"
    assert final["games_won"]["A"] == 2
    # One target for the whole match — no third-game-to-15 convention here.
    assert all(g["target"] == 11 for g in final["games"])


async def test_rally_scoring_gives_the_receiver_a_point(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
        scoring="rally", target=21,
    ))).json()
    assert (body["scoring"], body["target"]) == ("rally", 21)

    # Under side-out this would be a side out with no point; under rally the
    # receiving team scores.
    state = (await organizer.post(
        f"/api/v1/matches/{body['match_id']}/events",
        json={"events": [{"type": "RALLY_LOST", "team": None, "client_event_id": "r1"}]},
    )).json()
    assert state["current"]["score"]["B"] == 1


async def test_freeze_at_is_refused_without_rally_scoring(
    organizer: ApiUser, four: list[str]
) -> None:
    response = await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
        freeze_at=10,
    ))
    assert response.status_code == 422
    assert "rally" in response.json()["detail"]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_finished_games_are_listed_newest_first(
    organizer: ApiUser, four: list[str]
) -> None:
    first = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))).json()
    await play_out(organizer, first["match_id"], tag="one")
    second = (await start(organizer, doubles(
        [{"name": "Mike"}, {"name": "Dave"}], [{"name": "Ann"}, {"name": "Jo"}],
    ))).json()

    listed = (await organizer.get("/api/v1/casual/matches")).json()
    assert [m["match_id"] for m in listed] == [second["match_id"], first["match_id"]]
    assert listed[1]["winner"] == "A"
    assert listed[1]["games"][0]["a"] == 11
    assert listed[0]["status"] == "ready"


async def test_one_organizers_pickup_games_are_private(
    organizer: ApiUser, four: list[str], client
) -> None:
    await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))
    intruder = await register(client, "intruder@example.com")
    assert (await intruder.get("/api/v1/casual/matches")).json() == []


# ---------------------------------------------------------------------------
# The guards — each one is the whole reason "hidden" is true
# ---------------------------------------------------------------------------


async def test_the_casual_container_never_appears_as_a_tournament(
    organizer: ApiUser, four: list[str]
) -> None:
    await start(organizer, doubles(
        [{"player_id": four[0]}, {"player_id": four[1]}],
        [{"player_id": four[2]}, {"player_id": four[3]}],
    ))
    assert (await organizer.get("/api/v1/tournaments")).json() == []

    real = await organizer.post("/api/v1/tournaments", json={"name": "Spring Open"})
    listed = (await organizer.get("/api/v1/tournaments")).json()
    assert [t["id"] for t in listed] == [real.json()["id"]]


async def test_many_games_reuse_one_container(
    organizer: ApiUser, four: list[str]
) -> None:
    for _ in range(3):
        response = await start(organizer, doubles(
            [{"name": "Mike"}, {"name": "Dave"}], [{"name": "Ann"}, {"name": "Jo"}],
        ))
        assert response.status_code == 201, response.text
    assert len((await organizer.get("/api/v1/casual/matches")).json()) == 3
    assert (await organizer.get("/api/v1/tournaments")).json() == []


async def test_a_bulk_import_never_matches_a_guest_by_name(
    organizer: ApiUser,
) -> None:
    """Guard 3: a stranger from Tuesday's pickup game must not silently become a
    member of a tournament team because the names spell the same."""
    await start(organizer, doubles(
        [{"name": "Mike Jones"}, {"name": "Dave"}],
        [{"name": "Ann"}, {"name": "Jo"}],
    ))
    guests = (await organizer.get("/api/v1/players?include_guests=true")).json()
    guest_mike = next(g for g in guests if g["name"] == "Mike Jones")

    sheet = "Division,Player 1,Player 2\n4.0 Mixed,Mike Jones,Nina Roth\n"
    response = await organizer.post(
        "/api/v1/imports/commit",
        files={"file": ("teams.csv", sheet.encode(), "text/csv")},
        data={"tournament_name": "Spring Open"},
    )
    assert response.status_code == 201, response.text
    # A brand new roster player, not the guest.
    assert response.json()["players_created"] == 2
    assert response.json()["players_matched"] == 0

    roster = (await organizer.get("/api/v1/players")).json()
    assert {p["name"] for p in roster} == {"Mike Jones", "Nina Roth"}
    assert guest_mike["id"] not in {p["id"] for p in roster}


async def test_guests_stay_out_of_the_tournament_entry_picker(
    organizer: ApiUser,
) -> None:
    await start(organizer, doubles(
        [{"name": "Mike"}, {"name": "Dave"}], [{"name": "Ann"}, {"name": "Jo"}],
    ))
    ivo = await make_player(organizer, "Ivo Novak")
    roster = (await organizer.get("/api/v1/players")).json()
    assert [p["id"] for p in roster] == [ivo]


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def test_deleting_a_game_takes_its_guests_with_it(
    organizer: ApiUser, four: list[str]
) -> None:
    body = (await start(organizer, doubles(
        [{"player_id": four[0]}, {"name": "Mike"}],
        [{"player_id": four[2]}, {"name": "Dave"}],
    ))).json()
    await play_out(organizer, body["match_id"])

    response = await organizer.delete(f"/api/v1/casual/matches/{body['match_id']}")
    assert response.status_code == 204

    assert (await organizer.get("/api/v1/casual/matches")).json() == []
    # The guests go; the saved players stay.
    assert (await organizer.get("/api/v1/players?include_guests=true")).json() != []
    everyone = (await organizer.get("/api/v1/players?include_guests=true")).json()
    assert {p["name"] for p in everyone} == {
        "Ivo Novak", "Priya Raman", "Sam Whitfield", "Nina Roth",
    }
    assert (await organizer.get(f"/api/v1/matches/{body['match_id']}")).status_code == 404


async def test_a_guest_still_playing_elsewhere_is_kept(
    organizer: ApiUser,
) -> None:
    first = (await start(organizer, doubles(
        [{"name": "Mike"}, {"name": "Dave"}], [{"name": "Ann"}, {"name": "Jo"}],
    ))).json()
    guests = (await organizer.get("/api/v1/players?include_guests=true")).json()
    mike = next(g for g in guests if g["name"] == "Mike")

    # Re-picking the same guest is how you say "the same person".
    await start(organizer, doubles(
        [{"player_id": mike["id"]}, {"name": "Sue"}],
        [{"name": "Pat"}, {"name": "Kim"}],
    ))
    await organizer.delete(f"/api/v1/casual/matches/{first['match_id']}")

    left = {g["name"] for g in
            (await organizer.get("/api/v1/players?include_guests=true")).json()}
    assert "Mike" in left
    assert "Dave" not in left


async def test_a_tournament_match_cannot_be_deleted_as_a_pickup_game(
    organizer: ApiUser, four: list[str]
) -> None:
    tournament = (await organizer.post(
        "/api/v1/tournaments", json={"name": "Spring Open"})).json()
    division = (await organizer.post(
        f"/api/v1/tournaments/{tournament['id']}/divisions",
        json={"name": "4.0", "format": "doubles", "draw_kind": "round_robin"},
    )).json()
    for pair in ((four[0], four[1]), (four[2], four[3])):
        await organizer.post(
            f"/api/v1/divisions/{division['id']}/entries",
            json={"player_ids": list(pair)},
        )
    draw = (await organizer.post(f"/api/v1/divisions/{division['id']}/draw")).json()
    match_id = draw["matches"][0]["id"]

    response = await organizer.delete(f"/api/v1/casual/matches/{match_id}")
    assert response.status_code == 404
    assert (await organizer.get(f"/api/v1/matches/{match_id}")).status_code == 200
