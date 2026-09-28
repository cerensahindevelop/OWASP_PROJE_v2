"""Store the masked output path of quarantined files.

Released files must be written to the masked path, never to the source path.
"""
from alembic import op
import sqlalchemy as sa

revision = "a3d5e7f9b1c2"
down_revision = "f2a9c7d1e6b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.add_column(sa.Column("cikti_yolu", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("denetim_uyarilari") as batch:
        batch.drop_column("cikti_yolu")
