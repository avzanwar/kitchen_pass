"""Turn parsed rows into a plan of what to create, plus what is wrong with it.

Pure: no database, no I/O. That is what makes the rules here cheap to test and
what lets the same code back both the preview and the commit — the organizer is
shown the outcome of exactly the function that will run.

Severity matters. An `error` blocks the whole import; a `warning` is something
the organizer should see but which has a defensible automatic answer. The bias
is towards warnings: refusing a 60-team sheet over one odd cell is worse than
importing it and saying what was assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.imports.sheet import Sheet

Severity = Literal["error", "warning"]

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

FORMATS: dict[str, str] = {
    "doubles": "doubles", "double": "doubles", "d": "doubles", "dbl": "doubles",
    "md": "doubles", "wd": "doubles", "mensdoubles": "doubles",
    "womensdoubles": "doubles", "ladiesdoubles": "doubles",
    "singles": "singles", "single": "singles", "s": "singles",
    "ms": "singles", "ws": "singles", "menssingles": "singles",
    "womenssingles": "singles", "ladiessingles": "singles",
    "mixed": "mixed", "mixeddoubles": "mixed", "mx": "mixed", "mxd": "mixed",
    "xd": "mixed", "x": "mixed",
}

DRAWS: dict[str, str] = {
    "roundrobin": "round_robin", "rr": "round_robin", "round": "round_robin",
    "roundrobbin": "round_robin", "league": "round_robin",
    "singleelimination": "single_elimination", "singleelim": "single_elimination",
    "se": "single_elimination", "knockout": "single_elimination",
    "ko": "single_elimination", "elimination": "single_elimination",
    "bracket": "single_elimination",
    "doubleelimination": "double_elimination", "doubleelim": "double_elimination",
    "de": "double_elimination",
    "poolplayoff": "pool_playoff", "pools": "pool_playoff", "pool": "pool_playoff",
    "poolplay": "pool_playoff", "poolsplayoff": "pool_playoff",
    "poolstoplayoff": "pool_playoff", "pp": "pool_playoff",
}

ROSTER_SIZE: dict[str, int] = {"singles": 1, "doubles": 2, "mixed": 2}

DEFAULT_FORMAT = "doubles"
DEFAULT_DRAW = "round_robin"
DEFAULT_BEST_OF = 3
DEFAULT_POOLS = 2


def name_key(value: str) -> str:
    """Fold a name for comparison: lowercase, alphanumerics only.

    Used for division names, format words and player names alike, so that
    "Ivo Novak", "ivo novak" and "IvoNovak" are one person.
    """
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Plan shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    severity: Severity
    message: str
    #: Spreadsheet line number, or None for something wrong with the file overall.
    row: int | None = None


@dataclass
class PlannedPlayer:
    name: str
    rating: float | None = None
    #: Filled in by the import service: the existing roster player this matched.
    existing_id: str | None = None


@dataclass
class PlannedEntry:
    row: int
    name: str
    seed: int | None
    players: list[PlannedPlayer]


@dataclass
class PlannedDivision:
    name: str
    format: str
    draw_kind: str
    skill: str | None
    age: str | None
    best_of: int
    pools: int
    #: The line the division was first named on, for problem reporting.
    row: int
    entries: list[PlannedEntry] = field(default_factory=list)
    #: Filled in by the import service when a division of this name already exists.
    existing_id: str | None = None

    @property
    def draw_config(self) -> dict[str, Any]:
        if self.draw_kind == "pool_playoff":
            return {"pool_count": self.pools, "advance_per_pool": 2}
        if self.draw_kind == "round_robin":
            return {"pool_count": 1}
        return {}

    @property
    def match_config(self) -> dict[str, Any]:
        return {
            "format": "singles" if self.format == "singles" else "doubles",
            "best_of": self.best_of,
            # `games_to` is clamped by the engine, so three entries cover a
            # best-of-five too: 11, 11, then 15 for every decider after that.
            "games_to": [11] if self.best_of == 1 else [11, 11, 15],
            "win_by_2": True,
            "switch_ends": "deciding_game",
        }


@dataclass
class ImportPlan:
    divisions: list[PlannedDivision] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    #: Distinct player names in sheet order, lowercased key -> display name.
    player_names: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)

    @property
    def entry_count(self) -> int:
        return sum(len(d.entries) for d in self.divisions)

    def error(self, message: str, row: int | None = None) -> None:
        self.problems.append(Problem("error", message, row))

    def warn(self, message: str, row: int | None = None) -> None:
        self.problems.append(Problem("warning", message, row))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_plan(sheet: Sheet) -> ImportPlan:
    """Validate rows and group them into divisions. Never raises."""
    plan = ImportPlan()

    for heading in sheet.unknown_headings:
        plan.warn(f"Column {heading!r} was not recognised and has been ignored")

    if "division" not in sheet.present:
        plan.error("No Division column found — every row needs a division name")
    if "player1" not in sheet.present:
        plan.error("No Player 1 column found — every row needs at least one player")
    if not sheet.rows:
        plan.error("The sheet has a header but no rows")
    if not plan.ok:
        return plan

    by_name: dict[str, PlannedDivision] = {}
    # Per division: player key -> the line that already claimed them, and the
    # seeds already used. Both are per division because the same person may
    # legitimately play in two divisions, but not twice in one.
    claimed: dict[str, dict[str, int]] = {}
    seeds: dict[str, dict[int, int]] = {}

    for row in sheet.rows:
        line = int(row["line"])
        division_name = str(row["division"]).strip()
        if not division_name:
            plan.error("No division name on this row", line)
            continue

        key = name_key(division_name)
        division = by_name.get(key)
        if division is None:
            division = _new_division(plan, row, division_name, line)
            by_name[key] = division
            claimed[key] = {}
            seeds[key] = {}
            plan.divisions.append(division)
        else:
            _check_consistent(plan, division, row, line)

        entry = _entry_for(plan, division, row, line, claimed[key], seeds[key])
        if entry is not None:
            division.entries.append(entry)
            for player in entry.players:
                plan.player_names.setdefault(name_key(player.name), player.name)

    _check_field_sizes(plan, plan.divisions)
    return plan


def _new_division(
    plan: ImportPlan, row: dict[str, Any], name: str, line: int
) -> PlannedDivision:
    fmt = _parse_choice(
        plan, row.get("format"), FORMATS, "format", DEFAULT_FORMAT, name, line
    )
    draw = _parse_choice(
        plan, row.get("draw"), DRAWS, "draw", DEFAULT_DRAW, name, line
    )
    best_of = _parse_best_of(plan, row.get("best_of"), name, line)
    pools = _parse_pools(plan, row.get("pools"), draw, name, line)

    return PlannedDivision(
        name=name,
        format=fmt,
        draw_kind=draw,
        skill=str(row.get("skill") or "").strip() or None,
        age=str(row.get("age") or "").strip() or None,
        best_of=best_of,
        pools=pools,
        row=line,
    )


def _check_consistent(
    plan: ImportPlan, division: PlannedDivision, row: dict[str, Any], line: int
) -> None:
    """Warn when a later row disagrees with the division's settings.

    Settings come from the first row naming a division; blank cells on later
    rows are the normal case and mean "as above". A filled cell that disagrees
    is worth surfacing, because it is usually a copy-paste slip.
    """
    checks: list[tuple[str, str, Any]] = [
        ("format", "Format", FORMATS.get(name_key(row.get("format", "")))),
        ("draw_kind", "Draw", DRAWS.get(name_key(row.get("draw", "")))),
    ]
    for attribute, label, parsed in checks:
        if parsed is not None and parsed != getattr(division, attribute):
            plan.warn(
                f"{label} here says {parsed.replace('_', ' ')!r} but "
                f"{division.name!r} was already set to "
                f"{getattr(division, attribute).replace('_', ' ')!r} — keeping the first",
                line,
            )


def _entry_for(
    plan: ImportPlan,
    division: PlannedDivision,
    row: dict[str, Any],
    line: int,
    claimed: dict[str, int],
    seeds: dict[int, int],
) -> PlannedEntry | None:
    need = ROSTER_SIZE[division.format]
    name1 = str(row.get("player1") or "").strip()
    name2 = str(row.get("player2") or "").strip()

    if not name1 and not name2:
        plan.error("No player named on this row", line)
        return None
    if not name1:
        # Only a partner given: treat them as the first player rather than
        # discarding a row that plainly names someone.
        name1, name2 = name2, ""

    players = [PlannedPlayer(name1, _parse_rating(plan, row.get("rating1"), 1, line))]
    if need == 2:
        if not name2:
            plan.error(
                f"{division.name!r} is {division.format} and needs two players, "
                f"but Player 2 is blank",
                line,
            )
            return None
        players.append(
            PlannedPlayer(name2, _parse_rating(plan, row.get("rating2"), 2, line))
        )
    elif name2:
        plan.warn(
            f"{division.name!r} is singles, so Player 2 ({name2}) was ignored", line
        )

    if len({name_key(p.name) for p in players}) != len(players):
        plan.error("The same player is named twice in one team", line)
        return None

    for player in players:
        already = claimed.get(name_key(player.name))
        if already is not None:
            plan.error(
                f"{player.name} is already in a team in {division.name!r} "
                f"(row {already}) — a player can only hold one place per division",
                line,
            )
            return None
    for player in players:
        claimed[name_key(player.name)] = line

    seed = _parse_seed(plan, row.get("seed"), line)
    if seed is not None:
        clash = seeds.get(seed)
        if clash is not None:
            plan.warn(
                f"Seed {seed} is used on rows {clash} and {line} of "
                f"{division.name!r} — the draw will break the tie arbitrarily",
                line,
            )
        else:
            seeds[seed] = line

    name = str(row.get("team") or "").strip()
    if not name:
        # Same convention as the manual registration form, so a team added by
        # hand and one imported are named the same way.
        name = " & ".join(p.name.split(" ")[0] for p in players)

    return PlannedEntry(row=line, name=name, seed=seed, players=players)


def _check_field_sizes(plan: ImportPlan, divisions: list[PlannedDivision]) -> None:
    for division in divisions:
        count = len(division.entries)
        if count == 0:
            plan.error(f"{division.name!r} ended up with no teams", division.row)
        elif count == 1:
            plan.warn(
                f"{division.name!r} has only one team, so it has no draw to play",
                division.row,
            )
        if division.draw_kind == "pool_playoff" and count and count < division.pools * 2:
            plan.warn(
                f"{division.name!r} splits {count} team(s) across {division.pools} "
                f"pools — that is fewer than two per pool",
                division.row,
            )


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------


def _parse_choice(
    plan: ImportPlan,
    raw: Any,
    vocabulary: dict[str, str],
    label: str,
    default: str,
    division: str,
    line: int,
) -> str:
    text = str(raw or "").strip()
    if not text:
        return default
    value = vocabulary.get(name_key(text))
    if value is None:
        plan.warn(
            f"{label.capitalize()} {text!r} for {division!r} was not understood — "
            f"using {default.replace('_', ' ')!r}",
            line,
        )
        return default
    return value


def _parse_best_of(plan: ImportPlan, raw: Any, division: str, line: int) -> int:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_BEST_OF
    try:
        value = int(float(text))
    except ValueError:
        plan.warn(
            f"Best of {text!r} for {division!r} is not a number — using "
            f"{DEFAULT_BEST_OF}",
            line,
        )
        return DEFAULT_BEST_OF
    if value not in (1, 3, 5):
        plan.warn(
            f"Best of {value} for {division!r} must be 1, 3 or 5 — using "
            f"{DEFAULT_BEST_OF}",
            line,
        )
        return DEFAULT_BEST_OF
    return value


def _parse_pools(
    plan: ImportPlan, raw: Any, draw: str, division: str, line: int
) -> int:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_POOLS
    try:
        value = int(float(text))
    except ValueError:
        plan.warn(f"Pools {text!r} for {division!r} is not a number — using 2", line)
        return DEFAULT_POOLS
    if not 1 <= value <= 8:
        plan.warn(f"Pools {value} for {division!r} must be 1-8 — using 2", line)
        return DEFAULT_POOLS
    if draw != "pool_playoff":
        plan.warn(
            f"Pools was set for {division!r}, which is not a pools draw — ignored",
            line,
        )
    return value


def _parse_seed(plan: ImportPlan, raw: Any, line: int) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        plan.warn(f"Seed {text!r} is not a number — this team is left unseeded", line)
        return None
    if value < 1:
        plan.warn(f"Seed {value} must be 1 or more — this team is left unseeded", line)
        return None
    return value


def _parse_rating(plan: ImportPlan, raw: Any, position: int, line: int) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        plan.warn(f"Rating {text!r} for player {position} is not a number", line)
        return None
    if not 0 <= value <= 8:
        plan.warn(
            f"Rating {value} for player {position} is outside 0-8 and was dropped", line
        )
        return None
    return value
