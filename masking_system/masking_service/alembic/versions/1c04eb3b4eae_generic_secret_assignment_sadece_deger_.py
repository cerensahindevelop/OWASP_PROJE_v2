r"""generic secret assignment sadece deger degistir

Revision ID: 1c04eb3b4eae
Revises: a8c4d2e7f901
Create Date: 2026-09-16 14:24:35.578310

Bulunan bosluk: generic_secret_assignment kuralinin eslesmesi, anahtar
kelimeden (api_key/secret/token/password/...) itibaren baslar - onceki
tanimlayici parcasi SADECE alt cizgiyle (`_`) birlesikse eslesmeye dahil
edilir (`(?:[A-Za-z0-9]+_)*`). Anahtar tire (`-`) ile birlesikse (orn.
YAML'da yaygin `client-secret:`, `api-key:` gibi kebab-case alan adlari)
bu onek eslesmeye dahil OLMAZ; regex dogrudan "secret" kelimesinden
baslar ve "secret: <deger>" (ya da "key: <deger>") kismini TEK bir
eslesme olarak yakalar.

rule_engine.find_matches_compiled ise "deger" adli bir grup yoksa
eslesmenin TAMAMINI tek placeholder ile degistirir. Bu iki davranisin
bilesimi, "client-secret: SYNTHETIC-CLIENT-SECRET-9842" gibi bir satiri
"client-" onekine DOKUNMADAN "secret: SYNTHETIC-CLIENT-SECRET-9842"
kismini tek placeholder'a cevirip "client-mask_secret_88" haline
getiriyor - iki nokta ust uste (":") kayboluyor ve YAML/JSON gibi
`anahtar: deger` sozdizimine dayali formatlar bozuluyor (export sonrasi
syntax_parsers.parse_document "YAML sozdizimi hatasi" ile yakaliyor).

Fix: onceki 5a1c9e7d4b62/93e30d3e6141 migration'larinda oneki
alt-cizgi-disi ayiraclari (`-` gibi) da kapsayacak sekilde genisletmek
denenebilirdi, ama bu durumda TUM "anahtar: deger" cifti (anahtar dahil)
tek placeholder'a donusur ve iki nokta ust uste yine kaybolur - kok
neden onek degil, "deger" grubunun eksikligidir. Bunun yerine deger
kismi rule_engine'in zaten destekledigi `(?P<deger>...)` adli grupla
sarmalandi (bkz. xml_element_secret kuralindaki ayni yaklasim ve
rule_engine.py:11-20 docstring'i) - artik SADECE deger placeholder'a
donusuyor, anahtar adi ve ayiraç (":"/"=", tirnaklar dahil) OLDUGU GIBI
kaliyor: "client-secret: SYNTHETIC-CLIENT-SECRET-9842" ->
"client-secret: mask_secret_88" (gecerli YAML). Regex'in geri kalani
(anahtar kelime listesi, onek/sonek destegi, tirnakli anahtar destegi,
deger uzunluk/format alternatifleri) DEGISMEDI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c04eb3b4eae'
down_revision: Union[str, None] = 'a8c4d2e7f901'
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
    r"(?:_[A-Za-z0-9]+)*\b['\"]?\s*[:=]\s*"
    r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)
_NEW_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
    r"(?:_[A-Za-z0-9]+)*\b['\"]?\s*[:=]\s*"
    r"(?P<deger>\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_NEW_REGEX,
            aciklama="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
            "bilesik tanimlayicilar dahil, JSON/TOML tirnakli anahtar formu dahil) atama kalibi - "
            "sadece deger placeholder'a cevrilir, anahtar adi ve ayirac degismez.",
        )
    )


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_OLD_REGEX,
            aciklama="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
            "bilesik tanimlayicilar dahil, JSON/TOML tirnakli anahtar formu dahil) atama kalibi.",
        )
    )
