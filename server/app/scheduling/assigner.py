"""Court assignment.

A greedy assigner, deliberately: the plan reached for CP-SAT only if greedy
proved inadequate, and for a club event with a handful of courts it does not.
What matters more than optimality is that it never puts one player on two
courts at once, and that when it cannot satisfy a constraint it *says so*
instead of quietly reordering the day.

Pure functions over plain data — no database, so it is directly testable.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


class Playable(BaseModel):
    """A match that is ready to be sent to a court."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    division_id: str
    round: int
    slot: int
    #: Player ids on both sides. This is what conflict detection works on —
    #: entries differ per division, but a person is a person.
    player_ids: list[str] = Field(default_factory=list)
    label: str | None = None
    #: Lower sorts earlier. Pool matches before playoffs, earlier rounds first.
    priority: int = 0


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    court_id: str
    #: 0-based wave. Everything in a wave plays simultaneously.
    wave: int


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    reason: str
    player_ids: list[str] = Field(default_factory=list)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[Assignment] = Field(default_factory=list)
    #: Matches that could not be placed, and why. Surfaced, never swallowed.
    unplaced: list[Conflict] = Field(default_factory=list)

    def by_court(self) -> dict[str, list[Assignment]]:
        out: dict[str, list[Assignment]] = {}
        for item in sorted(self.assignments, key=lambda a: (a.wave, a.court_id)):
            out.setdefault(item.court_id, []).append(item)
        return out


def assign_courts(
    playable: Sequence[Playable],
    court_ids: Sequence[str],
    *,
    max_waves: int = 50,
    rest_waves: int = 1,
    busy_players: Sequence[str] = (),
    busy_courts: Sequence[str] = (),
) -> Schedule:
    """Pack ready matches onto courts in waves.

    `rest_waves` is the number of waves a player must sit out after finishing —
    1 means no back-to-back matches. `busy_players` and `busy_courts` describe
    what is already happening on court right now, so a live event schedules
    around it rather than on top of it.
    """
    if not court_ids:
        return Schedule(
            unplaced=[
                Conflict(match_id=m.match_id, reason="no courts available")
                for m in playable
            ]
        )

    free_courts = [c for c in court_ids if c not in set(busy_courts)]
    if not free_courts:
        return Schedule(
            unplaced=[
                Conflict(match_id=m.match_id, reason="every court is in use")
                for m in playable
            ]
        )

    queue = sorted(playable, key=lambda m: (m.priority, m.round, m.slot, m.match_id))
    assignments: list[Assignment] = []
    unplaced: list[Conflict] = []

    #: player id -> the wave after which they are free again.
    available_from: dict[str, int] = dict.fromkeys(busy_players, 1)
    remaining = list(queue)
    wave = 0

    while remaining and wave < max_waves:
        courts_left = list(free_courts)
        playing_now: set[str] = set()
        deferred: list[Playable] = []

        for match in remaining:
            if not courts_left:
                deferred.append(match)
                continue

            players = set(match.player_ids)
            if players & playing_now:
                deferred.append(match)  # same player, same wave
                continue
            if any(available_from.get(p, 0) > wave for p in players):
                deferred.append(match)  # still resting
                continue

            court = courts_left.pop(0)
            assignments.append(
                Assignment(match_id=match.match_id, court_id=court, wave=wave)
            )
            playing_now |= players
            for player in players:
                available_from[player] = wave + 1 + rest_waves

        if not playing_now:
            # Nothing was placed this wave. That is fine if someone is still
            # resting — waiting resolves it. It is a genuine deadlock only when
            # no clock is running, because then the next wave looks identical.
            resting = any(free > wave for free in available_from.values())
            if not resting:
                for match in deferred:
                    unplaced.append(
                        Conflict(
                            match_id=match.match_id,
                            reason="cannot be scheduled without double-booking "
                                   "a player",
                            player_ids=match.player_ids,
                        )
                    )
                remaining = []
                break

        remaining = deferred
        wave += 1

    for match in remaining:
        unplaced.append(
            Conflict(match_id=match.match_id, reason="ran out of scheduling waves")
        )

    return Schedule(assignments=assignments, unplaced=unplaced)


def find_conflicts(playable: Sequence[Playable]) -> list[Conflict]:
    """Players who appear in more than one ready match.

    Not an error on its own — it just means those matches cannot run in the same
    wave. Worth showing an organizer who is wondering why the board is not full.
    """
    seen: dict[str, list[str]] = {}
    for match in playable:
        for player in match.player_ids:
            seen.setdefault(player, []).append(match.match_id)

    conflicts: list[Conflict] = []
    for player, matches in sorted(seen.items()):
        if len(matches) > 1:
            conflicts.append(
                Conflict(
                    match_id=matches[0],
                    reason=f"player is in {len(matches)} ready matches: "
                           f"{', '.join(sorted(matches))}",
                    player_ids=[player],
                )
            )
    return conflicts
