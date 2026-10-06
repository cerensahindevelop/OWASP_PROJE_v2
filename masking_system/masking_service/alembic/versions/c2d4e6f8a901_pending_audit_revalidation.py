"""Bound automatic audit revalidation and fence concurrent workers."""
from alembic import op
import sqlalchemy as sa

revision = "c2d4e6f8a901"
down_revision = "f1c3a5e7b9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("denetim_uyarilari", sa.Column("yeniden_dogrulama_anahtari", sa.String(64), nullable=True))
    op.add_column("denetim_uyarilari", sa.Column("yeniden_dogrulama_tokeni", sa.String(32), nullable=True))
    op.add_column("denetim_uyarilari", sa.Column("yeniden_dogrulama_zamani", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.drop_column("yeniden_dogrulama_zamani")
        batch.drop_column("yeniden_dogrulama_tokeni")
        batch.drop_column("yeniden_dogrulama_anahtari")
