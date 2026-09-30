r"""generic_secret_assignment: kisa parolalar ve ek anahtar kelimeler

Revision ID: c4f8a2d6e1b9
Revises: a3d5e7f9b1c2

Bulunan bosluk: kural degeri en az 12 karakter istiyordu. LLM kapaliyken
(ya da LLM kacirdiginda) `password = "Sup3rGizli!"`, `DB_PASSWORD=abc123`,
`{"Password": "..."}`, `sifre=...` ve kisa parolali connection string
(`...;Password=Sup3rGizli!;`) maskelenmeden disari cikiyordu. `pwd`, `pass`,
`passphrase`, `credential` anahtarlari hic taninmiyordu.

Degisiklik:
- Deger alt siniri 12 -> 6 karakter.
- Anahtar kelimeler: pwd, pass, passphrase, credentials?, sifresi, parolasi
  eklendi (onek/sonek ve tirnakli anahtar destegi aynen).
- Anahtar ile deger arasi satir sonunu gecmez (`token:\n    entities` eslesmez).
- Parola olmayan ayar anahtarlari eslesmez: sonek bir olcu/ayar ise
  (`token_count`, `password_min_length`, `token_url`, `password_file` ...).
- validator_name=secret_value (app/services/validators.py): ortam degiskeni/
  sablon referanslari (`${DB_PASS}`, `%PASSWORD%`, `{{ vault.pw }}`),
  null/true/changeme gibi yer tutucular, bicim yer tutuculari (`{token}`,
  `%s`) reddedilir. Tirnaksiz deger rakam ya da sembol icermiyorsa (kodda
  `token = tokenizer`, tip bildirimi `token: Optional`) reddedilir.
  Bilinen sinir: tirnaksiz ve yalnizca harften olusan parola (`pwd=sunshine`)
  bu kuralla yakalanmaz; LLM katmanina kalir.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f8a2d6e1b9"
down_revision: Union[str, None] = "a3d5e7f9b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("dogrulayici_adi", sa.String),
    sa.column("aciklama", sa.Text),
)

_RULE = "generic_secret_assignment"

_OLD_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
    r"(?:_[A-Za-z0-9]+)*\b['\"]?\s*[:=]\s*"
    r"(?P<deger>\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
)
_OLD_DESCRIPTION = (
    "api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
    "bilesik tanimlayicilar dahil, JSON/TOML tirnakli anahtar formu dahil) atama kalibi - "
    "sadece deger placeholder'a cevrilir, anahtar adi ve ayirac degismez."
)

# Parola olmayan ayar sonekleri (`token_count`, `password_min_length`, `token_url`).
_NON_SECRET_SUFFIXES = (
    "count|length|len|size|limit|max|min|timeout|ttl|expiry|expires|expiration|days|hours|minutes|"
    "seconds|type|url|uri|endpoint|path|file|dir|name|field|header|policy|prefix|suffix|pattern|"
    "regex|enabled|required|mode|id|label|hint|placeholder|prompt|reset|rotation|strength"
)
_NEW_REGEX = (
    r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*"
    r"(?:api[_-]?key|secret|token|password|passwd|passphrase|pwd|pass|credentials?"
    r"|parola|parolas[ıi]|sifre|sifresi|şifre|şifresi)"
    r"(?:_(?!(?:" + _NON_SECRET_SUFFIXES + r")(?![A-Za-z0-9]))[A-Za-z0-9]+)*"
    r"\b['\"]?[ \t]*[:=][ \t]*"
    r"(?P<deger>\"[^\"\n]{6,}\"|'[^'\n]{6,}'|[^\s'\"(){}\[\],;.]{6,})"
)
_NEW_DESCRIPTION = (
    "api_key/secret/token/password/pwd/pass/passphrase/credential/parola/sifre iceren atama "
    "kalibi (onekli/sonekli bilesik tanimlayicilar, tirnakli anahtar ve connection string "
    "alanlari dahil), 6+ karakterlik deger. Ortam degiskeni referanslari, yer tutucular ve kod "
    "degiskenleri secret_value dogrulayicisiyla elenir. Sadece deger placeholder'a cevrilir."
)


def upgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == _RULE)
        .values(regex_deseni=_NEW_REGEX, dogrulayici_adi="secret_value", aciklama=_NEW_DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(
        filtre_kurallari_table.update()
        .where(filtre_kurallari_table.c.kural_adi == _RULE)
        .values(regex_deseni=_OLD_REGEX, dogrulayici_adi=None, aciklama=_OLD_DESCRIPTION)
    )
