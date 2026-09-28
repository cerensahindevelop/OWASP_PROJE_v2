r"""generic secret assignment tirnakli anahtar (JSON/TOML) destegi

Revision ID: 5a1c9e7d4b62
Revises: 41f61751e3c7
Create Date: 2026-08-10 00:00:00.000000

Bulunan bosluk: generic_secret_assignment kurali anahtar kelimeyle ayirac
(":"/"=") arasinda SADECE bosluk kabul ediyordu (`\b\s*[:=]\s*`). JSON'da
anahtarlar da tirnaklidir (orn. `"password": "S3cr3t..."`) - anahtarin
kapanis tirnagi ayiractan hemen once geldigi icin regex hic tetiklenmiyordu;
gercek export testinde settings.json/service.xml gibi dosyalarda parola
DUZ METIN olarak disari sizdigi gozlemlendi (service.xml'deki XML eleman
formu ayri, kapsam disi - bkz. asagisi).

Fix: anahtar kelime ile ayirac arasina opsiyonel TEK tirnak karakteri
(`['"]?`) eklendi - "password": ve 'password': gibi JSON/TOML tarzi
tirnakli anahtarlari da kapsar, bare (`password=`) ve YAML tarzi
(`password:`) davranisi DEGISMEDEN kalir.

ONEMLI: bu fix TEK BASINA yeterli degildi - eslesme artik anahtarin kendi
tirnak ciftinin ICINDEN basliyor (orn. `"password"` tirnaklarinin arasindaki
'p' harfinden), token_boundary_validator._find_enclosing_string_literal ise
satirdaki ILK kesisen tirnak ciftini donduruyordu - bu da (yanlislikla)
anahtarin kendisini "deger" sanip SADECE "password" kelimesini maskeleyip
asil parolayi duz metin birakiyordu. Bu yuzden token_boundary_validator.py
ayni degisiklikte, satirdaki SON (en sagdaki) kesisen tirnak ciftini sececek
sekilde duzeltildi (bkz. o dosyadaki degisiklik) - boylece iki tirnak cifti
(anahtarin ve degerin) cakistiginda dogru olan (degerin) kazanir.

XML eleman formu (`<password>deger</password>`) BILEREK bu fix'in kapsami
DISINDA birakildi: rule_engine eslesmenin TAMAMINI tek placeholder ile
degistirir (alt-grup/ic-span degistirme desteklenmiyor) - bir XML etiket
ciftini de kapsayan bir eslesme, degeri degil TUM "<tag>deger</tag>"
bloğunu tek placeholder'a cevirip XML sozdizimini bozardı. Bu format icin
duzgun destek, kural motoruna alt-grup/ic-span replacement yetenegi
eklenmesini gerektirir - kapsam disi, ayri bir calisma olarak birakildi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a1c9e7d4b62'
down_revision: Union[str, None] = '41f61751e3c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("aciklama", sa.Text),
)

_OLD_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
    r"(?:_[A-Za-z0-9]+)*\b\s*[:=]\s*"
    r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)
_NEW_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
    r"(?:_[A-Za-z0-9]+)*\b['\"]?\s*[:=]\s*"
    r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_NEW_REGEX,
            aciklama="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
            "bilesik tanimlayicilar dahil, JSON/TOML tirnakli anahtar formu dahil) atama kalibi.",
        )
    )


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_OLD_REGEX,
            aciklama="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
            "bilesik tanimlayicilar dahil) atama kalibi.",
        )
    )
