r"""ic domain local ve xmlns namespace allow-list

Revision ID: 64918dac8066
Revises: c7b3f5a8d1e2
Create Date: 2026-08-06 12:01:44.496379

Kor test raporunda bulunan iki bosluk:

1. presidio_internal_domain_name kurali SADECE ".internal" sonekini
   taniyordu (\b[\w\-]+\.internal\b) - kurum ".local" (orn.
   "koral-api.kuzeynet.local") kullaniyorsa hic eslesmiyordu. Presidio'nun
   yerlesik URL taniyicisi da "local"i TLD listesinde barindirmadigi icin
   (bkz. app/services/presidio_detector.py _harden_url_recognizer) bu
   host adlari HICBIR katmandan gecmiyordu. Regex artik coklu alt-etiketi
   (orn. "koral-api.kuzeynet.local" - iki nokta) TUMUYLE yakalayacak
   sekilde ((?:[\w-]+\.)+) genisletildi ve "local" sonege eklendi.

2. Ayni kor testte pom.xml icindeki xmlns="http://maven.apache.org/POM/4.0.0"
   Presidio'nun URL taniyicisi tarafindan maskelenmis, dosyayi bozmustu.
   Bu bir XML NAMESPACE bildirimi - kurumsal veri degil, yapisal/genel
   bilinen bir sema URI'si. Yeni bir allow-list (is_allow_list=True) kurali
   ekleniyor: xmlns(:onek)?="http(s)://..." bicimindeki deger Katman 2
   bulgularini bastirir (bkz. PresidioDetector._find_allow_list_spans).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64918dac8066'
down_revision: Union[str, None] = 'c7b3f5a8d1e2'
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

_OLD_INTERNAL_DOMAIN_REGEX = r"\b[\w\-]+\.internal\b"
_NEW_INTERNAL_DOMAIN_REGEX = r"\b(?:[\w-]+\.)+(?:internal|local)\b"

_XMLNS_ALLOW_LIST_ROW = dict(
    kural_adi="allow_xmlns_namespace_uri",
    kategori="xml_namespace_uri",
    desen_tipi="presidio",
    regex_deseni=r"""\bxmlns(?::[\w.-]+)?\s*=\s*(?:"https?://[^"]*"|'https?://[^']*')""",
    regex_bayraklari="i",
    yer_tutucu_on_eki="mask_xml_ns",
    oncelik=203,
    aktif_mi=True,
    aciklama="XML namespace bildirimi (xmlns=\"http(s)://...\") - yapisal/genel "
    "bilinen sema URI'si, hassas veri degil; Katman 2 URL bulgusunu bastirir.",
    dogrulayici_adi=None,
    kaynak_katman="katman2_presidio",
    entity_tipi="XML_NAMESPACE_URI",
    guven_skoru=1.0,
    allow_list_mi=True,
    desen_sifreli_mi=False,
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "presidio_internal_domain_name")
        .values(
            regex_deseni=_NEW_INTERNAL_DOMAIN_REGEX,
            aciklama="Ic .internal/.local domain/sunucu adi (coklu alt-etiket destekli).",
        )
    )
    op.bulk_insert(filtre_kurallari_table, [_XMLNS_ALLOW_LIST_ROW])


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.delete().where(
            filtre_kurallari_table.c.kural_adi == "allow_xmlns_namespace_uri"
        )
    )
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "presidio_internal_domain_name")
        .values(
            regex_deseni=_OLD_INTERNAL_DOMAIN_REGEX,
            aciklama="Ic .internal domain/sunucu adi.",
        )
    )
