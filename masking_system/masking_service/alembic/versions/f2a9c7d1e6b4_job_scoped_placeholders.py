"""Scope new placeholder mappings and counters to each masking job.

Existing mappings remain in the NULL job scope for historical restores.
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a9c7d1e6b4"
down_revision = "b6f209a731cd"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("maskeleme_calismalari", sa.Column("esleme_surumu", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("deger_eslemeleri") as batch:
        batch.add_column(sa.Column("calisma_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_mapping_job", "maskeleme_calismalari", ["calisma_id"], ["id"], ondelete="CASCADE")
        batch.drop_constraint("uq_mapping_context_original", type_="unique")
        batch.drop_constraint("uq_mapping_context_placeholder", type_="unique")
        batch.create_unique_constraint("uq_mapping_job_original", ["calisma_id", "orijinal_deger_hash"])
        batch.create_unique_constraint("uq_mapping_job_placeholder", ["calisma_id", "yer_tutucu_degeri"])
    op.create_index("uq_mapping_legacy_original", "deger_eslemeleri", ["baglam_id", "orijinal_deger_hash"], unique=True, sqlite_where=sa.text("calisma_id IS NULL"))
    op.create_index("uq_mapping_legacy_placeholder", "deger_eslemeleri", ["baglam_id", "yer_tutucu_degeri"], unique=True, sqlite_where=sa.text("calisma_id IS NULL"))
    op.create_table(
        "islem_yer_tutucu_sayaclari",
        sa.Column("calisma_id", sa.Integer(), sa.ForeignKey("maskeleme_calismalari.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("on_ek", sa.String(50), primary_key=True),
        sa.Column("sayac", sa.Integer(), nullable=False),
    )


def downgrade():
    # A context can now contain several different values named mask_ip_1.
    # Refuse a downgrade that would lose or reinterpret those mappings.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM maskeleme_calismalari WHERE esleme_surumu = 2")):
        raise RuntimeError("Islem bazli kayitlar varken geri migration veri kaybettirir; yedekten geri donun.")
    op.drop_table("islem_yer_tutucu_sayaclari")
    op.drop_index("uq_mapping_legacy_original", table_name="deger_eslemeleri")
    op.drop_index("uq_mapping_legacy_placeholder", table_name="deger_eslemeleri")
    with op.batch_alter_table("deger_eslemeleri") as batch:
        batch.drop_constraint("uq_mapping_job_original", type_="unique")
        batch.drop_constraint("uq_mapping_job_placeholder", type_="unique")
        batch.drop_constraint("fk_mapping_job", type_="foreignkey")
        batch.drop_column("calisma_id")
        batch.create_unique_constraint("uq_mapping_context_original", ["baglam_id", "orijinal_deger_hash"])
        batch.create_unique_constraint("uq_mapping_context_placeholder", ["baglam_id", "yer_tutucu_degeri"])
    op.drop_column("maskeleme_calismalari", "esleme_surumu")
