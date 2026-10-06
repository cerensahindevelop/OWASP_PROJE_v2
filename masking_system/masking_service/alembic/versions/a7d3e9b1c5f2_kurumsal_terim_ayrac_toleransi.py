r"""kurumsal terim sozlugu - birlesik terimlerde ayrac toleransi

Revision ID: a7d3e9b1c5f2
Revises: c2d4e6f8a901
Create Date: 2026-10-06 14:00:00.000000

"DenizKuvvetleri" gibi birlesik yazilmis bir terim, metindeki "Deniz
Kuvvetleri", "deniz_kuvvetleri" ve "deniz-kuvvetleri" yazimlarini
yakalamiyordu; bu gecisler maskelenmeden kaliyordu
(term_upload.corporate_term_pattern ile duzeltildi).

Daha once yuklenmis terimlerin desenleri yukleme aninda uretilip sifreli
saklandigi ve ayni terimin yeniden yuklenmesi mevcut kaydi atladigi icin
bu migration desenleri, saklanan terim metninden yeniden uretir. Deseni
eski formulun urettigi desenle BIREBIR ayni olmayan (elle degistirilmis)
satirlara dokunulmaz.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_value, encrypt_value
from app.services.term_upload import corporate_term_pattern, legacy_corporate_term_pattern


revision: str = "a7d3e9b1c5f2"
down_revision: Union[str, None] = "c2d4e6f8a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("id", sa.Integer),
    sa.column("regex_deseni", sa.Text),
    sa.column("desen_sifreli_mi", sa.Boolean),
    sa.column("kurumsal_terim_sifreli", sa.Text),
)


def _migrate(old_pattern, new_pattern) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            filtre_kurallari_table.c.id,
            filtre_kurallari_table.c.regex_deseni,
            filtre_kurallari_table.c.kurumsal_terim_sifreli,
        ).where(
            filtre_kurallari_table.c.kurumsal_terim_sifreli.is_not(None),
            filtre_kurallari_table.c.desen_sifreli_mi.is_(True),
        )
    ).all()

    for row_id, encrypted_pattern, encrypted_term in rows:
        if encrypted_pattern is None:
            continue
        term = decrypt_value(encrypted_term)
        expected_old, replacement = old_pattern(term), new_pattern(term)
        if expected_old == replacement or decrypt_value(encrypted_pattern) != expected_old:
            continue
        bind.execute(
            filtre_kurallari_table.update()
            .where(filtre_kurallari_table.c.id == row_id)
            .values(regex_deseni=encrypt_value(replacement))
        )


def upgrade() -> None:
    _migrate(legacy_corporate_term_pattern, corporate_term_pattern)


def downgrade() -> None:
    _migrate(corporate_term_pattern, legacy_corporate_term_pattern)
