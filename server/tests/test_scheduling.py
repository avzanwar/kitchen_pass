"""Court assignment and conflict detection."""

from __future__ import annotations

from app.scheduling.assigner import Playable, assign_courts, find_conflicts


def match(mid: str, players: list[str], *, rnd: int = 1, slot: int = 1,
          priority: int = 0) -> Playable:
    return Playable(
        match_id=mid, division_id="d1", round=rnd, slot=slot,
        player_ids=players, priority=priority,
    )


def test_independent_matches_all_play_in_the_first_wave():
    schedule = assign_courts(
        [match("m1", ["p1", "p2"]), match("m2", ["p3", "p4"], slot=2)],
        ["c1", "c2"],
    )
    assert schedule.unplaced == []
    assert {a.wave for a in schedule.assignments} == {0}
    assert {a.court_id for a in schedule.assignments} == {"c1", "c2"}


def test_a_shared_player_forces_a_later_wave():
    """The bug the prototype had: the same person on two courts at once."""
    schedule = assign_courts(
        [match("m1", ["p1", "p2"]), match("m2", ["p1", "p3"], slot=2)],
        ["c1", "c2"],
        rest_waves=0,
    )
    waves = {a.match_id: a.wave for a in schedule.assignments}
    assert schedule.unplaced == []
    assert waves["m1"] != waves["m2"], "p1 cannot be on two courts simultaneously"


def test_no_player_is_ever_double_booked():
    matches = [
        match("m1", ["p1", "p2"], slot=1),
        match("m2", ["p2", "p3"], slot=2),
        match("m3", ["p3", "p4"], slot=3),
        match("m4", ["p4", "p1"], slot=4),
        match("m5", ["p5", "p6"], slot=5),
    ]
    schedule = assign_courts(matches, ["c1", "c2", "c3"], rest_waves=0)
    assert schedule.unplaced == []

    by_wave: dict[int, list[str]] = {}
    lookup = {m.match_id: m for m in matches}
    for a in schedule.assignments:
        by_wave.setdefault(a.wave, []).extend(lookup[a.match_id].player_ids)
    for wave, players in by_wave.items():
        assert len(players) == len(set(players)), f"double booking in wave {wave}"


def test_courts_are_never_double_booked():
    matches = [match(f"m{i}", [f"p{i}a", f"p{i}b"], slot=i) for i in range(1, 8)]
    schedule = assign_courts(matches, ["c1", "c2"])
    seen: set[tuple[int, str]] = set()
    for a in schedule.assignments:
        key = (a.wave, a.court_id)
        assert key not in seen, f"court {a.court_id} booked twice in wave {a.wave}"
        seen.add(key)


def test_rest_waves_prevent_back_to_back_matches():
    schedule = assign_courts(
        [match("m1", ["p1", "p2"]), match("m2", ["p1", "p3"], slot=2)],
        ["c1", "c2"],
        rest_waves=1,
    )
    waves = {a.match_id: a.wave for a in schedule.assignments}
    assert waves["m2"] - waves["m1"] >= 2, "p1 gets a wave off between matches"


def test_every_match_is_scheduled_exactly_once():
    matches = [match(f"m{i}", [f"p{i}", f"q{i}"], slot=i) for i in range(1, 13)]
    schedule = assign_courts(matches, ["c1", "c2", "c3"])
    assert schedule.unplaced == []
    assert sorted(a.match_id for a in schedule.assignments) == sorted(
        m.match_id for m in matches
    )


def test_priority_puts_pool_play_before_playoffs():
    schedule = assign_courts(
        [
            match("playoff", ["p1", "p2"], priority=1),
            match("pool", ["p3", "p4"], priority=0),
        ],
        ["c1"],
    )
    order = sorted(schedule.assignments, key=lambda a: a.wave)
    assert order[0].match_id == "pool"


def test_no_courts_reports_every_match_as_unplaced():
    schedule = assign_courts([match("m1", ["p1", "p2"])], [])
    assert schedule.assignments == []
    assert [c.reason for c in schedule.unplaced] == ["no courts available"]


def test_live_matches_hold_their_court_and_players():
    schedule = assign_courts(
        [match("m1", ["p1", "p2"]), match("m2", ["p9", "p8"], slot=2)],
        ["c1", "c2"],
        busy_courts=["c1"],
        busy_players=["p1"],
    )
    assert all(a.court_id == "c2" for a in schedule.assignments)
    waves = {a.match_id: a.wave for a in schedule.assignments}
    assert waves["m2"] == 0, "an unaffected match still goes on now"
    assert waves["m1"] >= 1, "p1 is mid-match and cannot start another"


def test_all_courts_busy_reports_unplaced():
    schedule = assign_courts(
        [match("m1", ["p1", "p2"])], ["c1"], busy_courts=["c1"]
    )
    assert schedule.assignments == []
    assert schedule.unplaced[0].reason == "every court is in use"


def test_conflicts_are_reported_rather_than_swallowed():
    conflicts = find_conflicts(
        [
            match("m1", ["p1", "p2"]),
            match("m2", ["p1", "p3"], slot=2),
            match("m3", ["p4", "p5"], slot=3),
        ]
    )
    assert len(conflicts) == 1
    assert conflicts[0].player_ids == ["p1"]
    assert "2 ready matches" in conflicts[0].reason


def test_no_conflicts_when_everyone_plays_once():
    assert find_conflicts(
        [match("m1", ["p1", "p2"]), match("m2", ["p3", "p4"], slot=2)]
    ) == []


def test_scheduling_is_deterministic():
    matches = [match(f"m{i}", [f"p{i % 4}", f"q{i}"], slot=i) for i in range(1, 10)]
    first = assign_courts(matches, ["c1", "c2"])
    second = assign_courts(matches, ["c1", "c2"])
    assert first.model_dump() == second.model_dump()


def test_wave_cap_reports_leftovers_instead_of_looping_forever():
    # Everyone shares a player, so only one match can run per wave.
    matches = [match(f"m{i}", ["shared", f"p{i}"], slot=i) for i in range(1, 8)]
    schedule = assign_courts(matches, ["c1"], max_waves=3, rest_waves=0)
    assert len(schedule.assignments) == 3
    assert len(schedule.unplaced) == 4
    assert all(c.reason == "ran out of scheduling waves" for c in schedule.unplaced)
