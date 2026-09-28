r"""Legacy \b...\b kurumsal terimlerini listeleme alanina backfill et.

Revision ID: e6f1c9a4b2d7
Revises: d4e8a1b7c2f9
Create Date: 2026-08-14 16:15:00.000000

Ilk kurumsal-terim surumu literal ifadeyi ``\\b<escaped-term>\\b`` olarak
sakliyordu. Daha yeni compound-aware desenleri d4e8 migration'inda tasindi;
bu takip migration'i eski bicimde kalmis kayitlari kayipsiz tamamlar.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_value, encrypt_value


revision: str = "e6f1c9a4b2d7"
down_revision: Union[str, None] = "d4e8a1b7c2f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RULE_NAME_PREFIX = "kurumsal_terim_"


def _unescape_literal(escaped_value: str) -> str:
    chars: list[str] = []
    index = 0
    while index < len(escaped_value):
        if escaped_value[index] == "\\" and index + 1 < len(escaped_value):
            index += 1
        chars.append(escaped_value[index])
        index += 1
    return "".join(chars)


def upgrade() -> None:
    bind = op.get_bind()
    table = sa.table(
        "filtre_kurallari",
        sa.column("id", sa.Integer),
        sa.column("kural_adi", sa.String),
        sa.column("regex_deseni", sa.Text),
        sa.column("desen_sifreli_mi", sa.Boolean),
        sa.column("kurumsal_terim_sifreli", sa.Text),
    )
    rows = bind.execute(
        sa.select(table.c.id, table.c.regex_deseni).where(
            table.c.kural_adi.like(f"{_RULE_NAME_PREFIX}%"),
            table.c.desen_sifreli_mi.is_(True),
            table.c.kurumsal_terim_sifreli.is_(None),
        )
    ).all()
    for row_id, encrypted_pattern in rows:
        if not encrypted_pattern:
            continue
        pattern = decrypt_value(encrypted_pattern)
        if not pattern.startswith(r"\b") or not pattern.endswith(r"\b"):
            continue
        term = _unescape_literal(pattern[2:-2])
        bind.execute(
            table.update()
            .where(table.c.id == row_id)
            .values(kurumsal_terim_sifreli=encrypt_value(term))
        )


def downgrade() -> None:
    # d4e8 kolonlari kaldigi surece literal degeri silmek gereksiz veri kaybi
    # yaratir; downgrade semayi degistirmez.
    pass
