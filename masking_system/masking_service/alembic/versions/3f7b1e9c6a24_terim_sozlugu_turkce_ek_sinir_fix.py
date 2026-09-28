r"""kurumsal terim sozlugu - turkce ek siniri duzeltmesi

Revision ID: 3f7b1e9c6a24
Revises: 8d2f6c19a4e7
Create Date: 2026-08-10 01:00:00.000000

Kor test raporunda bulunan tutarlilik bosluğu: Turkce'de bir eke ozel isme
KESME ISARETI OLMADAN dogrudan yapismasi COK yaygin bir yazimdir (dogru
yazim "Poseidon'un" olsa da metinlerde sikca "Poseidonun" gorulur). Kural
motorunun sinir (boundary) regex'i bunu bir "sinir" saymiyordu - bu yuzden
boyle yazilmis COK sayida gecis sessizce maskelenmeden kaliyordu (rule_engine.py
_TR_SUFFIX_TRANSITION ile duzeltildi - bkz. o dosyadaki degisiklik).

Bu fix, `app/services/rule_engine.compound_aware_boundary_pattern` icindeki
SAF kod degisikligidir - parametrik kurallar (proje adi/sicil/branch) icin
HICBIR migration'a gerek yoktur (desen her taramada tazeden uretilir). AMA
kurumsal terim sozlugune (Katman 1, "kurumsal_terim_" onekli kurallar)
DAHA ONCE yuklenmis terimlerin regex_deseni'si YUKLEME ANINDA uretilip
Fernet ile SIFRELI olarak DB'ye yazildi - bu satirlar kod duzeltmesinden
otomatik faydalanmaz, YENIDEN YUKLENMEDIKCE eski (eksik) desenle kalirlar.

Bu migration, KULLANICIYA HICBIR TERIMI YENIDEN YUKLETMEDEN mevcut tum
terim kurallarini duzeltir: her deseni cozup (decrypt), sabit (veriye
bagli OLMAYAN) eski sag-sinir son-ekini sabit yeni son-ekle degistirip
tekrar sifreler. Orijinal terim METNINE HIC ihtiyac YOKTUR - sadece
desenin son-eki (data-bagimsiz, sabit bir string) degistirilir; boylece
bu islem "terim yeniden yuklendi" ile BIREBIR AYNI sonucu, terimin duz
metnini hic gormeden/DB'ye hic yazmadan uretir.

Desen bu sabit son-ekle BITMIYORSA (beklenmeyen/elle degistirilmis bir
desen) o satir DOKUNULMADAN atlanir - riskli bir tahminle asla yazilmaz.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_value, encrypt_value


# revision identifiers, used by Alembic.
revision: str = '3f7b1e9c6a24'
down_revision: Union[str, None] = '8d2f6c19a4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RULE_NAME_PREFIX = "kurumsal_terim_"

_TR_LOWER = "a-zçğıöşü"
_TR_UPPER = "A-ZÇĞİÖŞÜ"
_TERM_CONNECTOR_CLASS = f"0-9{_TR_LOWER}{_TR_UPPER}"
_COMPOUND_WORD_TRANSITION = (
    rf"(?:(?:(?-i:(?<=[{_TR_LOWER}])(?=[{_TR_UPPER}]))"
    rf"|(?-i:(?<=[{_TR_UPPER}])(?=[{_TR_UPPER}][{_TR_LOWER}])))"
    rf"|(?:(?<=[0-9])(?=[{_TR_LOWER}{_TR_UPPER}])"
    rf"|(?<=[{_TR_LOWER}{_TR_UPPER}])(?=[0-9])))"
)
_TR_SUFFIX_STARTS = (
    "dan", "den", "tan", "ten",
    "dır", "dir", "dur", "dür", "tır", "tir", "tur", "tür",
    "nın", "nin", "nun", "nün", "ın", "in", "un", "ün",
)
_TR_WORD_CHAR_CLASS = f"0-9_{_TR_LOWER}{_TR_UPPER}"
_TR_SUFFIX_TRANSITION = rf"(?=(?:{'|'.join(_TR_SUFFIX_STARTS)})(?:[^{_TR_WORD_CHAR_CLASS}]|$))"

_OLD_RIGHT_SUFFIX = rf"(?:(?![{_TERM_CONNECTOR_CLASS}])|{_COMPOUND_WORD_TRANSITION})"
_NEW_RIGHT_SUFFIX = rf"(?:(?![{_TERM_CONNECTOR_CLASS}])|{_COMPOUND_WORD_TRANSITION}|{_TR_SUFFIX_TRANSITION})"


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("id", sa.Integer),
    sa.column("kural_adi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("desen_sifreli_mi", sa.Boolean),
)


def _migrate(old_suffix: str, new_suffix: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(filtre_kurallari_table.c.id, filtre_kurallari_table.c.regex_deseni).where(
            filtre_kurallari_table.c.kural_adi.like(f"{_RULE_NAME_PREFIX}%"),
            filtre_kurallari_table.c.desen_sifreli_mi.is_(True),
        )
    ).all()

    for row_id, encrypted_pattern in rows:
        if encrypted_pattern is None:
            continue
        plain_pattern = decrypt_value(encrypted_pattern)
        if not plain_pattern.endswith(old_suffix):
            # Beklenmeyen/elle degistirilmis desen - dokunmadan atla.
            continue
        migrated_pattern = plain_pattern[: -len(old_suffix)] + new_suffix
        bind.execute(
            filtre_kurallari_table.update()
            .where(filtre_kurallari_table.c.id == row_id)
            .values(regex_deseni=encrypt_value(migrated_pattern))
        )


def upgrade() -> None:
    _migrate(_OLD_RIGHT_SUFFIX, _NEW_RIGHT_SUFFIX)


def downgrade() -> None:
    _migrate(_NEW_RIGHT_SUFFIX, _OLD_RIGHT_SUFFIX)
