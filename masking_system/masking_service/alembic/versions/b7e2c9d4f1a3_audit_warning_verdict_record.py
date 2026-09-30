"""Store the post-mask LLM audit verdict with each quarantined file.

Releasing byte-identical content reuses the recorded verdict instead of
sampling the model again (same content, same decision).
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e2c9d4f1a3"
down_revision = "c4f8a2d6e1b9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.add_column(sa.Column("denetim_sonucu", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.drop_column("denetim_sonucu")
