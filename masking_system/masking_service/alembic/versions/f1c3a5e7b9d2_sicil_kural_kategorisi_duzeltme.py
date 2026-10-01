"""Sicil kurali kategorisi: personnel_no -> sicil_no (sizinti duzeltmesi).

Revision ID: f1c3a5e7b9d2
Revises: e3a7c1f9d2b5
Create Date: 2026-10-01

Ilk kurulum verisi (9f21a6b8e4c3) sicil parametrik kuralini
`kural_adi=kategori='personnel_no'` ile olusturuyordu. Export ise kullanicinin
sicil degerini `sicil_no` anahtariyla verir (app/services/runtime_params.py)
ve rule_engine parametrik kurali `runtime_params.get(kategori)` ile esler.
Sonuc: alembic ile kurulan DB'lerde sicil degeri ne icerikte ne yolda
maskeleniyordu.

Kapsam ve guvenceler:
- YALNIZCA ilk kurulum verisinden gelen ve icerigi degistirilmemis kayit
  duzeltilir. Imza: ad, kategori, desen tipi, bos regex/bayrak/dogrulayici/
  entity, yer tutucu oneki `mask_personel_no`, kaynak katman, allow-list
  degil, seed aciklamasi. `aktif_mi` ve `oncelik` operasyonel ayardir: imzaya
  dahil degildir ve DEGISTIRILMEZ (kullanici kurali pasif yaptiysa pasif kalir).
- Idempotent: duzeltilmis ya da elle (`sicil_no` ile) kurulmus bir DB'de
  eslesen kayit olmadigi icin hicbir sey degismez. `sicil_no` adli bir kural
  zaten varsa (benzersiz ad) seed kaydina dokunulmaz.
- Kayit id'si, yer tutucu oneki ve sayaci degismez: mevcut eslemeler
  (deger_eslemeleri.kural_id) ve daha once uretilmis ciktilarin geri alinmasi
  etkilenmez.
- Eslesmeyen bir `personnel_no` kaydi kalirsa ya da aktif bir `sicil_no`
  parametrik kurali yoksa migrasyon uyari yazar; preflight da ayni durumu
  FAIL olarak raporlar (scripts/check_llm_preflight.py, runtime_rules).
- downgrade: yalnizca bu migrasyonun duzelttigi bicimdeki kaydi (ayni imza,
  seed aciklamasi korunur) geri cevirir; elle kurulmus `sicil_no` kurallarinin
  aciklamasi farkli oldugu icin onlara dokunmaz.

Bilerek app koduna bagimli degildir: migrasyonlar dondurulmus veridir.
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1c3a5e7b9d2"
down_revision: Union[str, None] = "e3a7c1f9d2b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_OLD = "personnel_no"
_NEW = "sicil_no"
# 9f21a6b8e4c3 seed kaydinin (aktif_mi/oncelik disindaki) imzasi.
_SEED_SIGNATURE = """
    desen_tipi = 'parametric'
    AND regex_deseni IS NULL
    AND regex_bayraklari IS NULL
    AND dogrulayici_adi IS NULL
    AND entity_tipi IS NULL
    AND yer_tutucu_on_eki = 'mask_personel_no'
    AND kaynak_katman = 'katman1'
    AND allow_list_mi = 0
    AND kurumsal_terim_silinme_tarihi IS NULL
    AND aciklama = 'Personel numarasi - calisma zamaninda saglanan literal deger.'
"""


def _rename(source: str, target: str) -> int:
    result = op.get_bind().execute(sa.text(f"""
        UPDATE filtre_kurallari
        SET kural_adi = :target, kategori = :target
        WHERE kural_adi = :source AND kategori = :source AND {_SEED_SIGNATURE}
          AND NOT EXISTS (SELECT 1 FROM filtre_kurallari WHERE kural_adi = :target)
    """), {"source": source, "target": target})
    return result.rowcount or 0


def upgrade() -> None:
    changed = _rename(_OLD, _NEW)
    bind = op.get_bind()
    leftover = bind.execute(sa.text(
        "SELECT COUNT(*) FROM filtre_kurallari WHERE kategori = :old AND desen_tipi = 'parametric'"
    ), {"old": _OLD}).scalar()
    active_sicil = bind.execute(sa.text(
        "SELECT COUNT(*) FROM filtre_kurallari WHERE kategori = :new AND desen_tipi = 'parametric' "
        "AND aktif_mi = 1"
    ), {"new": _NEW}).scalar()
    logger.info("sicil_kategori_duzeltme duzeltilen=%d", changed)
    if leftover:
        logger.warning(
            "sicil_kategori_duzeltme: %d parametrik 'personnel_no' kurali seed imzasina uymadigi icin "
            "DEGISTIRILMEDI; bu kural hicbir zaman eslesmez. Elle inceleyin.", leftover,
        )
    if not active_sicil:
        logger.warning(
            "sicil_kategori_duzeltme: aktif 'sicil_no' parametrik kurali yok; sicil degeri maskelenmez."
        )


def downgrade() -> None:
    _rename(_NEW, _OLD)
