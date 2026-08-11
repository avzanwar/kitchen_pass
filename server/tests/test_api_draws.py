"""Draw generation through the API — the seam between the pure draw engine and
the database."""

from __future__ import annotations

from .conftest_api import register
from .test_api_tournament import make_division, make_players, make_tournament


async def build_field(org, size: int, *, fmt: str = "doubles", **division_kw):
    """A tournament with one division and `size` entries, seeded 1..size."""
    tid = await make_tournament(org)
    did = await make_division(org, tid, format=fmt, **division_kw)

    per_entry = 1 if fmt == "singles" else 2
    names = [f"P{i}" for i in range(1, size * per_entry + 1)]
    player_ids = await make_players(org, names)

    entries = []
    for i in range(size):
        chunk = player_ids[i * per_entry:(i + 1) * per_entry]
        response = await org.post(
            f"/api/v1/divisions/{did}/entries",
            json={"player_ids": chunk, "name": f"T{i + 1}", "seed": i + 1},
        )
        assert response.status_code == 201, response.text
        entries.append(response.json())
    return tid, did, entries


async def test_round_robin_draw_is_generated_and_persisted(organizer):
    _, did, entries = await build_field(organizer, 4)

    created = await organizer.post(f"/api/v1/divisions/{did}/draw")
    assert created.status_code == 201, created.text
    draw = created.json()
    assert len(draw["matches"]) == 6, "4 entries -> 6 round-robin matches"
    assert all(m["status"] == "ready" for m in draw["matches"])

    # It survives a round trip through the database.
    fetched = await organizer.get(f"/api/v1/divisions/{did}/draw")
    assert fetched.status_code == 200
    assert len(fetched.json()["matches"]) == 6
    assert {m["entry_a_name"] for m in fetched.json()["matches"]} <= {
        e["name"] for e in entries
    }

    division = (await organizer.get(f"/api/v1/divisions/{did}")).json()
    assert division["draw_generated"] is True


async def test_single_elimination_byes_resolve_on_generation(organizer):
    _, did, _ = await build_field(
        organizer, 5, draw_kind="single_elimination"
    )
    draw = (await organizer.post(f"/api/v1/divisions/{did}/draw")).json()

    byes = [m for m in draw["matches"] if m["status"] == "bye"]
    assert len(byes) == 3, "8-slot bracket with 5 entries"
    assert all(m["winner_entry_id"] for m in byes)
    # The top seed got a bye and is already through.
    assert "T1" in {m["entry_a_name"] or m["entry_b_name"] for m in byes}

    ready = {m["draw_match_id"] for m in draw["matches"] if m["status"] == "ready"}
    # W-R1-M2 is the only contested first-round match (T4 v T5). W-R2-M2 is also
    # playable straight away: both of its feeders were byes, so T2 v T3 is known
    # before anyone has hit a ball.
    assert ready == {"W-R1-M2", "W-R2-M2"}


async def test_unresolved_slots_are_labelled_for_display(organizer):
    _, did, _ = await build_field(organizer, 4, draw_kind="single_elimination")
    draw = (await organizer.post(f"/api/v1/divisions/{did}/draw")).json()
    final = next(m for m in draw["matches"] if m["decides_title"])
    assert final["a_label"].startswith("winner of")
    assert final["status"] == "pending"


async def test_pool_playoff_draw_labels_pool_qualifiers(organizer):
    _, did, _ = await build_field(
        organizer, 8, draw_kind="pool_playoff",
        draw_config={"pool_count": 2, "advance_per_pool": 2},
    )
    draw = (await organizer.post(f"/api/v1/divisions/{did}/draw")).json()
    assert set(draw["pools"]) == {"A", "B"}

    semis = [m for m in draw["matches"] if m["bracket"] == "winners"
             and m["a_label"] in {"A1", "B1"}]
    assert {(m["a_label"], m["b_label"]) for m in semis} == {("A1", "B2"), ("B1", "A2")}


async def test_double_elimination_draw_persists_the_reset_match(organizer):
    _, did, _ = await build_field(organizer, 4, draw_kind="double_elimination")
    draw = (await organizer.post(f"/api/v1/divisions/{did}/draw")).json()
    ids = {m["draw_match_id"] for m in draw["matches"]}
    assert {"GF", "GF2"} <= ids
    reset = next(m for m in draw["matches"] if m["draw_match_id"] == "GF2")
    assert reset["decides_title"] is True


async def test_a_draw_needs_at_least_two_entries(organizer):
    _, did, _ = await build_field(organizer, 1)
    response = await organizer.post(f"/api/v1/divisions/{did}/draw")
    assert response.status_code == 409
    assert "at least two active entries" in response.json()["detail"]


async def test_regenerating_without_replace_is_refused(organizer):
    _, did, _ = await build_field(organizer, 4)
    assert (await organizer.post(f"/api/v1/divisions/{did}/draw")).status_code == 201

    again = await organizer.post(f"/api/v1/divisions/{did}/draw")
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]

    replaced = await organizer.post(
        f"/api/v1/divisions/{did}/draw", params={"replace": "true"}
    )
    assert replaced.status_code == 201
    assert len(replaced.json()["matches"]) == 6


async def test_entries_are_locked_once_the_draw_exists(organizer):
    _, did, _ = await build_field(organizer, 4)
    await organizer.post(f"/api/v1/divisions/{did}/draw")

    extra = await make_players(organizer, ["Late1", "Late2"])
    response = await organizer.post(
        f"/api/v1/divisions/{did}/entries", json={"player_ids": extra}
    )
    assert response.status_code == 409
    assert "draw has been generated" in response.json()["detail"]


async def test_withdrawn_entries_are_excluded_from_the_draw(organizer):
    _, did, entries = await build_field(organizer, 5)
    await organizer.patch(
        f"/api/v1/entries/{entries[-1]['id']}/status", json={"status": "withdrawn"}
    )

    draw = (await organizer.post(f"/api/v1/divisions/{did}/draw")).json()
    names = {m["entry_a_name"] for m in draw["matches"]} | {
        m["entry_b_name"] for m in draw["matches"]
    }
    assert "T5" not in names
    assert len(draw["matches"]) == 6, "a 4-team round robin, not 5"


async def test_standings_start_empty_and_are_shaped_per_pool(organizer):
    _, did, _ = await build_field(
        organizer, 8, draw_kind="pool_playoff",
        draw_config={"pool_count": 2, "advance_per_pool": 2},
    )
    await organizer.post(f"/api/v1/divisions/{did}/draw")

    standings = (await organizer.get(f"/api/v1/divisions/{did}/standings")).json()
    assert [t["pool"] for t in standings] == ["A", "B"]
    assert all(len(t["rows"]) == 4 for t in standings)
    assert all(row["played"] == 0 for t in standings for row in t["rows"])
    assert all(row["entry_name"].startswith("T") for t in standings for row in t["rows"])


async def test_another_organizer_cannot_read_or_generate_the_draw(client):
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")
    _, did, _ = await build_field(alice, 4)

    assert (await bob.post(f"/api/v1/divisions/{did}/draw")).status_code == 404
    assert (await bob.get(f"/api/v1/divisions/{did}/draw")).status_code == 404
    assert (await bob.get(f"/api/v1/divisions/{did}/standings")).status_code == 404
