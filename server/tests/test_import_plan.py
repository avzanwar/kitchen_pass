"""The rules about what a valid entry sheet means.

The dividing line under test throughout: an `error` blocks the import, a
`warning` states an assumption and carries on. Getting that split wrong is the
difference between a tool that saves an organizer an hour and one that hands
back a wall of red over a stray cell.
"""

from __future__ import annotations

import pytest

from app.imports import build_plan, read_sheet


def plan_for(text: str):
    return build_plan(read_sheet(text.encode(), "teams.csv"))


def errors(plan) -> list[str]:
    return [p.message for p in plan.problems if p.severity == "error"]


def warnings(plan) -> list[str]:
    return [p.message for p in plan.problems if p.severity == "warning"]


DOUBLES = (
    "Division,Player 1,Player 2\n"
    "4.0 Mixed,Ivo Novak,Priya Raman\n"
    "4.0 Mixed,Sam Whitfield,Nina Roth\n"
)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_rows_sharing_a_division_name_become_one_division() -> None:
    plan = plan_for(DOUBLES)
    assert plan.ok
    assert len(plan.divisions) == 1
    assert len(plan.divisions[0].entries) == 2


def test_division_names_group_case_insensitively() -> None:
    plan = plan_for(
        "Division,Player 1,Player 2\n"
        "4.0 Mixed,Ivo,Priya\n"
        "4.0 MIXED,Sam,Nina\n"
    )
    assert len(plan.divisions) == 1
    # The first spelling wins, so the division is named the way it was written.
    assert plan.divisions[0].name == "4.0 Mixed"


def test_settings_come_from_the_first_row_naming_a_division() -> None:
    plan = plan_for(
        "Division,Format,Draw,Best of,Player 1\n"
        "Open,singles,single elim,5,Ivo\n"
        "Open,,,,Sam\n"
        "Open,,,,Nina\n"
    )
    division = plan.divisions[0]
    assert (division.format, division.draw_kind, division.best_of) == (
        "singles", "single_elimination", 5,
    )
    assert len(division.entries) == 3


def test_a_later_row_contradicting_the_settings_warns_but_keeps_the_first() -> None:
    plan = plan_for(
        "Division,Draw,Player 1,Player 2\n"
        "4.0,round robin,Ivo,Priya\n"
        "4.0,double elim,Sam,Nina\n"
    )
    assert plan.ok
    assert plan.divisions[0].draw_kind == "round_robin"
    assert any("keeping the first" in w for w in warnings(plan))


def test_a_player_may_appear_in_several_divisions() -> None:
    plan = plan_for(
        "Division,Format,Player 1,Player 2\n"
        "4.0 Mixed,mixed,Ivo Novak,Priya Raman\n"
        "4.0 Mixed,mixed,Sam Whitfield,Nina Roth\n"
        "Open Singles,singles,Ivo Novak,\n"
        "Open Singles,singles,Sam Whitfield,\n"
    )
    assert plan.ok, errors(plan)
    assert len(plan.player_names) == 4
    assert plan.entry_count == 4


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [("doubles", "doubles"), ("Doubles", "doubles"), ("MD", "doubles"),
     ("singles", "singles"), ("MS", "singles"), ("mixed", "mixed"),
     ("Mixed Doubles", "mixed"), ("XD", "mixed")],
)
def test_format_spellings(written: str, expected: str) -> None:
    partner = "" if expected == "singles" else "Nina"
    plan = plan_for(f"Division,Format,Player 1,Player 2\n4.0,{written},Ivo,{partner}\n")
    assert plan.divisions[0].format == expected


@pytest.mark.parametrize(
    ("written", "expected"),
    [("round robin", "round_robin"), ("RR", "round_robin"),
     ("single elim", "single_elimination"), ("knockout", "single_elimination"),
     ("double elim", "double_elimination"), ("DE", "double_elimination"),
     ("pools", "pool_playoff"), ("pool play", "pool_playoff")],
)
def test_draw_spellings(written: str, expected: str) -> None:
    plan = plan_for(f"Division,Draw,Player 1,Player 2\n4.0,{written},Ivo,Nina\n")
    assert plan.divisions[0].draw_kind == expected


