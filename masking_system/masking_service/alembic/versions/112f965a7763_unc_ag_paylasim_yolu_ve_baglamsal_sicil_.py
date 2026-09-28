r"""unc ag paylasim yolu ve baglamsal sicil no kurallari

Revision ID: 112f965a7763
Revises: 93e30d3e6141
Create Date: 2026-08-06 12:01:45.163624

Kor test raporunda bulunan iki tespit bosluğu icin YENI Katman 1 regex
kurallari (mevcut hicbir kurali degistirmez):

1. unc_network_path: Windows UNC ag paylasim yolu (\\host\pay\alt-yol) -
   daha once hicbir kural bu formati hedeflemiyordu.

2. contextual_personnel_id: metin icinde gecen (mevcut kullanicinin KENDI
   sicil numarasindan FARKLI, orn. bir raporda listelenen baska personelin)
   sicil/personel numaralari. Mevcut "sicil_no" kurali (bkz. SEED_RULES)
   pattern_type="parametric" - SADECE o an islemi yapan kullanicinin kendi
   girdigi degeri maskeler, belge icindeki baska sayilari degil. Yanlis
   pozitif riskini dusuk tutmak icin BAGLAMSIZ (bare 5-8 haneli sayi) bir
   desen yerine, "sicil no"/"personel no"/"employee id" gibi acik bir
   etiketin HEMEN ONUNDE gecen sayilarla sinirlandirildi - regex motoru
   sadece tam eslesen araligi (bkz. rule_engine.py Match.start/end,
   grup degil TUM eslesme) placeholder ile degistirdigi icin etiket de
   sayiyla BIRLIKTE maskelenir (generic_secret_assignment kuraliyla ayni
   davranis).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '112f965a7763'
down_revision: Union[str, None] = '93e30d3e6141'
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
        kural_adi="unc_network_path",
        kategori="network_path",
        desen_tipi="regex",
        regex_deseni=r"\\\\[\w.-]+(?:\\[\w.$ -]+)+",
        regex_bayraklari=None,
        yer_tutucu_on_eki="mask_ag_yolu",
        oncelik=26,
        aktif_mi=True,
        aciklama="Windows UNC ag paylasim yolu (\\\\host\\pay\\alt-yol).",
        dogrulayici_adi=None,
        kaynak_katman="katman1",
        entity_tipi=None,
        guven_skoru=0.85,
        allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="contextual_personnel_id",
        kategori="personel_kimlik_no",
        desen_tipi="regex",
        regex_deseni=r"\b(?:sicil\s*(?:no|numaras[ıi])|personel\s*(?:no|numaras[ıi])|employee\s*id)\s*[:\-]?\s*[0-9]{5,8}\b",
        regex_bayraklari="i",
        yer_tutucu_on_eki="mask_personel_kimlik",
        oncelik=52,
        aktif_mi=True,
        aciklama="Belge icinde 'sicil no'/'personel no'/'employee id' etiketiyle gecen "
        "personel kimlik numarasi (etiket sayiyla birlikte maskelenir).",
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
            filtre_kurallari_table.c.kural_adi.in_(
                ["unc_network_path", "contextual_personnel_id"]
            )
        )
    )
