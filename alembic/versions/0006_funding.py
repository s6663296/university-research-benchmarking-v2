"""funding（經分表）匯入

Revision ID: 0006_funding
Revises: 0005_teacher_detail
Create Date: 2026-11-02 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_funding'
down_revision = '0005_teacher_detail'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'funding_imports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=False),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.Column('generated_at', sa.String(), nullable=True),
        sa.Column('years_json', sa.Text(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
    )
    op.create_table(
        'funding_projects',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('funding_import_id', sa.Integer(), sa.ForeignKey('funding_imports.id'), nullable=False),
        sa.Column('row', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('main_category', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('serial', sa.String(), nullable=True),
        sa.Column('nstc_no', sa.String(), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('pi', sa.String(), nullable=True),
        sa.Column('pi_college', sa.String(), nullable=True),
        sa.Column('pi_dept', sa.String(), nullable=True),
        sa.Column('exec_college', sa.String(), nullable=True),
        sa.Column('exec_dept', sa.String(), nullable=True),
        sa.Column('sponsor', sa.String(), nullable=True),
        sa.Column('amount', sa.String(), nullable=False),
    )
    op.create_index('ix_funding_projects_import_year', 'funding_projects', ['funding_import_id', 'year'])


def downgrade() -> None:
    op.drop_index('ix_funding_projects_import_year', table_name='funding_projects')
    op.drop_table('funding_projects')
    op.drop_table('funding_imports')
