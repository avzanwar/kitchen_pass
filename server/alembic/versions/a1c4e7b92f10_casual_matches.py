"""casual matches: tournament kind and guest players

Two additive columns, nothing else. Both carry a server default so the rows
already in the deployed database satisfy NOT NULL without a backfill pass, and
both are plain ADD COLUMNs — SQLite cannot ALTER a constraint, which is why the
initial migration was squashed to create_table calls in the first place.

Revision ID: a1c4e7b92f10
Revises: 519d0b77f31e
Create Date: 2026-08-14 17:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c4e7b92f10'
down_revision: Union[str, Sequence[str], None] = '519d0b77f31e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'tournaments',
        sa.Column(
            'kind',
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default='tournament',
        ),
    )
    op.create_index(
        op.f('ix_tournaments_kind'), 'tournaments', ['kind'], unique=False
    )

    op.add_column(
        'players',
        sa.Column(
            'is_guest',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        op.f('ix_players_is_guest'), 'players', ['is_guest'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_players_is_guest'), table_name='players')
    op.drop_column('players', 'is_guest')
    op.drop_index(op.f('ix_tournaments_kind'), table_name='tournaments')
    op.drop_column('tournaments', 'kind')
