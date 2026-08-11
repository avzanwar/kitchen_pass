"""Double-elimination bracket generation.

The structure, for a bracket padded to N = 2^k:

* Winners bracket: k rounds, halving each time.
* Losers bracket: 2k-2 rounds, alternating
  - **minor** rounds, which pair up survivors already in the losers bracket, and
  - **major** rounds, which absorb the fresh losers dropping out of the winners
    bracket.
* Grand final, plus a **conditional reset** — if the losers-bracket team wins
  the grand final, both teams have one loss and a decider is played.

Losers dropping from the winners bracket are cross-fed in reversed order, which
is the standard trick for reducing immediate rematches between teams that
already met in the winners bracket.
"""

from __future__ import annotations

from .seeding import next_power_of_two, seed_slots
from .types import Condition, Draw, DrawMatch, Slot, Source

WB = "W"
LB = "L"
GF = "GF"
GF_RESET = "GF2"


def double_elimination(entry_ids: list[str]) -> Draw:
    if len(entry_ids) < 2:
        raise ValueError("a bracket needs at least two entries")

    slots = seed_slots(entry_ids)
    size = next_power_of_two(len(entry_ids))
    k = size.bit_length() - 1

    matches: list[DrawMatch] = []
    matches.extend(_winners_bracket(slots, size, k))
    matches.extend(_losers_bracket(size, k))
    matches.extend(_grand_final(k))

    return Draw(kind="double_elimination", matches=matches, seeds=list(entry_ids))


def _winners_bracket(slots: list[str | None], size: int, k: int) -> list[DrawMatch]:
    matches: list[DrawMatch] = []

    for index in range(0, size, 2):
        slot = index // 2 + 1
        left, right = slots[index], slots[index + 1]
        matches.append(
            DrawMatch(
                id=f"{WB}-R1-M{slot}",
                bracket="winners",
                round=1,
                slot=slot,
                a=Slot(entry_id=left) if left else Slot(bye=True),
                b=Slot(entry_id=right) if right else Slot(bye=True),
                label="Winners round 1",
            )
        )

    for rnd in range(2, k + 1):
        for slot in range(1, size // (2**rnd) + 1):
            matches.append(
                DrawMatch(
                    id=f"{WB}-R{rnd}-M{slot}",
                    bracket="winners",
                    round=rnd,
                    slot=slot,
                    a=Slot(source=Source(kind="winner",
                                         match_id=f"{WB}-R{rnd - 1}-M{2 * slot - 1}")),
                    b=Slot(source=Source(kind="winner",
                                         match_id=f"{WB}-R{rnd - 1}-M{2 * slot}")),
                    label="Winners final" if rnd == k else f"Winners round {rnd}",
                )
            )
    return matches


def _losers_bracket(size: int, k: int) -> list[DrawMatch]:
    """Emit the 2k-2 losers rounds, alternating minor and major."""
    matches: list[DrawMatch] = []
    lb_round = 0

    for j in range(1, k):
        count = size // (2 ** (j + 1))

        # --- minor round: survivors already in the losers bracket pair up ----
        lb_round += 1
        for i in range(1, count + 1):
            if j == 1:
                # The very first minor round consumes the winners round 1 losers.
                a = Source(kind="loser", match_id=f"{WB}-R1-M{2 * i - 1}")
                b = Source(kind="loser", match_id=f"{WB}-R1-M{2 * i}")
            else:
                prev = lb_round - 1
                a = Source(kind="winner", match_id=f"{LB}-R{prev}-M{2 * i - 1}")
                b = Source(kind="winner", match_id=f"{LB}-R{prev}-M{2 * i}")
            matches.append(
                DrawMatch(
                    id=f"{LB}-R{lb_round}-M{i}",
                    bracket="losers",
                    round=lb_round,
                    slot=i,
                    a=Slot(source=a),
                    b=Slot(source=b),
                    label=f"Losers round {lb_round}",
                )
            )

        # --- major round: absorb the losers dropping from the winners bracket -
        lb_round += 1
        wb_round = j + 1
        wb_count = size // (2**wb_round)
        prev = lb_round - 1
        for i in range(1, count + 1):
            # Reversed order: the loser of the *last* winners match faces the
            # survivor from the *first* losers match, so teams that just played
            # each other are less likely to meet again immediately.
            drop_slot = wb_count - i + 1
            matches.append(
                DrawMatch(
                    id=f"{LB}-R{lb_round}-M{i}",
                    bracket="losers",
                    round=lb_round,
                    slot=i,
                    a=Slot(source=Source(kind="winner", match_id=f"{LB}-R{prev}-M{i}")),
                    b=Slot(source=Source(kind="loser",
                                         match_id=f"{WB}-R{wb_round}-M{drop_slot}")),
                    label=(
                        "Losers final" if lb_round == 2 * k - 2
                        else f"Losers round {lb_round}"
                    ),
                )
            )

    return matches


def _grand_final(k: int) -> list[DrawMatch]:
    # With a two-entry bracket there is no losers bracket at all; the beaten
    # finalist comes straight from the single winners match.
    if k == 1:
        challenger = Source(kind="loser", match_id=f"{WB}-R1-M1")
    else:
        challenger = Source(kind="winner", match_id=f"{LB}-R{2 * k - 2}-M1")

    # Round numbers are per-bracket, and the losers bracket runs to 2k-2 rounds
    # — longer than the winners bracket for k > 2. The grand final must sort
    # after both, or it collides with the losers final.
    gf_round = max(k, 2 * k - 2) + 1

    return [
        DrawMatch(
            id=GF,
            bracket="final",
            round=gf_round,
            slot=1,
            a=Slot(source=Source(kind="winner", match_id=f"{WB}-R{k}-M1")),
            b=Slot(source=challenger),
            decides_title=True,
            label="Grand final",
        ),
        DrawMatch(
            id=GF_RESET,
            bracket="final",
            round=gf_round + 1,
            slot=1,
            a=Slot(source=Source(kind="winner", match_id=GF)),
            b=Slot(source=Source(kind="loser", match_id=GF)),
            # Only played if the losers-bracket team wins the grand final —
            # slot A of GF is the winners-bracket team, so the reset is on
            # exactly when they lose it.
            conditional=True,
            condition=Condition(kind="slot_a_lost", match_id=GF),
            decides_title=True,
            label="Grand final (reset)",
        ),
    ]
