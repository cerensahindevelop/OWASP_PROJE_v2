r"""kurumsal terim sozlugu - soldan yapisik yazim ve bosluk dizisi toleransi

Revision ID: b8e2f4a6c1d3
Revises: a7d3e9b1c5f2
Create Date: 2026-10-16 16:00:00.000000

"veda" terimi "ETTIVEDA"/"eskiveda" gibi soldan yapisik yazimlari, birlesik
terimler de parcalar arasinda cift bosluk/sekme iceren yazimlari ("Deniz
Kuvvetleri" cift boslukla) yakalamiyordu (term_upload.corporate_term_pattern
ile duzeltildi; soldan yapisik yazim yalnizca 4+ harfli terimlerde).

Desenler yukleme aninda uretilip sifreli saklandigi icin bu migration
desenleri saklanan terim metninden yeniden uretir. Deseni onceki formulun
urettigi desenle BIREBIR ayni olmayan (elle degistirilmis) satirlara
dokunulmaz.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_value, encrypt_value
from app.services.term_upload import corporate_term_pattern, separator_tolerant_corporate_term_pattern


revision: str = "b8e2f4a6c1d3"
down_revision: Union[str, None] = "a7d3e9b1c5f2"
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
    _migrate(separator_tolerant_corporate_term_pattern, corporate_term_pattern)


def downgrade() -> None:
    _migrate(corporate_term_pattern, separator_tolerant_corporate_term_pattern)