def test_an_unrecognised_draw_falls_back_with_a_warning() -> None:
    plan = plan_for("Division,Draw,Player 1,Player 2\n4.0,swiss,Ivo,Nina\n")
    assert plan.ok
    assert plan.divisions[0].draw_kind == "round_robin"
    assert any("swiss" in w for w in warnings(plan))


def test_defaults_when_no_settings_are_given() -> None:
    division = plan_for(DOUBLES).divisions[0]
    assert division.format == "doubles"
    assert division.draw_kind == "round_robin"
    assert division.best_of == 3
    assert division.match_config["games_to"] == [11, 11, 15]
    assert division.draw_config == {"pool_count": 1}


def test_pool_playoff_carries_its_pool_count_into_the_draw_config() -> None:
    plan = plan_for(
        "Division,Draw,Pools,Player 1,Player 2\n"
        "4.0,pools,3,Ivo,Nina\n4.0,,,Sam,Priya\n4.0,,,Toby,Grace\n"
        "4.0,,,Alex,Mia\n4.0,,,Dan,Rosa\n4.0,,,Chris,Lena\n"
    )
    assert plan.divisions[0].draw_config == {"pool_count": 3, "advance_per_pool": 2}


def test_best_of_one_plays_a_single_game_to_eleven() -> None:
    plan = plan_for("Division,Best of,Player 1,Player 2\n4.0,1,Ivo,Nina\n")
    assert plan.divisions[0].match_config["games_to"] == [11]
    assert plan.divisions[0].match_config["best_of"] == 1


def test_an_even_best_of_is_refused_because_it_cannot_be_decided() -> None:
    plan = plan_for("Division,Best of,Player 1,Player 2\n4.0,2,Ivo,Nina\n")
    assert plan.divisions[0].best_of == 3
    assert any("must be 1, 3 or 5" in w for w in warnings(plan))


# ---------------------------------------------------------------------------
# Roster rules
# ---------------------------------------------------------------------------


def test_doubles_without_a_partner_is_an_error() -> None:
    plan = plan_for("Division,Player 1,Player 2\n4.0,Ivo Novak,\n")
    assert not plan.ok
    assert any("needs two players" in e for e in errors(plan))


def test_singles_ignores_a_stray_partner_with_a_warning() -> None:
    plan = plan_for(
        "Division,Format,Player 1,Player 2\n"
        "Open,singles,Ivo,Nina\nOpen,,Sam,\n"
    )
    assert plan.ok
    assert plan.divisions[0].entries[0].players == [
        plan.divisions[0].entries[0].players[0]
    ]
    assert len(plan.divisions[0].entries[0].players) == 1
    assert any("was ignored" in w for w in warnings(plan))


def test_the_same_person_cannot_partner_themselves() -> None:
    plan = plan_for("Division,Player 1,Player 2\n4.0,Ivo Novak,ivo novak\n")
    assert not plan.ok
    assert any("named twice in one team" in e for e in errors(plan))


def test_a_player_cannot_hold_two_places_in_one_division() -> None:
    # The scheduler would otherwise be asked to put them on two courts at once.
    plan = plan_for(
        "Division,Player 1,Player 2\n"
        "4.0,Ivo Novak,Priya Raman\n"
        "4.0,Sam Whitfield,Ivo Novak\n"
    )
    assert not plan.ok
    assert any("only hold one place per division" in e for e in errors(plan))


def test_a_row_with_no_players_is_an_error() -> None:
    plan = plan_for("Division,Player 1,Player 2\n4.0,,\n4.0,Ivo,Nina\n")
    assert not plan.ok
    assert any("No player named" in e for e in errors(plan))


