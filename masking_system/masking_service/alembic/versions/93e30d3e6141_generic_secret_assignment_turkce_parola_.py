"""generic secret assignment turkce parola sifre eklendi

Revision ID: 93e30d3e6141
Revises: 64918dac8066
Create Date: 2026-08-06 12:01:44.822513

Kor test raporunda bulunan bosluk: generic_secret_assignment kurali sadece
Ingilizce anahtar kelimeleri taniyordu (api_key/secret/token/password/
passwd). Belge Turkce "Parola: MAVI_KAPI_2026!" gibi bir etiketle
geciyorsa hic eslesmiyordu - bu sistem Turkce kurumsal belgeler icin
kullanildigindan onemli bir bosluk. "parola" ve "sifre"/"şifre" (Turkce
karakterli ve karaktersiz iki yaziliş da) alternatife eklendi; regex'in
geri kalani (bilesik tanimlayici onek/sonek destegi, 3 farkli deger
esleme sekli, "secretary" tarzi yanlis-pozitif korumasi) DEGISMEDI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93e30d3e6141'
down_revision: Union[str, None] = '64918dac8066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("aciklama", sa.Text),
)

_OLD_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd)"
    r"(?:_[A-Za-z0-9]+)*\b\s*[:=]\s*"
    r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)
_NEW_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
    r"(?:_[A-Za-z0-9]+)*\b\s*[:=]\s*"
    r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_NEW_REGEX,
            aciklama="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
            "bilesik tanimlayicilar dahil) atama kalibi.",
        )
    )


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == "generic_secret_assignment")
        .values(
            regex_deseni=_OLD_REGEX,
            aciklama="api_key/secret/token/password iceren (onekli/onekli-sonekli bilesik "
            "tanimlayicilar dahil) atama kalibi.",
        )
    )
