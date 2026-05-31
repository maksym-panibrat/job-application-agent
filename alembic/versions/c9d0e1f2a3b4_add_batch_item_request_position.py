"""add batch item request position

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-05-30 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_match_batch_items",
        sa.Column(
            "provider_request_position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY batch_id, provider_request_key
                    ORDER BY created_at ASC, id ASC
                ) - 1 AS position
            FROM llm_match_batch_items
        )
        UPDATE llm_match_batch_items item
        SET provider_request_position = ordered.position
        FROM ordered
        WHERE item.id = ordered.id
        """
    )
    op.create_index(
        "ix_llm_match_batch_items_request_position",
        "llm_match_batch_items",
        ["batch_id", "provider_request_key", "provider_request_position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_match_batch_items_request_position",
        table_name="llm_match_batch_items",
    )
    op.drop_column("llm_match_batch_items", "provider_request_position")
