from __future__ import annotations

from typing import Protocol

# Sorgu insasi (select) ve DB session tipi icin cekirdek SQLAlchemy bilesenleri.
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FileCategoryRestriction


# FileCategoryRestriction (dosya_tipi_kategori_kisitlamasi) tablosuna erisimin
# soyut sozlesmesi.
class FileCategoryRestrictionRepository(Protocol):
    # Aktif dosya-uzantisi -> izinli Presidio kategorisi kisitlamalarini dondurur.
    def load_active_restrictions(self) -> dict[str, list[str]]:
        ...


# FileCategoryRestrictionRepository sozlesmesinin SQLAlchemy implementasyonu.
class SqlAlchemyFileCategoryRestrictionRepository:
    # Cagiran taraftan gelen aktif DB session'ini saklar.
    def __init__(self, db: Session) -> None:
        self.db = db

    # Aktif kisitlamalari {dosya_uzantisi: [izinli_kategoriler]} sozlugune cevirir.
    def load_active_restrictions(self) -> dict[str, list[str]]:
        rows = self.db.scalars(
            select(FileCategoryRestriction).where(FileCategoryRestriction.is_active.is_(True))
        ).all()
        restrictions: dict[str, list[str]] = {}
        for row in rows:
            extension = row.file_extension.lower().lstrip(".")
            restrictions.setdefault(extension, []).append(row.allowed_category)
        return restrictions
