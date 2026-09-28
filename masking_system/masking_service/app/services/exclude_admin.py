"""Read-side access to exclude_patterns for the scan pipeline.

Asama 2 / Adim 5: bu modulun eskiden var olan yazma/yonetim API'si
(add_exclude_pattern/set_exclude_pattern_active/list_exclude_patterns)
silindi - hicbir CLI komutu ya da webapp ekrani onlari cagirmiyordu (bkz.
rule_admin.py'nin CLI'da tam karsiligi var, exclude_admin'in yoktu). Bu
silmenin sonucu: exclude_patterns yonetimi artik SADECE migration/
seed_data.py ile yapilabilir - "kural = veri, kod degisikligi gerekmez"
ilkesi bu tablo icin gecerliligini yitirdi (bilinen, kabul edilen trade-off).
"""

from __future__ import annotations

# sqlalchemy: exclude_patterns tablosunu okumak icin kullanilan DB sorgu araclari.
from sqlalchemy import select
from sqlalchemy.orm import Session

# ExcludePattern: DB modeli (satir). ExcludeSpec: exclude_engine'in kullandigi
# DB'siz/saf karsiligi - bu modul ikisi arasindaki cevirmeni yapar.
from app.db.models import ExcludePattern
from app.services.exclude_engine import ExcludeSpec


# Veritabanindaki aktif tum haric tutma desenlerini okuyup, exclude_engine'in
# kullanacagi ExcludeSpec listesine cevirir (rule_admin/mapping_service'teki
# load_active_rules ile ayni desen).
def load_active_exclude_specs(db: Session) -> list[ExcludeSpec]:
    rows = db.scalars(
        select(ExcludePattern).where(ExcludePattern.is_active.is_(True))
    ).all()
    return [
        ExcludeSpec(
            id=r.id,
            pattern_name=r.pattern_name,
            glob_pattern=r.glob_pattern,
            applies_to=r.applies_to,
        )
        for r in rows
    ]
