r"""contextual personnel id genisletildi daha fazla varyant

Revision ID: 41f61751e3c7
Revises: 112f965a7763
Create Date: 2026-08-06 13:42:38.483578

Onceki contextual_personnel_id kurali (112f965a7763) SADECE "sicil no" /
"sicil numarası" / "personel no" / "personel numarası" / "employee id"
tam etiketlerinin HEMEN ardindan gelen sayilari yakaliyordu - kor testte
kullanilan gercek belgede etiket farkli yazilmis oldugu icin (orn. "no"/
"numarası" soneki OLMADAN sadece "Sicil: 847215" ya da "Sicil 847215"
gibi) kural hic tetiklenmedi.

Regex genisletildi:
  - "sicil" tek basina (soneksiz) da artik gecerli etiket - "personel"
    kasitli olarak TEK BASINA eklenmedi ("personel maaşı 5000" gibi
    ID-DISI baglamlarda yanlis pozitif riski yuksek; "sicil" kelimesi
    Turkce'de bu riski tasimiyor).
  - Etiket-deger arasi ayrac artik opsiyonel (": "/" "/hicbiri) - "Sicil
    847215" (iki noktasiz) de eslesir.
  - \s zaten satir sonu (\n) da kapsadigi icin "Sicil No:\n847215" gibi
    coklu satira yayilan durumlar onceki surumde de calisiyordu.
  - Ayni etiketin ardindan virgul/slash/"ve" ile ayrilmis BIRDEN FAZLA
    numara da (orn. "Sicil No: 847215, 731904") artik TEK eslesmede
    yakalanip maskelenir.

Bu YINE DE genel bir cozum degil: belgede etiket HIC olmadan (orn. bir
tabloda sadece "Sicil No" basligi bir kez gecip alttaki satirlarda ciplak
sayilar tekrarlaniyorsa) regex bu sayilari BULAMAZ - rakamlarin kendisi
hicbir ayirt edici "sekle" sahip degil (bir tutar, port numarasi, tarih
parcasi ile ayni gorunur). Boyle durumlar icin iki alternatif yol var:
  1) Bilinen sicil numaralarini Kurumsal Terim Sozlugu'ne (bkz.
     app/services/term_file_parser.py) TAM ESLESEN deger olarak
     kaydetmek - o zaman etiketten BAGIMSIZ, metnin HER YERINDE yakalanir.
  2) VLLM_ENABLED=true iken calisan Katman 3 (LLM) tanimasini
     person_name disinda personel-kimlik-no'yu da kapsayacak sekilde
     genisletmek - semantik baglamdan cikarim yapar, tam etiket metnine
     bagimli degildir.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41f61751e3c7'
down_revision: Union[str, None] = '112f965a7763'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("aciklama", sa.Text),
)

_OLD_REGEX = (
    r"\b(?:sicil\s*(?:no|numaras[ıi])|personel\s*(?:no|numaras[ıi])|employee\s*id)"
    r"\s*[:\-]?\s*[0-9]{5,8}\b"
)
_NEW_REGEX = (
    r"\b(?:sicil(?:\s*(?:no|numaras[ıi]))?|personel\s*(?:no|numaras[ıi])|employee\s*id)"
    r"\s*[:\-]?\s*[0-9]{5,8}\b"
    r"(?:\s*(?:,|/|ve)\s*[0-9]{5,8}\b)*"
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "contextual_personnel_id")
        .values(
            regex_deseni=_NEW_REGEX,
            aciklama="Belge icinde 'sicil'/'sicil no'/'personel no'/'employee id' etiketiyle "
            "gecen (soneksiz/ayracsiz/coklu-numarali varyantlar dahil) personel kimlik "
            "numarasi (etiket sayiyla birlikte maskelenir).",
        )
    )


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "contextual_personnel_id")
        .values(
            regex_deseni=_OLD_REGEX,
            aciklama="Belge icinde 'sicil no'/'personel no'/'employee id' etiketiyle gecen "
            "personel kimlik numarasi (etiket sayiyla birlikte maskelenir).",
        )
    )
