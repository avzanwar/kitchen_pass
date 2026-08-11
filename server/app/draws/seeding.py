"""Seeding helpers: bracket slot order, bye allocation and snake pools."""

from __future__ import annotations


def next_power_of_two(n: int) -> int:
    size = 1
    while size < n:
        size *= 2
    return size


def seed_order(bracket_size: int) -> list[int]:
    """Standard single-elimination seed positions for a power-of-two bracket.

    Returns 1-indexed seeds in slot order, so consecutive pairs are the
    first-round matchups. For 8 this gives 1v8, 4v5, 2v7, 3v6 — the top seeds
    are kept apart until as late as possible.
    """
    if bracket_size < 1 or bracket_size & (bracket_size - 1):
        raise ValueError(f"bracket_size must be a power of two, got {bracket_size}")

    order = [1]
    while len(order) < bracket_size:
        size = len(order) * 2
        expanded: list[int] = []
        for seed in order:
            expanded.append(seed)
            expanded.append(size + 1 - seed)
        order = expanded
    return order


def seed_slots(entry_ids: list[str]) -> list[str | None]:
    """Place seeded entries into bracket slots, padding with byes.

    `entry_ids` must already be in seed order (index 0 is the top seed). Slots
    that fall to a seed number higher than the field size become `None`, which
    is what gives the top seeds the byes.
    """
    size = next_power_of_two(max(len(entry_ids), 1))
    return [
        entry_ids[seed - 1] if seed <= len(entry_ids) else None
        for seed in seed_order(size)
    ]


def snake_pools(entry_ids: list[str], pool_count: int) -> dict[str, list[str]]:
    """Distribute seeded entries across pools in snake order.

    Seeds 1..n go A, B, C, then C, B, A, then A, B, C — so the pools come out as
    balanced as the field allows instead of stacking the top seeds together.
    """
    if pool_count < 1:
        raise ValueError("pool_count must be at least 1")
    if pool_count > len(entry_ids):
        raise ValueError(
            f"cannot split {len(entry_ids)} entries into {pool_count} pools"
        )

    labels = [pool_label(i) for i in range(pool_count)]
    pools: dict[str, list[str]] = {label: [] for label in labels}

    for index, entry_id in enumerate(entry_ids):
        row, col = divmod(index, pool_count)
        if row % 2:
            col = pool_count - 1 - col
        pools[labels[col]].append(entry_id)
    return pools


def pool_label(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    label = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label
