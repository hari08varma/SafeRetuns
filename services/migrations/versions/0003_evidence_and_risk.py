"""evidence and risk

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03 13:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('address', sa.Column('address_index', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_address_address_index'), 'address', ['address_index'], unique=False)
    op.add_column('product', sa.Column('image_phashes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('evidence', sa.Column('thumb_uri', sa.Text(), nullable=True))
    op.add_column('evidence', sa.Column('sha256', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_evidence_sha256'), 'evidence', ['sha256'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_evidence_sha256'), table_name='evidence')
    op.drop_column('evidence', 'sha256')
    op.drop_column('evidence', 'thumb_uri')
    op.drop_column('product', 'image_phashes')
    op.drop_index(op.f('ix_address_address_index'), table_name='address')
    op.drop_column('address', 'address_index')
