"""tournaments.kind must be a native enum on Postgres

a1c4e7b92f10 added `kind` as VARCHAR. That is wrong: SQLModel maps an Enum
field to a *native* Postgres enum type, so every query binds the parameter as
`$1::tournamentkind` and fails with `type "tournamentkind" does not exist`.
Every other enum column in this schema (tournaments.status, matches.status,
divisions.format, ...) is a native enum, so VARCHAR was the odd one out.

SQLite has no native enum types — it stores them as VARCHAR either way — which
is exactly why the local suite passed and only Postgres failed. Same shape as
the naive-timestamp and missing-cascade bugs this project hit before.

Kept as a separate migration rather than amending a1c4e7b92f10 because that one
has already run in deployment; this way a fresh database and the deployed one
follow the identical path and end in the identical state.

The conversion casts rather than drops, so it stays non-destructive even where
casual tournaments already exist.

Revision ID: b3d81a44c206
Revises: a1c4e7b92f10
Create Date: 2026-08-14 17:22:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3d81a44c206'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7b92f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Native enums store the member *names*, which is how the initial migration
# spelled every other enum in this schema.
KIND = sa.Enum('TOURNAMENT', 'CASUAL', name='tournamentkind')


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        # SQLite already treats the column as VARCHAR, which is what the enum
        # maps to there. Nothing to do.
        return

    KIND.create(bind, checkfirst=True)
    op.execute("ALTER TABLE tournaments ALTER COLUMN kind DROP DEFAULT")
    op.execute(
        "ALTER TABLE tournaments ALTER COLUMN kind TYPE tournamentkind "
        "USING upper(kind)::tournamentkind"
    )
    op.execute("ALTER TABLE tournaments ALTER COLUMN kind SET DEFAULT 'TOURNAMENT'")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    op.execute("ALTER TABLE tournaments ALTER COLUMN kind DROP DEFAULT")
    op.execute(
        "ALTER TABLE tournaments ALTER COLUMN kind TYPE VARCHAR "
        "USING lower(kind::text)"
    )
    op.execute("ALTER TABLE tournaments ALTER COLUMN kind SET DEFAULT 'tournament'")
    KIND.drop(bind, checkfirst=True)
