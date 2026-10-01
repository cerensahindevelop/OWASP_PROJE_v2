"""Store which check kept each quarantined file out of the export output.

Nullable, additive only: existing warnings keep NULL (reported as 'bilinmiyor').
"""
from alembic import op
import sqlalchemy as sa

revision = "e3a7c1f9d2b5"
down_revision = "b7e2c9d4f1a3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.add_column(sa.Column("basarisiz_kontrol", sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.drop_column("basarisiz_kontrol")
