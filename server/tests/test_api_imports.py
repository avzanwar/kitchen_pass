"""The bulk import endpoints, end to end against a real database.

What the pure planner tests cannot cover: matching names against an existing
roster, adding to a division that already exists, and the all-or-nothing
guarantee on commit.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from .conftest_api import ApiUser, register

DOUBLES_SHEET = (
    "Division,Format,Draw,Player 1,Player 2\n"
    "4.0 Mixed,mixed,round robin,Ivo Novak,Priya Raman\n"
    "4.0 Mixed,,,Sam Whitfield,Nina Roth\n"
    "4.0 Mixed,,,Toby Chen,Grace Lim\n"
    "Open Singles,singles,single elim,Ivo Novak,\n"
    "Open Singles,,,Sam Whitfield,\n"
)


def upload(text: str, name: str = "teams.csv") -> dict:
    return {"file": (name, text.encode(), "text/csv")}


async def preview(user: ApiUser, text: str, **data: str):
    return await user.post("/api/v1/imports/preview", files=upload(text), data=data)


async def commit(user: ApiUser, text: str, **data: str):
    return await user.post("/api/v1/imports/commit", files=upload(text), data=data)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


async def test_the_csv_template_downloads_without_a_login(client: AsyncClient) -> None:
    # A plain <a download> link cannot carry a bearer token, and the template
    # contains no user data, so it is deliberately public.
    response = await client.get("/api/v1/imports/template.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0].startswith("Division,Format,Draw")


async def test_the_xlsx_template_downloads(client: AsyncClient) -> None:
    response = await client.get("/api/v1/imports/template.xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert "spreadsheetml" in response.headers["content-type"]


async def test_the_downloaded_template_imports_as_a_working_tournament(
    organizer: ApiUser,
) -> None:
    # The template doubles as a demo: download it, upload it, get an event.
    template = await organizer.get("/api/v1/imports/template.csv")
    response = await commit(organizer, template.text, tournament_name="From Template")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["divisions_created"] == 3
    assert body["entries_created"] == 12
    assert body["players_created"] == 11


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


async def test_preview_reports_what_would_be_created(organizer: ApiUser) -> None:
    response = await preview(organizer, DOUBLES_SHEET, tournament_name="Spring Open")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["ok"] is True
    assert body["creates_tournament"] is True
    assert body["tournament_name"] == "Spring Open"
    assert body["entry_count"] == 5
    assert body["new_players"] == 6
    assert body["matched_players"] == 0
    assert [d["name"] for d in body["divisions"]] == ["4.0 Mixed", "Open Singles"]
    assert body["divisions"][0]["draw_kind"] == "round_robin"
    assert body["divisions"][1]["format"] == "singles"


async def test_preview_writes_nothing(organizer: ApiUser) -> None:
    await preview(organizer, DOUBLES_SHEET, tournament_name="Spring Open")
    assert (await organizer.get("/api/v1/tournaments")).json() == []
    assert (await organizer.get("/api/v1/players")).json() == []


async def test_preview_matches_names_against_the_existing_roster(
    organizer: ApiUser,
) -> None:
    await organizer.post("/api/v1/players", json={"name": "Ivo Novak"})
    response = await preview(organizer, DOUBLES_SHEET, tournament_name="Spring Open")
    body = response.json()

    assert body["matched_players"] == 1
    assert body["new_players"] == 5
    first = body["divisions"][0]["entries"][0]
    assert first["players"][0]["existing"] is True
    assert first["players"][1]["existing"] is False


async def test_preview_reports_problems_with_row_numbers(organizer: ApiUser) -> None:
    response = await preview(
        organizer,
        "Division,Player 1,Player 2\n4.0,Ivo,Priya\n4.0,Sam,\n",
        tournament_name="Spring Open",
    )
    body = response.json()
    assert body["ok"] is False
    errors = [p for p in body["problems"] if p["severity"] == "error"]
    assert errors and errors[0]["row"] == 3


async def test_an_unreadable_file_is_rejected(organizer: ApiUser) -> None:
    response = await preview(organizer, "just some prose\nwith no header\n")
    assert response.status_code == 422
    assert "No header row" in response.json()["detail"]


async def test_an_empty_upload_is_rejected(organizer: ApiUser) -> None:
    response = await organizer.post(
        "/api/v1/imports/preview", files={"file": ("empty.csv", b"", "text/csv")}
    )
    assert response.status_code == 422


async def test_importing_needs_a_login(client: AsyncClient) -> None:
    response = await client.post("/api/v1/imports/preview", files=upload(DOUBLES_SHEET))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


async def test_commit_creates_the_whole_tournament(organizer: ApiUser) -> None:
    response = await commit(organizer, DOUBLES_SHEET, tournament_name="Spring Open")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["tournament_created"] is True
    assert body["tournament"]["name"] == "Spring Open"
    assert body["tournament"]["slug"] == "spring-open"
    assert (body["divisions_created"], body["entries_created"]) == (2, 5)
    assert (body["players_created"], body["players_matched"]) == (6, 0)

    tournament_id = body["tournament"]["id"]
    divisions = (
        await organizer.get(f"/api/v1/tournaments/{tournament_id}/divisions")
    ).json()
    assert {d["name"] for d in divisions} == {"4.0 Mixed", "Open Singles"}

    mixed = next(d for d in divisions if d["name"] == "4.0 Mixed")
    entries = (await organizer.get(f"/api/v1/divisions/{mixed['id']}/entries")).json()
    assert [e["name"] for e in entries] == ["Ivo & Priya", "Sam & Nina", "Toby & Grace"]
    assert [p["name"] for p in entries[0]["players"]] == ["Ivo Novak", "Priya Raman"]


async def test_the_imported_field_generates_a_playable_draw(
    organizer: ApiUser,
) -> None:
    # The point of the import is a runnable event, not just rows in a table.
    body = (await commit(organizer, DOUBLES_SHEET, tournament_name="Spring")).json()
    divisions = (
        await organizer.get(
            f"/api/v1/tournaments/{body['tournament']['id']}/divisions"
        )
    ).json()
    mixed = next(d for d in divisions if d["name"] == "4.0 Mixed")

    draw = await organizer.post(f"/api/v1/divisions/{mixed['id']}/draw")
    assert draw.status_code == 201, draw.text
    matches = draw.json()["matches"]
    assert len(matches) == 3  # three teams, round robin
    assert all(m["status"] == "ready" for m in matches)


async def test_a_player_in_two_divisions_becomes_one_roster_entry(
    organizer: ApiUser,
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_name="Spring Open")
    players = (await organizer.get("/api/v1/players")).json()
    assert [p["name"] for p in players].count("Ivo Novak") == 1


async def test_names_match_the_roster_case_insensitively(organizer: ApiUser) -> None:
    created = await organizer.post(
        "/api/v1/players", json={"name": "Ivo Novak", "rating": 4.5}
    )
    existing_id = created.json()["id"]

    await commit(
        organizer,
        "Division,Player 1,Player 2\n4.0,IVO NOVAK,Nina Roth\n",
        tournament_name="Spring Open",
    )
    players = (await organizer.get("/api/v1/players")).json()
    assert len(players) == 2
    # The existing row is reused, not shadowed by a second "IVO NOVAK".
    assert existing_id in {p["id"] for p in players}
    assert next(p for p in players if p["id"] == existing_id)["name"] == "Ivo Novak"


async def test_an_import_never_overwrites_a_curated_rating(
    organizer: ApiUser,
) -> None:
    created = await organizer.post(
        "/api/v1/players", json={"name": "Ivo Novak", "rating": 4.5}
    )
    await commit(
        organizer,
        "Division,Player 1,Rating 1,Player 2\n4.0,Ivo Novak,2.0,Nina Roth\n",
        tournament_name="Spring Open",
    )
    player = (await organizer.get(f"/api/v1/players/{created.json()['id']}")).json()
    assert player["rating"] == 4.5


async def test_ratings_are_written_onto_players_the_import_creates(
    organizer: ApiUser,
) -> None:
    await commit(
        organizer,
        "Division,Player 1,Rating 1,Player 2,Rating 2\n4.0,Ivo,4.25,Nina,3.5\n",
        tournament_name="Spring Open",
    )
    players = {p["name"]: p["rating"] for p in (await organizer.get("/api/v1/players")).json()}
    assert players == {"Ivo": 4.25, "Nina": 3.5}


async def test_seeds_and_team_names_survive_the_round_trip(
    organizer: ApiUser,
) -> None:
    body = (
        await commit(
            organizer,
            "Division,Team,Seed,Player 1,Player 2\n"
            "4.0,Kitchen Bandits,2,Ivo,Priya\n"
            "4.0,Dink Dynasty,1,Sam,Nina\n",
            tournament_name="Spring Open",
        )
    ).json()
    divisions = (
        await organizer.get(
            f"/api/v1/tournaments/{body['tournament']['id']}/divisions"
        )
    ).json()
    entries = (
        await organizer.get(f"/api/v1/divisions/{divisions[0]['id']}/entries")
    ).json()
    # Listed in seed order.
    assert [(e["name"], e["seed"]) for e in entries] == [
        ("Dink Dynasty", 1), ("Kitchen Bandits", 2),
    ]


# ---------------------------------------------------------------------------
# All-or-nothing
# ---------------------------------------------------------------------------


async def test_a_sheet_with_errors_creates_absolutely_nothing(
    organizer: ApiUser,
) -> None:
    response = await commit(
        organizer,
        "Division,Player 1,Player 2\n"
        "4.0,Ivo,Priya\n"
        "4.0,Sam,Nina\n"
        "Open,Toby,\n",  # doubles by default, no partner
        tournament_name="Spring Open",
    )
    assert response.status_code == 422

    # A half-created tournament is worse than a rejected file: the organizer
    # cannot tell what is missing without auditing every row by hand.
    assert (await organizer.get("/api/v1/tournaments")).json() == []
    assert (await organizer.get("/api/v1/players")).json() == []


async def test_a_rejection_carries_the_full_preview_back(organizer: ApiUser) -> None:
    response = await commit(
        organizer,
        "Division,Player 1,Player 2\n4.0,Ivo,Priya\n4.0,Sam,\n",
        tournament_name="Spring Open",
    )
    detail = response.json()["detail"]
    assert "errors" in detail["message"]
    assert detail["preview"]["ok"] is False
    assert any(p["row"] == 3 for p in detail["preview"]["problems"])


async def test_committing_without_a_name_or_a_target_is_refused(
    organizer: ApiUser,
) -> None:
    response = await commit(organizer, DOUBLES_SHEET)
    assert response.status_code == 422
    assert "Name the new tournament" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Importing into an existing tournament
# ---------------------------------------------------------------------------


@pytest.fixture
async def tournament(organizer: ApiUser) -> str:
    response = await organizer.post("/api/v1/tournaments", json={"name": "Club Night"})
    return str(response.json()["id"])


async def test_importing_into_an_existing_tournament(
    organizer: ApiUser, tournament: str
) -> None:
    response = await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["tournament_created"] is False
    assert body["tournament"]["id"] == tournament
    assert body["divisions_created"] == 2

    divisions = (
        await organizer.get(f"/api/v1/tournaments/{tournament}/divisions")
    ).json()
    assert len(divisions) == 2


async def test_a_second_import_adds_teams_to_the_division_it_already_made(
    organizer: ApiUser, tournament: str
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    response = await commit(
        organizer,
        "Division,Format,Player 1,Player 2\n4.0 Mixed,mixed,Alex Moreau,Mia Torres\n",
        tournament_id=tournament,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["divisions_created"], body["divisions_reused"]) == (0, 1)

    divisions = (
        await organizer.get(f"/api/v1/tournaments/{tournament}/divisions")
    ).json()
    mixed = next(d for d in divisions if d["name"] == "4.0 Mixed")
    entries = (await organizer.get(f"/api/v1/divisions/{mixed['id']}/entries")).json()
    assert len(entries) == 4


async def test_re_importing_the_same_sheet_is_refused_not_duplicated(
    organizer: ApiUser, tournament: str
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    response = await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)

    assert response.status_code == 422
    problems = response.json()["detail"]["preview"]["problems"]
    assert any("already registered" in p["message"] for p in problems)


async def test_importing_into_a_division_whose_draw_is_generated_is_refused(
    organizer: ApiUser, tournament: str
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    divisions = (
        await organizer.get(f"/api/v1/tournaments/{tournament}/divisions")
    ).json()
    mixed = next(d for d in divisions if d["name"] == "4.0 Mixed")
    await organizer.post(f"/api/v1/divisions/{mixed['id']}/draw")

    response = await commit(
        organizer,
        "Division,Format,Player 1,Player 2\n4.0 Mixed,mixed,Alex Moreau,Mia Torres\n",
        tournament_id=tournament,
    )
    assert response.status_code == 422
    problems = response.json()["detail"]["preview"]["problems"]
    assert any("draw" in p["message"] for p in problems)


async def test_a_format_clash_with_an_existing_division_is_refused(
    organizer: ApiUser, tournament: str
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    response = await commit(
        organizer,
        "Division,Format,Player 1\nOpen Singles,doubles,Alex Moreau\n",
        tournament_id=tournament,
    )
    assert response.status_code == 422
    problems = response.json()["detail"]["preview"]["problems"]
    assert any("already exists as singles" in p["message"] for p in problems)


async def test_preview_flags_a_division_that_would_be_added_to(
    organizer: ApiUser, tournament: str
) -> None:
    await commit(organizer, DOUBLES_SHEET, tournament_id=tournament)
    response = await preview(
        organizer,
        "Division,Format,Player 1,Player 2\n4.0 Mixed,mixed,Alex Moreau,Mia Torres\n",
        tournament_id=tournament,
    )
    body = response.json()
    assert body["ok"] is True
    assert body["divisions"][0]["existing"] is True
    assert any("will be added to it" in p["message"] for p in body["problems"])


async def test_someone_elses_tournament_is_not_a_valid_target(
    organizer: ApiUser, tournament: str, client: AsyncClient
) -> None:
    intruder = await register(client, "intruder@example.com")
    response = await commit(intruder, DOUBLES_SHEET, tournament_id=tournament)
    assert response.status_code == 404


async def test_one_organizers_roster_is_not_matched_against_anothers(
    organizer: ApiUser, client: AsyncClient
) -> None:
    await organizer.post("/api/v1/players", json={"name": "Ivo Novak"})
    intruder = await register(client, "intruder@example.com")

    body = (
        await preview(intruder, DOUBLES_SHEET, tournament_name="Theirs")
    ).json()
    assert body["matched_players"] == 0