def test_a_row_with_no_division_is_an_error() -> None:
    plan = plan_for("Division,Player 1,Player 2\n,Ivo,Nina\n4.0,Sam,Priya\n")
    assert not plan.ok
    assert any("No division name" in e for e in errors(plan))


def test_only_a_partner_named_promotes_them_to_player_one() -> None:
    # Better than discarding a row that plainly names someone.
    plan = plan_for("Division,Format,Player 1,Player 2\nOpen,singles,,Ivo Novak\n")
    assert plan.ok, errors(plan)
    assert plan.divisions[0].entries[0].players[0].name == "Ivo Novak"


# ---------------------------------------------------------------------------
# Names, seeds and ratings
# ---------------------------------------------------------------------------


def test_a_blank_team_name_is_built_from_first_names() -> None:
    # Same convention as the manual registration form, so a team added by hand
    # and one imported are named the same way.
    plan = plan_for(DOUBLES)
    assert plan.divisions[0].entries[0].name == "Ivo & Priya"


def test_an_explicit_team_name_is_kept() -> None:
    plan = plan_for("Division,Team,Player 1,Player 2\n4.0,Kitchen Bandits,Ivo,Nina\n")
    assert plan.divisions[0].entries[0].name == "Kitchen Bandits"


def test_seeds_are_read_and_duplicates_only_warn() -> None:
    plan = plan_for(
        "Division,Seed,Player 1,Player 2\n"
        "4.0,1,Ivo,Priya\n4.0,1,Sam,Nina\n"
    )
    assert plan.ok
    assert [e.seed for e in plan.divisions[0].entries] == [1, 1]
    assert any("Seed 1 is used on rows" in w for w in warnings(plan))


def test_a_nonsense_seed_leaves_the_team_unseeded() -> None:
    plan = plan_for("Division,Seed,Player 1,Player 2\n4.0,top,Ivo,Nina\n")
    assert plan.ok
    assert plan.divisions[0].entries[0].seed is None
    assert any("left unseeded" in w for w in warnings(plan))


def test_ratings_are_read_and_out_of_range_ones_dropped() -> None:
    plan = plan_for(
        "Division,Player 1,Rating 1,Player 2,Rating 2\n"
        "4.0,Ivo,4.25,Nina,99\n"
    )
    assert plan.ok
    players = plan.divisions[0].entries[0].players
    assert players[0].rating == 4.25
    assert players[1].rating is None
    assert any("outside 0-8" in w for w in warnings(plan))


# ---------------------------------------------------------------------------
# Field size
# ---------------------------------------------------------------------------


def test_a_division_with_one_team_warns_because_it_has_no_draw() -> None:
    plan = plan_for("Division,Player 1,Player 2\n4.0,Ivo,Nina\n")
    assert plan.ok
    assert any("only one team" in w for w in warnings(plan))


def test_a_sheet_with_only_a_header_is_rejected() -> None:
    plan = plan_for("Division,Player 1,Player 2\n")
    assert not plan.ok
    assert any("no rows" in e for e in errors(plan))


def test_a_missing_division_column_is_rejected_up_front() -> None:
    plan = plan_for("Player 1,Player 2\nIvo,Nina\n")
    assert not plan.ok
    assert any("No Division column" in e for e in errors(plan))


def test_thin_pools_warn_without_blocking() -> None:
    plan = plan_for(
        "Division,Draw,Pools,Player 1,Player 2\n"
        "4.0,pools,4,Ivo,Nina\n4.0,,,Sam,Priya\n"
    )
    assert plan.ok
    assert any("fewer than two per pool" in w for w in warnings(plan))


def test_a_bad_row_does_not_discard_the_good_ones() -> None:
    # The organizer should be able to fix one line, not re-enter the sheet.
    plan = plan_for(
        "Division,Player 1,Player 2\n"
        "4.0,Ivo,Priya\n"
        "4.0,Sam,\n"
        "4.0,Toby,Nina\n"
    )
    assert not plan.ok
    assert len(plan.divisions[0].entries) == 2
    assert [p.row for p in plan.problems if p.severity == "error"] == [3]
