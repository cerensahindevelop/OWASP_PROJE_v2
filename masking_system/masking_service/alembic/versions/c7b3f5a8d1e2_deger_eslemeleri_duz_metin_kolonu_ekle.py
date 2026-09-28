"""deger_eslemeleri.orijinal_deger_duz_metin kolonu ekle

Revision ID: c7b3f5a8d1e2
Revises: 9f21a6b8e4c3
Create Date: 2026-08-05 12:00:00.000000

BILEREK GUVENLIK GEVSETMESI - proje sahibinin acik talebiyle eklendi:
"neyin nasil maskelendigi" DB'ye dogrudan erisen biri (DataGrip, DB
dosyasinin kendisi) tarafindan sifre cozmeden gorulebilsin diye
orijinal_deger_sifreli'nin YANINA duz metin bir kopya eklendi.

GUVENLIK UYARISI (bkz. app/db/models.py ValueMapping.original_value_plain
kolon yorumu): bu, orijinal_deger_sifreli'nin sagladigi "DB dosyasina
erisen SECURITY_ENCRYPTION_KEY olmadan sirlari okuyamaz" garantisini
TAMAMEN ORTADAN KALDIRIR. Bu tabloya erisen HERKES tum maskelenmis
sirlari (API key, parola, TC kimlik no vb.) dogrudan okuyabilir.

server_default='' SADECE SQLite'in "NOT NULL kolon eklerken DEFAULT
zorunlu" kisitlamasini karsilamak icin var - uygulama (mapping_service.
get_or_create_mapping) her satirda gercek degeri ACIKCA saglar, bu
varsayilan pratikte hic tetiklenmez (migration aninda deger_eslemeleri
tablosu zaten BOSTU).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7b3f5a8d1e2"
down_revision: Union[str, None] = "9f21a6b8e4c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deger_eslemeleri",
        sa.Column(
            "orijinal_deger_duz_metin", sa.Text(), nullable=False, server_default="",
            comment="BILEREK DUZ METIN: orijinal hassas degerin sifrelenmemis hali - bkz. bu "
            "migration'in ve app/db/models.py ValueMapping.original_value_plain'in guvenlik uyarisi.",
        ),
    )
    with op.batch_alter_table("deger_eslemeleri") as batch_op:
        batch_op.alter_column("orijinal_deger_duz_metin", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("deger_eslemeleri") as batch_op:
        batch_op.drop_column("orijinal_deger_duz_metin")
