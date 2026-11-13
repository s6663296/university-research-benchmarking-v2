"""AI 摘要：儲存各系所／學院的摘要與期間比較，並記錄使用者改選的前期分析

Revision ID: 0008_ai_summaries
Revises: 0007_import_retired_at
Create Date: 2026-11-12 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008_ai_summaries'
down_revision = '0007_import_retired_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('ai_baseline_run_id', sa.Integer(), nullable=True))
    op.create_table(
        'ai_summaries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('runs.id'), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('baseline_run_id', sa.Integer(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('comparison', sa.Text(), nullable=True),
        sa.Column('input_hash', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('edited', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('run_id', 'kind', 'key'),
    )
    op.create_index('ix_ai_summaries_run_id', 'ai_summaries', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_summaries_run_id', table_name='ai_summaries')
    op.drop_table('ai_summaries')
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('ai_baseline_run_id')
