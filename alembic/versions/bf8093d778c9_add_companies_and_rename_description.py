"""add companies, target_company_ids, jobs.company_id, description rename, source normalization

Revision ID: bf8093d778c9
Revises: 05b608a37f60
Create Date: 2026-05-08 16:19:52.297222
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "bf8093d778c9"
down_revision = "05b608a37f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. companies table
    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column(
            "provider_slugs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "unfollowable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("normalized_key", name="uq_companies_normalized_key"),
    )
    op.create_index("ix_companies_normalized_key", "companies", ["normalized_key"], unique=False)
    op.create_index(
        "ix_companies_provider_slugs",
        "companies",
        ["provider_slugs"],
        postgresql_using="gin",
    )

    # 2. user_profiles.target_company_ids
    op.add_column(
        "user_profiles",
        sa.Column(
            "target_company_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )

    # 3. jobs.company_id + indexes
    op.add_column(
        "jobs",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_company_id_companies",
        "jobs",
        "companies",
        ["company_id"],
        ["id"],
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"], unique=False)

    # 4. description column renames
    op.alter_column("jobs", "description_md", new_column_name="description_raw")
    op.alter_column("jobs", "description_clean", new_column_name="description")

    # 5. provider name normalization
    op.execute("UPDATE jobs SET source = 'greenhouse' WHERE source = 'greenhouse_board'")
    op.execute("UPDATE slug_fetches SET source = 'greenhouse' WHERE source = 'greenhouse_board'")

    # 6. data backfill — Company rows from existing greenhouse slugs
    op.execute("""
        INSERT INTO companies (
            id, canonical_name, normalized_key,
            provider_slugs, resolved_at, created_at
        )
        SELECT
            gen_random_uuid(),
            initcap(replace(slug, '-', ' ')),
            slug,
            jsonb_build_object('greenhouse', slug),
            NOW(),
            NOW()
        FROM (
            SELECT DISTINCT jsonb_array_elements_text(target_company_slugs->'greenhouse') AS slug
            FROM user_profiles
            WHERE jsonb_typeof(target_company_slugs->'greenhouse') = 'array'
        ) s
        WHERE slug IS NOT NULL AND slug <> ''
        ON CONFLICT (normalized_key) DO NOTHING
    """)

    # 7. populate target_company_ids on each profile
    op.execute("""
        UPDATE user_profiles up
        SET target_company_ids = COALESCE((
            SELECT array_agg(c.id)
            FROM jsonb_array_elements_text(up.target_company_slugs->'greenhouse') AS slug
            JOIN companies c ON c.provider_slugs->>'greenhouse' = slug
        ), '{}')
    """)

    # 8. backfill jobs.company_id from existing greenhouse jobs
    op.execute("""
        UPDATE jobs j
        SET company_id = c.id
        FROM companies c
        WHERE j.source = 'greenhouse'
          AND c.provider_slugs->>'greenhouse' IS NOT NULL
          AND c.canonical_name = j.company_name
    """)


def downgrade() -> None:
    op.execute("UPDATE jobs SET company_id = NULL")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_constraint("fk_jobs_company_id_companies", "jobs", type_="foreignkey")
    op.drop_column("jobs", "company_id")

    op.alter_column("jobs", "description", new_column_name="description_clean")
    op.alter_column("jobs", "description_raw", new_column_name="description_md")

    op.execute("UPDATE jobs SET source = 'greenhouse_board' WHERE source = 'greenhouse'")
    op.execute("UPDATE slug_fetches SET source = 'greenhouse_board' WHERE source = 'greenhouse'")

    op.drop_column("user_profiles", "target_company_ids")

    op.drop_index("ix_companies_provider_slugs", table_name="companies", postgresql_using="gin")
    op.drop_index("ix_companies_normalized_key", table_name="companies")
    op.drop_table("companies")
