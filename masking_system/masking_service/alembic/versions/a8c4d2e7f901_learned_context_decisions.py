"""Context scoped learned sensitive and suppression decisions.

Revision ID: a8c4d2e7f901
Revises: e6f1c9a4b2d7
Create Date: 2026-09-09 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8c4d2e7f901"
down_revision: Union[str, None] = "e6f1c9a4b2d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ogrenilen_bulgu_kararlari",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("baglam_id", sa.Integer(), sa.ForeignKey("maskeleme_baglamlari.id"), nullable=False),
        sa.Column("karar_tipi", sa.String(20), nullable=False),
        sa.Column("deger_sifreli", sa.Text(), nullable=False),
        sa.Column("deger_hash", sa.String(64), nullable=False),
        sa.Column("varlik_tipi", sa.String(100), nullable=False),
        sa.Column("kapsam_anahtari", sa.String(260), nullable=False),
        sa.Column("kaynak_inceleme_id", sa.Integer(), sa.ForeignKey("gozden_gecirme_kuyrugu.id"), nullable=True),
        sa.Column("aktif_mi", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("olusturulma_tarihi", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("karar_tipi IN ('sensitive', 'suppression')", name="ck_learned_decision_type"),
        sa.UniqueConstraint(
            "baglam_id", "karar_tipi", "deger_hash", "varlik_tipi", "kapsam_anahtari",
            name="uq_learned_decision_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("ogrenilen_bulgu_kararlari")
