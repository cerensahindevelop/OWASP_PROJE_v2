r"""xml element secret kurali ekle

Revision ID: 8d2f6c19a4e7
Revises: 5a1c9e7d4b62
Create Date: 2026-08-10 00:10:00.000000

Kor test raporunda bulunan tespit bosluğu: generic_secret_assignment (ve
onun tirnakli-anahtar varyanti - bkz. 5a1c9e7d4b62) sadece "anahtar
kelime [:=] deger" formatini kapsar. XML/HTML'in eleman-govdesi formati
(`<password>deger</password>`) bu kaliba hic uymaz - gercek export
testinde service.xml gibi bir dosyada parola DUZ METIN sizdigi gozlemlendi.

Bu YENI kural, `(?P<deger>...)` adli bir named group kullanir -
rule_engine.find_matches_compiled() (bkz. o dosyadaki eslesen degisiklik)
boyle bir grup gordugunde eslesme span'ini SADECE bu ic gruba daraltir;
`<password>`/`</password>` etiketleri metinde OLDUGU GIBI kalir, TEK
placeholder tum elemani (etiketler dahil) yutup XML sozdizimini bozmaz.

Kapsam disi (kasitli): SQL'in pozisyonel `INSERT ... VALUES (...)` formati
(orn. `password) VALUES ('...', 'gizli')`) bu migration'a DAHIL DEGIL -
kolon adi degerden cok uzakta/ayrik oldugu icin regex ile guvenli
(yanlis-pozitifsiz) hedeflenemiyor; bu format icin Katman 2/3 (Presidio/LLM)
semantik tespiti gerekir.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d2f6c19a4e7'
down_revision: Union[str, None] = '5a1c9e7d4b62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("kategori", sa.String),
    sa.column("desen_tipi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("regex_bayraklari", sa.String),
    sa.column("yer_tutucu_on_eki", sa.String),
    sa.column("oncelik", sa.Integer),
    sa.column("aktif_mi", sa.Boolean),
    sa.column("aciklama", sa.Text),
    sa.column("dogrulayici_adi", sa.String),
    sa.column("kaynak_katman", sa.String),
    sa.column("entity_tipi", sa.String),
    sa.column("guven_skoru", sa.Float),
    sa.column("allow_list_mi", sa.Boolean),
    sa.column("desen_sifreli_mi", sa.Boolean),
)

_NEW_ROWS = [
    dict(
        kural_adi="xml_element_secret",
        kategori="secret",
        desen_tipi="regex",
        regex_deseni=(
            r"<((?:[A-Za-z][\w.-]*:)?[\w.-]*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
            r"(?![a-zçğıöşü])(?:[_.:-][\w.-]*)?)>"
            r"(?P<deger>[^<>&]{8,})</\1>"
        ),
        regex_bayraklari="i",
        yer_tutucu_on_eki="mask_secret",
        oncelik=21,
        aktif_mi=True,
        aciklama="XML/HTML eleman govdesinde gecen api_key/secret/token/password/parola/sifre "
        "degeri (orn. <password>deger</password>) - sadece ic deger degistirilir, etiketler korunur.",
        dogrulayici_adi=None,
        kaynak_katman="katman1",
        entity_tipi=None,
        guven_skoru=0.85,
        allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
]


def upgrade() -> None:
    op.bulk_insert(filtre_kurallari_table, _NEW_ROWS)


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.delete().where(
            filtre_kurallari_table.c.kural_adi == "xml_element_secret"
        )
    )
