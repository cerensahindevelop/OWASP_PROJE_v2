"""Keep mapping records and a failed run when output publication fails."""
from alembic import op

revision = "b6f209a731cd"
down_revision = "1c04eb3b4eae"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("maskeleme_calismalari") as batch:
        batch.drop_constraint("ck_masking_runs_status", type_="check")
        batch.create_check_constraint("ck_masking_runs_status",
            "durum IN ('in_progress', 'completed', 'completed_with_warnings', 'failed')")


def downgrade():
    op.execute("UPDATE maskeleme_calismalari SET durum='completed_with_warnings' WHERE durum='failed'")
    with op.batch_alter_table("maskeleme_calismalari") as batch:
        batch.drop_constraint("ck_masking_runs_status", type_="check")
        batch.create_check_constraint("ck_masking_runs_status",
            "durum IN ('in_progress', 'completed', 'completed_with_warnings')")
