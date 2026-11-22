"""學校的 Scopus 機構代碼（AF-ID），取代以校名字串排除名稱相近的他校

Revision ID: 0014_school_affiliations
Revises: 0013_quota_per_key
Create Date: 2026-11-22 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0014_school_affiliations'
down_revision = '0013_quota_per_key'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('school_affiliations',
    sa.Column('school_key', sa.String(), nullable=False),
    sa.Column('school_en', sa.String(), nullable=False),
    sa.Column('af_id', sa.String(), nullable=True),
    sa.Column('af_name', sa.String(), nullable=True),
    sa.Column('matched_by', sa.String(), nullable=False),
    sa.Column('candidates_json', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('school_key')
    )


def downgrade() -> None:
    op.drop_table('school_affiliations')
