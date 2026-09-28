"""Kurumsal terim listeleme ve gecmisi koruyan soft-delete alanlari.

Revision ID: d4e8a1b7c2f9
Revises: 3f7b1e9c6a24
Create Date: 2026-08-14 16:10:00.000000

Kurumsal terimler daha once yalnizca sifreli, boundary ile sarilmis regex
deseni olarak tutuluyordu. Bu migration kullanicinin ekledigi literal terimi
ayri bir sifreli kolona backfill eder. Silme fiziksel DELETE degildir: eski
deger_eslemeleri.kural_id baglantilari ve restore gecmisi korunur.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_value, encrypt_value
from app.services.rule_engine import TR_LOWER_CLASS, TR_UPPER_CLASS, compound_aware_boundary_pattern


revision: str = "d4e8a1b7c2f9"
down_revision: Union[str, None] = "3f7b1e9c6a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RULE_NAME_PREFIX = "kurumsal_terim_"
_TERM_CONNECTOR_CLASS = f"0-9{TR_LOWER_CLASS}{TR_UPPER_CLASS}"
_SENTINEL = "CORPORATE_TERM_SENTINEL_Q91"
_TEMPLATE = compound_aware_boundary_pattern(
    _SENTINEL,
    connector_class=_TERM_CONNECTOR_CLASS,
)
_LEFT, _RIGHT = _TEMPLATE.split(_SENTINEL, 1)


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
    op.add_column(
        "filtre_kurallari",
        sa.Column("kurumsal_terim_sifreli", sa.Text(), nullable=True),
    )
    op.add_column(
        "filtre_kurallari",
        sa.Column("kurumsal_terim_silinme_tarihi", sa.DateTime(timezone=True), nullable=True),
    )

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
        )
    ).all()
    for row_id, encrypted_pattern in rows:
        if not encrypted_pattern:
            continue
        pattern = decrypt_value(encrypted_pattern)
        if not pattern.startswith(_LEFT) or not pattern.endswith(_RIGHT):
            continue
        escaped_term = pattern[len(_LEFT) : len(pattern) - len(_RIGHT)]
        term = _unescape_literal(escaped_term)
        bind.execute(
            table.update()
            .where(table.c.id == row_id)
            .values(kurumsal_terim_sifreli=encrypt_value(term))
        )


def downgrade() -> None:
    with op.batch_alter_table("filtre_kurallari") as batch_op:
        batch_op.drop_column("kurumsal_terim_silinme_tarihi")
        batch_op.drop_column("kurumsal_terim_sifreli")
