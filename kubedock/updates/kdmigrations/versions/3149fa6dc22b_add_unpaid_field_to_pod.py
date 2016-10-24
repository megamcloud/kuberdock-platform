"""add unpaid field to pod

Revision ID: 3149fa6dc22b
Revises: 50e4a32fa6c3
Create Date: 2016-10-24 12:41:47.589403

"""

# revision identifiers, used by Alembic.
revision = '3149fa6dc22b'
down_revision = '50e4a32fa6c3'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('pods', sa.Column('unpaid', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('pods', 'unpaid')
