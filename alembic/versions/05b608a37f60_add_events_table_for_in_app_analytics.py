"""add events table for in-app analytics

Revision ID: 05b608a37f60
Revises: 4a27b82fcbb9
Create Date: 2026-05-07 10:01:51.930612

"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '05b608a37f60'
down_revision: str | None = '4a27b82fcbb9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('profile_id', sa.Uuid(), nullable=True),
    sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('properties', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('user_agent', sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True),
    sa.Column('path', sqlmodel.sql.sqltypes.AutoString(length=256), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['user_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_events_profile_id_occurred_at', 'events',
        ['profile_id', 'occurred_at'],
    )
    op.create_index(
        'ix_events_name_occurred_at', 'events',
        ['name', 'occurred_at'],
    )
    op.create_index(
        'ix_events_session_id_occurred_at', 'events',
        ['session_id', 'occurred_at'],
    )
    op.create_index(op.f('ix_events_name'), 'events', ['name'], unique=False)
    op.create_index(op.f('ix_events_occurred_at'), 'events', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_events_profile_id'), 'events', ['profile_id'], unique=False)
    op.create_index(op.f('ix_events_session_id'), 'events', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_events_session_id_occurred_at', table_name='events')
    op.drop_index('ix_events_name_occurred_at', table_name='events')
    op.drop_index('ix_events_profile_id_occurred_at', table_name='events')
    op.drop_index(op.f('ix_events_session_id'), table_name='events')
    op.drop_index(op.f('ix_events_profile_id'), table_name='events')
    op.drop_index(op.f('ix_events_occurred_at'), table_name='events')
    op.drop_index(op.f('ix_events_name'), table_name='events')
    op.drop_table('events')
