"""Kurumsal terim sozlugu toplu yukleme - kural uretimi, kategori
dogrulamasi ve onizleme (Adim 4-5). Atomik yazma (Adim 6) bu modulun
uzerine insa edilecek.

Her terim, mevcut Katman 1 (regex) motorunun anladigi sekilde SIRADAN bir
FilterRule satirina cevrilir - rule_engine.compile_rules/
find_matches_compiled/OverlapResolver/RuleBasedDetector'a HICBIR yeni kod
yolu eklenmez. Tek fark: regex_deseni duz metin degil, Fernet ile sifreli
saklanir (bkz. app/repository/filter_rule_repository.py'nin decrypt
adimi) - cunku bu regex'in kendisi (bitisik-kelime/camelCase sinirlarini
da kapsayan, "Atlas" terimini "AtlasDB" gibi bir tanimlayicinin icinde de
yakalayan bir desen - bkz. rule_engine.compound_aware_boundary_pattern)
kurumun gizli isimlendirme envanterinin bir parcasi.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

# encrypt_value: uretilen regex desenini DB'ye yazmadan once Fernet ile
# sifreler (bkz. modul dokstringi); decrypt_value: find_leaked_terms'in
# son-kontrol taramasi icin aynen geri cozer. FilterRule: bu ozelligin
# yazdigi tablonun ORM modeli. classify_term: her terimin ok/suspicious/
# rejected karari icin. parse_terms_from_file: yuklenen dosyayi (.txt/.csv/
# .xlsx) bellekte terim listesine cevirir.
from app.core.crypto import decrypt_value, encrypt_value
from app.db.models import FilterRule, ValueMapping
from app.services.rule_engine import (
    PLACEHOLDER_RE,
    TR_LOWER_CLASS,
    TR_UPPER_CLASS,
    _compile_flags,
    compound_aware_boundary_pattern,
    diacritic_tolerant_escape,
)
from app.services.identifier_parts import split_identifier
from app.services.term_classifier import classify_term
from app.services.term_file_parser import parse_terms_from_file
from app.services.term_search import (
    compact_text, compact_view, find_compact, is_mixed_case_glued, structural_parts,
)
from app.services.placeholder_policy import CORPORATE_PLACEHOLDER_PREFIX, CORPORATE_RULE_PREFIXES, is_corporate_rule
from app.services.path_placeholders import PathPlaceholderResolver

_RULE_NAME_PREFIX = "kurumsal_terim_"
_MAX_CATEGORY_SLUG_LEN = 40

# Terimin camelCase/rakam gecisleriyle bitisik gectigi durumlari da
# (orn. "sicilNumarasi", "sicil01") yakalamak icin akilli sinir kullanilir.
# Alt cizgi BILEREK ayrac sayilir ("sicil_no" icindeki "sicil" de eslesir) -
# parametrik kurallardan farkli olarak burada "main_handler" tarzi bir
# tanimlayicayla yanlislikla cakisma riski yok (terimler elle onaylanir).
_TERM_CONNECTOR_CLASS = f"0-9{TR_LOWER_CLASS}{TR_UPPER_CLASS}"


class TermUploadValidationError(ValueError):
    """Dosya ayristirma/siniflandirma DISINDAKI girdi hatalari - orn.
    kategori adi bos ya da baska bir kuralla cakisiyor."""


# Kullanicinin girdigi kategori adini kanonik bir forma (kucuk harf, alt
# cizgi ile ayrilmis) cevirir - boylece 'Proje Kodu'/'proje-kodu'/
# 'PROJE_KODU' hep AYNI kategoriye ('proje_kodu') karsilik gelir.
# Bu dahili gruplama anahtari disari aktarilan placeholder'a yazilmaz.
def normalize_category(raw_category: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", raw_category.strip()).strip("_").lower()
    if not slug:
        raise TermUploadValidationError("kategori adi bos ya da gecersiz")
    return slug[:_MAX_CATEGORY_SLUG_LEN]


# Baslik/proje adi hassas olabilir; tum kategoriler genel bir onek kullanir.
def placeholder_prefix_for_category(category: str) -> str:
    return CORPORATE_PLACEHOLDER_PREFIX


# Normalize edilmis kategoriyi, baska bir ozelligin (seed kurallari, elle
# eklenmis rule_admin kurallari vb.) zaten kullandigi bir kategoriyle
# cakismadigini dogrulamak icin DB'ye karsi kontrol eder. Bu ozelligin
# DAHA ONCE ayni kategoriye eklemis oldugu terimler (rule_name
# 'kurumsal_terim_' ile basliyor) CAKISMA SAYILMAZ - ayni kategoriye
# tekrar yukleme beklenen/desteklenen bir akistir (idempotency).
def validate_category_choice(db: Session, raw_category: str) -> str:
    category = normalize_category(raw_category)
    conflicting = db.scalar(
        select(FilterRule.id)
        .where(
            FilterRule.category == category,
            FilterRule.rule_name.not_like(f"{_RULE_NAME_PREFIX}%"),
        )
        .limit(1)
    )
    if conflicting is not None:
        raise TermUploadValidationError(
            f"'{category}' kategorisi baska bir kural tarafindan zaten kullaniliyor, "
            "farkli bir kategori adi secin"
        )
    return category


# Terim + kategoriden, deterministik (ayni girdi HER ZAMAN ayni sonucu verir) benzersiz bir kural adi uretir.
def rule_name_for_term(category: str, term: str) -> str:
    digest = hashlib.sha256(term.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{_RULE_NAME_PREFIX}{category}_{digest}"


# Tek bir terim icin gerekli tum FilterRule alanlarini tasir.
@dataclass(frozen=True)
class _RuleFields:
    rule_name: str
    category: str
    source_layer: str
    pattern_type: str
    regex_pattern: str
    regex_flags: str
    is_pattern_encrypted: bool
    corporate_term_encrypted: str
    placeholder_prefix: str
    priority: int
    is_active: bool
    description: str


# Birlesik bir terimin harf parcalari arasina istege bagli bir ayrac
# (bosluk, alt cizgi, tire) koyar: "DenizKuvvetleri" terimi "Deniz Kuvvetleri",
# "deniz_kuvvetleri" ve "deniz-kuvvetleri" yazimlarini da yakalar; tersine
# "Deniz Kuvvetleri" terimi bitisik yazimi da yakalar. Rakam gecisleri ve
# baska ayraclar (nokta, egik cizgi) oldugu gibi kalir.
def _separator_tolerant_escape(term: str, separator_pattern: str) -> str:
    parts = split_identifier(term)
    if len(parts) < 2 or parts[0].start != 0 or parts[-1].end != len(term):
        return diacritic_tolerant_escape(term)
    pieces = [diacritic_tolerant_escape(parts[0].text)]
    for left, right in zip(parts, parts[1:]):
        gap = term[left.end:right.start]
        if gap in _TERM_SEPARATORS and left.text[-1].isalpha() and right.text[0].isalpha():
            pieces.append(separator_pattern)
        else:
            pieces.append(diacritic_tolerant_escape(gap))
        pieces.append(diacritic_tolerant_escape(right.text))
    return "".join(pieces)


_TERM_SEPARATORS = ("", " ", "_", "-")
_SINGLE_SEPARATOR_PATTERN = "[ _-]?"
# Parcalar arasinda tek alt cizgi/tire ya da yatay bosluk dizisi (cift bosluk,
# sekme, Word'den gelen bolunmez bosluk). Satir sonu BILEREK yok: kodda bir
# yorum satirinin sonundaki parcayi alt satirdaki koda baglayip tek yer
# tutucuyla degistirmek sozdizimini bozar; satira bolunmus terimleri son
# kontrol (bkz. term_search) yakalar.
_TERM_SEPARATOR_PATTERN = r"(?:[_-]|[ \t\u00a0]+)?"

# Soldan yapisik yazimin ("ETTIVEDA") da eslesmesi icin gereken en az harf
# sayisi. Olcum (2026-10-16, 519 aktif terim, ilgisiz 6.376 dosya): sinirsiz
# serbest birakmak 230 fazladan eslesme uretti; tamami 3 harfli kisaltmalar
# ("TSK" -> "agentSkill") ve salt rakam terimlerdendi ("11111111111" ->
# kart numarasi). 4+ harfte 15'e iniyor.
_GLUED_PREFIX_MIN_LETTERS = 4


def allows_glued_prefix(term: str) -> bool:
    return sum(ch.isalpha() for ch in term) >= _GLUED_PREFIX_MIN_LETTERS


def corporate_term_pattern(term: str) -> str:
    return compound_aware_boundary_pattern(
        _separator_tolerant_escape(term, _TERM_SEPARATOR_PATTERN),
        connector_class=_TERM_CONNECTOR_CLASS,
        free_right_continuation=True,
        free_left_continuation=allows_glued_prefix(term),
    )


# Onceki desen surumleri: yalnizca mevcut kurallari yeni desene tasiyan
# migration'larin "elle degistirilmemis" kontrolu icin.
#
# Tek ayrac toleransi, sol sinir her terimde siki.
def separator_tolerant_corporate_term_pattern(term: str) -> str:
    return compound_aware_boundary_pattern(
        _separator_tolerant_escape(term, _SINGLE_SEPARATOR_PATTERN),
        connector_class=_TERM_CONNECTOR_CLASS,
        free_right_continuation=True,
    )


# Ayrac toleransindan ONCEKI desen.
def legacy_corporate_term_pattern(term: str) -> str:
    return compound_aware_boundary_pattern(
        diacritic_tolerant_escape(term),
        connector_class=_TERM_CONNECTOR_CLASS,
        free_right_continuation=True,
    )


# Tek bir terim icin tum FilterRule alanlarini hesaplar (regex_pattern
# sifreli, is_active durumu suspicious ise False) - _RuleFields dokstringindeki
# ortak hesaplama noktasi budur.
def _compute_rule_fields(*, term: str, category: str, status: str, priority: int) -> _RuleFields:
    pattern_text = corporate_term_pattern(term)
    classification = classify_term(term)
    inactive_note = (
        f" Pasiflik nedeni: {classification.reason}"
        if status == "suspicious" and classification.reason
        else ""
    )
    return _RuleFields(
        rule_name=rule_name_for_term(category, term),
        category=category,
        source_layer="katman1",
        pattern_type="regex",
        regex_pattern=encrypt_value(pattern_text),
        regex_flags="i",
        is_pattern_encrypted=True,
        corporate_term_encrypted=encrypt_value(term),
        placeholder_prefix=placeholder_prefix_for_category(category),
        priority=priority,
        is_active=(status != "suspicious"),
        description=f"Kurumsal terim sozlugu ile eklendi (kategori: {category}).{inactive_note}",
    )


# Python attribute adindan (ORM) fiziksel DB kolon adina eslesme - toplu
# (Core-level) INSERT icin gerekli, cunku sqlite_insert(Model).values([...])
# ORM oznitelik adlarini degil DB kolon adlarini bekler (bkz.
# mapping_service._next_counter'daki ayni desen: on_ek=..., sayac=...).
_RULE_FIELD_TO_COLUMN = {
    "rule_name": "kural_adi",
    "category": "kategori",
    "source_layer": "kaynak_katman",
    "pattern_type": "desen_tipi",
    "regex_pattern": "regex_deseni",
    "regex_flags": "regex_bayraklari",
    "is_pattern_encrypted": "desen_sifreli_mi",
    "corporate_term_encrypted": "kurumsal_terim_sifreli",
    "placeholder_prefix": "yer_tutucu_on_eki",
    "priority": "oncelik",
    "is_active": "aktif_mi",
    "description": "aciklama",
}


# _RuleFields'i, Core-level toplu INSERT icin gerekli DB kolon adi -> deger
# sozlugune cevirir (bkz. _RULE_FIELD_TO_COLUMN esleme tablosu).
def _rule_fields_to_insert_values(fields: _RuleFields) -> dict:
    return {column: getattr(fields, attr) for attr, column in _RULE_FIELD_TO_COLUMN.items()}


# Tek bir terimden, henuz DB'ye eklenmemis bir FilterRule nesnesi uretir.
def build_filter_rule(*, term: str, category: str, status: str, priority: int) -> FilterRule:
    f = _compute_rule_fields(term=term, category=category, status=status, priority=priority)
    return FilterRule(
        rule_name=f.rule_name,
        category=f.category,
        source_layer=f.source_layer,
        pattern_type=f.pattern_type,
        regex_pattern=f.regex_pattern,
        regex_flags=f.regex_flags,
        is_pattern_encrypted=f.is_pattern_encrypted,
        corporate_term_encrypted=f.corporate_term_encrypted,
        placeholder_prefix=f.placeholder_prefix,
        priority=f.priority,
        is_active=f.is_active,
        description=f.description,
    )


# Ayni terimin farkli yazimlarini (orn. "Atlas"/"ATLAS") tek terime indirger; ilk gorulen yazim korunur.
def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(term)
    return result


# Ortak DB'de silinmemis tum kurumsal terimleri BASLIKTAN BAGIMSIZ, _dedupe_terms
# ile ayni anahtarla (casefold) dondurur. Ayni ifade baska bir baslik altinda
# zaten kayitliysa yeniden eklenmez: kural adi basligi icerdiginden kural adi
# kontrolu tek basina yetmez. Terimler Fernet ile (deterministik olmayan)
# sifreli tutuldugu icin karsilastirma cozulmus deger uzerinden yapilir.
def _registered_terms(db: Session) -> dict[str, FilterRule]:
    rows = db.scalars(
        select(FilterRule)
        .where(
            FilterRule.rule_name.like(f"{_RULE_NAME_PREFIX}%"),
            FilterRule.corporate_term_deleted_at.is_(None),
            FilterRule.corporate_term_encrypted.is_not(None),
        )
        .order_by(FilterRule.id)
    ).all()
    registered: dict[str, FilterRule] = {}
    for row in rows:
        registered.setdefault(decrypt_value(row.corporate_term_encrypted).casefold(), row)
    return registered


# Onizlemede/rapor da tek bir terimi, varsa gerekcesiyle (suspicious/rejected
# nedeni) birlikte tasiyan kucuk yardimci nesne.
@dataclass(frozen=True)
class TermPreviewItem:
    term: str
    reason: str | None = None


# Bir toplu yuklemenin onizleme sonucu - HICBIR satir yazilmadan/flush
# edilmeden hesaplanir (bkz. preview_term_upload). new_valid/new_suspicious
# onaylanirsa gercekten eklenecek terimlerdir; already_registered atlanacak,
# rejected hicbir zaman eklenmeyecektir.
@dataclass(frozen=True)
class TermUploadPreview:
    filename: str
    category: str
    total_found: int
    new_valid: list[str] = field(default_factory=list)
    new_suspicious: list[TermPreviewItem] = field(default_factory=list)
    already_registered: list[str] = field(default_factory=list)
    rejected: list[TermPreviewItem] = field(default_factory=list)

    # Onaylanirsa gercekten eklenecek toplam terim sayisi (valid + suspicious).
    @property
    def new_count(self) -> int:
        return len(self.new_valid) + len(self.new_suspicious)

    # Zaten kayitli oldugu icin atlanacak terim sayisi.
    @property
    def existing_count(self) -> int:
        return len(self.already_registered)

    # Pasif olarak eklenecek supheli terim sayisi.
    @property
    def suspicious_count(self) -> int:
        return len(self.new_suspicious)

    # Hic eklenmeyecek reddedilen terim sayisi.
    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


# Bu modulun onizleme giris noktasi: dosyayi ayristirir (Adim 2),
# terimleri siniflandirir (Adim 3), sonra TEK bir toplu SELECT ile
# hangilerinin zaten kayitli oldugunu hesaplar. HICBIR satir DB'ye
# yazilmaz/flush edilmez - salt okunur.
def preview_term_upload(
    db: Session, *, filename: str, content: bytes, category: str
) -> TermUploadPreview:
    normalized_category = validate_category_choice(db, category)
    terms = _dedupe_terms(parse_terms_from_file(filename, content))

    rejected: list[TermPreviewItem] = []
    checkable: list[tuple[str, str, str | None]] = []  # (term, status, reason)
    for term in terms:
        classification = classify_term(term)
        if classification.status == "rejected":
            rejected.append(TermPreviewItem(term=classification.term, reason=classification.reason))
            continue
        checkable.append((classification.term, classification.status, classification.reason))

    rule_name_by_term = {
        term: rule_name_for_term(normalized_category, term) for term, _status, _reason in checkable
    }
    existing_rule_names: set[str] = set()
    if rule_name_by_term:
        existing_rule_names = set(
            db.scalars(
                select(FilterRule.rule_name).where(
                    FilterRule.rule_name.in_(rule_name_by_term.values()),
                    FilterRule.corporate_term_deleted_at.is_(None),
                )
            ).all()
        )

    registered = _registered_terms(db) if checkable else {}

    new_valid: list[str] = []
    new_suspicious: list[TermPreviewItem] = []
    already_registered: list[str] = []
    for term, status, reason in checkable:
        if rule_name_by_term[term] in existing_rule_names or term.casefold() in registered:
            already_registered.append(term)
        elif status == "suspicious":
            new_suspicious.append(TermPreviewItem(term=term, reason=reason))
        else:
            new_valid.append(term)

    return TermUploadPreview(
        filename=filename,
        category=normalized_category,
        total_found=len(terms),
        new_valid=new_valid,
        new_suspicious=new_suspicious,
        already_registered=already_registered,
        rejected=rejected,
    )


# Bir toplu yuklemenin GERCEKLESEN sonucu - onizleme (TermUploadPreview)
# DEGIL, fiilen commit edilenin sayilaridir (bkz. commit_term_upload
# dokstring'i - onizleme ile commit arasinda baska biri ayni terimi
# eklemis olabilir).
@dataclass(frozen=True)
class TermUploadResult:
    filename: str
    category: str
    added_count: int
    skipped_count: int
    rejected_count: int
    suspicious_added_count: int


# Yazma giris noktasi: dosyayi tekrar ayristirir/siniflandirir, sonra tek bir
# atomik toplu INSERT ile yazar (cakisan terimler sessizce atlanir). Kendisi
# commit cagirmaz - hata olursa rollback eder, cagiran taraf commit eder.
def commit_term_upload(db: Session, *, filename: str, content: bytes, category: str) -> TermUploadResult:
    try:
        normalized_category = validate_category_choice(db, category)
        terms = _dedupe_terms(parse_terms_from_file(filename, content))

        rejected_count = 0
        candidates: list[tuple[str, str]] = []  # (term, status)
        for term in terms:
            classification = classify_term(term)
            if classification.status == "rejected":
                rejected_count += 1
                continue
            candidates.append((classification.term, classification.status))

        added_count = 0
        suspicious_added_count = 0
        skipped_count = 0

        if candidates:
            registered = _registered_terms(db)
            skipped_count += sum(1 for term, _status in candidates if term.casefold() in registered)
            candidates = [(term, status) for term, status in candidates if term.casefold() not in registered]

        if candidates:
            base_priority = (db.scalar(select(func.max(FilterRule.priority))) or 0) + 10
            fields_by_rule_name = {
                rule_name_for_term(normalized_category, term): _compute_rule_fields(
                    term=term,
                    category=normalized_category,
                    status=status,
                    priority=base_priority,
                )
                for term, status in candidates
            }
            existing_rows = {
                row.rule_name: row
                for row in db.scalars(
                    select(FilterRule).where(FilterRule.rule_name.in_(fields_by_rule_name))
                ).all()
            }
            insert_rows: list[dict] = []
            for rule_name, fields in fields_by_rule_name.items():
                existing = existing_rows.get(rule_name)
                if existing is None:
                    insert_rows.append(_rule_fields_to_insert_values(fields))
                    continue
                if existing.corporate_term_deleted_at is None:
                    skipped_count += 1
                    continue

                # Ayni terim daha once UI'dan silinmisse tekrar yukleme onu
                # yeni bir FK kimligi uretmeden guvenli bicimde canlandirir.
                existing.category = fields.category
                existing.source_layer = fields.source_layer
                existing.pattern_type = fields.pattern_type
                existing.regex_pattern = fields.regex_pattern
                existing.regex_flags = fields.regex_flags
                existing.is_pattern_encrypted = fields.is_pattern_encrypted
                existing.corporate_term_encrypted = fields.corporate_term_encrypted
                existing.placeholder_prefix = fields.placeholder_prefix
                existing.priority = fields.priority
                existing.is_active = fields.is_active
                existing.description = fields.description
                existing.corporate_term_deleted_at = None
                added_count += 1
                suspicious_added_count += int(not fields.is_active)

            table = FilterRule.__table__
            if insert_rows:
                stmt = (
                    sqlite_insert(table)
                    .values(insert_rows)
                    .on_conflict_do_nothing(index_elements=[table.c.kural_adi])
                    .returning(table.c.kural_adi, table.c.aktif_mi)
                )
                inserted_rows = db.execute(stmt).all()
                added_count += len(inserted_rows)
                suspicious_added_count += sum(
                    1 for _rule_name, is_active in inserted_rows if not is_active
                )
                skipped_count += len(insert_rows) - len(inserted_rows)

        db.flush()
    except Exception:
        db.rollback()
        raise

    return TermUploadResult(
        filename=filename,
        category=normalized_category,
        added_count=added_count,
        skipped_count=skipped_count,
        rejected_count=rejected_count,
        suspicious_added_count=suspicious_added_count,
    )


@dataclass(frozen=True)
class CorporateTermRecord:
    id: int
    rule_name: str
    term: str
    category: str
    placeholder_prefix: str
    is_active: bool
    inactive_reason: str | None
    mapping_count: int
    deleted_at: object | None = None


def _corporate_term_record(rule: FilterRule, mapping_count: int) -> CorporateTermRecord:
    if not rule.corporate_term_encrypted:
        raise TermUploadValidationError(
            f"'{rule.rule_name}' kurumsal terim kaydinin sifreli literal degeri bulunamadi"
        )
    term = decrypt_value(rule.corporate_term_encrypted)
    inactive_reason: str | None = None
    if not rule.is_active:
        if rule.corporate_term_deleted_at is not None:
            inactive_reason = "Bu ifade silindiği için yeni taramalarda kullanılmıyor."
        else:
            classification = classify_term(term)
            inactive_reason = (
                classification.reason
                if classification.status == "suspicious" and classification.reason
                else "Bu ifade kullanıcı veya yönetici tarafından pasif duruma getirildi."
            )
    return CorporateTermRecord(
        id=rule.id,
        rule_name=rule.rule_name,
        term=term,
        category=rule.category,
        placeholder_prefix=placeholder_prefix_for_category(rule.category),
        is_active=rule.is_active,
        inactive_reason=inactive_reason,
        mapping_count=mapping_count,
        deleted_at=rule.corporate_term_deleted_at,
    )


def list_corporate_terms(db: Session, *, include_deleted: bool = False) -> list[CorporateTermRecord]:
    """List only user-uploaded corporate expressions, never system rules."""
    stmt = (
        select(FilterRule, func.count(ValueMapping.id))
        .outerjoin(ValueMapping, ValueMapping.rule_id == FilterRule.id)
        .where(FilterRule.rule_name.like(f"{_RULE_NAME_PREFIX}%"))
        .group_by(FilterRule.id)
        .order_by(FilterRule.category, FilterRule.id)
    )
    if not include_deleted:
        stmt = stmt.where(FilterRule.corporate_term_deleted_at.is_(None))
    return [_corporate_term_record(rule, mapping_count) for rule, mapping_count in db.execute(stmt).all()]


def delete_corporate_term(db: Session, term_id: int) -> CorporateTermRecord:
    """Soft-delete one term while keeping historical mappings restorable."""
    row = db.scalar(
        select(FilterRule).where(
            FilterRule.id == term_id,
            FilterRule.rule_name.like(f"{_RULE_NAME_PREFIX}%"),
        )
    )
    if row is None:
        raise TermUploadValidationError("Kurumsal terim bulunamadi")
    if row.corporate_term_deleted_at is not None:
        raise TermUploadValidationError("Kurumsal terim daha once silinmis")

    mapping_count = db.scalar(
        select(func.count(ValueMapping.id)).where(ValueMapping.rule_id == row.id)
    ) or 0
    row.is_active = False
    row.corporate_term_deleted_at = datetime.now(timezone.utc)
    db.flush()
    return _corporate_term_record(row, mapping_count)


def activate_corporate_term(
    db: Session,
    term_id: int,
    *,
    confirmed_sensitive: bool,
) -> CorporateTermRecord:
    """Activate one reviewed corporate term after explicit user consent."""
    if not confirmed_sensitive:
        raise TermUploadValidationError(
            "Pasif terimi aktif etmek için ifadenin kurumsal ve hassas olduğunu onaylamalısınız"
        )

    row = db.scalar(
        select(FilterRule).where(
            FilterRule.id == term_id,
            FilterRule.rule_name.like(f"{_RULE_NAME_PREFIX}%"),
        )
    )
    if row is None:
        raise TermUploadValidationError("Kurumsal terim bulunamadı")
    if row.corporate_term_deleted_at is not None:
        raise TermUploadValidationError(
            "Silinmiş kurumsal ifade doğrudan aktif edilemez; önce yeniden yüklenmelidir"
        )

    row.is_active = True
    db.flush()
    mapping_count = db.scalar(
        select(func.count(ValueMapping.id)).where(ValueMapping.rule_id == row.id)
    ) or 0
    return _corporate_term_record(row, mapping_count)


def add_single_corporate_term(
    db: Session,
    *,
    term: str,
    title: str,
    confirmed_sensitive: bool,
) -> CorporateTermRecord:
    """Add one explicitly confirmed corporate expression without a file."""
    clean_term = term.strip()
    if not clean_term:
        raise TermUploadValidationError("Kurumsal ifade bos birakilamaz")
    if "\n" in clean_term or "\r" in clean_term:
        raise TermUploadValidationError("Tekil ekleme alanina yalnizca bir kurumsal ifade girilebilir")
    if not confirmed_sensitive:
        raise TermUploadValidationError("Kurumsal ifadenin hassas oldugunu onaylamalisiniz")

    normalized_category = validate_category_choice(db, title)
    classification = classify_term(clean_term)
    if classification.status == "rejected":
        raise TermUploadValidationError(
            f"Kurumsal ifade eklenemedi: {classification.reason or 'gecersiz ifade'}"
        )

    row = _registered_terms(db).get(classification.term.casefold())
    if row is not None and row.is_active:
        raise TermUploadValidationError(
            f"'{clean_term}' ifadesi '{row.category}' başlığı altında zaten kayıtlı; tekrar eklenmedi"
        )
    if row is None:
        commit_term_upload(
            db,
            filename="tek-kurumsal-ifade.txt",
            content=f"{clean_term}\n".encode("utf-8"),
            category=normalized_category,
        )
        rule_name = rule_name_for_term(normalized_category, clean_term)
        row = db.scalar(select(FilterRule).where(FilterRule.rule_name == rule_name))
        if row is None:
            raise TermUploadValidationError("Kurumsal ifade veritabanina eklenemedi")
    # Pasif kayitli ayni ifade yeni satir acilmadan, kendi basligiyla aktif edilir.

    # Tekil formdaki acik hassaslik onayi, toplu yuklemedeki "suspicious"
    # sinifinin gerektirdigi insan onayinin kendisidir. Reddedilen ifadeler
    # yukarida yine engellenir; onayli ama kisa/genel ifade aktif eklenir.
    row.is_active = True
    row.corporate_term_deleted_at = None
    mapping_count = db.scalar(
        select(func.count(ValueMapping.id)).where(ValueMapping.rule_id == row.id)
    ) or 0
    db.flush()
    return _corporate_term_record(row, mapping_count)


# --------------------------------------------------------------------------
# Maskeleme-SONRASI son kontrol (Adim 8): export pipeline'inin ana tespit
# gecisinden (RuleBasedDetector/OverlapResolver/TokenBoundaryValidator)
# TAMAMEN BAGIMSIZ, ikinci bir dogrulama. O gecis bir terimi farkli bir
# NEDENLE (orn. baska bir detector'la cakisip OverlapResolver'da kaybetti,
# ya da TokenBoundaryValidator bare-kod/dengesiz-parantez kontrolunden
# REDDETTI) maskelemeden birakmis olabilir - round-trip dogrulamasi byle
# bir durumu YAKALAMAZ (cunku "maskelenmemis" bir deger zaten orijinaliyle
# ayni kalir, round-trip'i BOZMAZ). Bu yuzden exporter.py, maskelenmis
# metni burada AYRICA, aktif terim kurallarinin regex'ine karsi yeniden
# tarar - audit_reviewer.py'nin LLM-tabanli ikincil denetimiyle AYNI
# felsefe (maskeleme SONRASI, "hala bir ipucu kaldi mi" sorusu), ama
# deterministik ve terim sozlugune ozel.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakedTerm:
    rule_name: str
    category: str
    line_number: int
    column_number: int
    matched_value: str
    # Otomatik duzeltmede ayri maskelenecek parcalar; bos ise matched_value
    # tek deger olarak maskelenir (bkz. term_search.structural_parts).
    mask_parts: tuple[str, ...] = ()


# Supheli diye PASIF eklenen terimler hicbir taramada maskelenmez (bkz.
# term_classifier). Kullanici terimi ekledigi icin korundugunu sanabilir;
# disa aktarma bu desenlerle yalnizca rapor notu uretir (bkz. exporter
# _measure_passive_terms), dosya engellenmez.
def compile_passive_term_patterns(db: Session) -> list[re.Pattern]:
    rows = db.execute(
        select(FilterRule.rule_name, FilterRule.regex_pattern, FilterRule.regex_flags, FilterRule.is_pattern_encrypted)
        .where(
            or_(*(FilterRule.rule_name.startswith(prefix, autoescape=True) for prefix in CORPORATE_RULE_PREFIXES)),
            FilterRule.is_active.is_(False),
            FilterRule.corporate_term_deleted_at.is_(None),
            FilterRule.pattern_type == "regex",
        )
    ).all()
    return [
        re.compile(decrypt_value(pattern) if is_encrypted else pattern, _compile_flags(flags))
        for rule_name, pattern, flags, is_encrypted in rows
        if is_corporate_rule(rule_name)
    ]


def count_passive_terms(patterns: list[re.Pattern], text: str) -> int:
    protected = [match.span() for match in PLACEHOLDER_RE.finditer(text)]
    return sum(
        1 for pattern in patterns for match in pattern.finditer(text)
        if not any(left <= match.start() and match.end() <= right for left, right in protected)
    )


# Ayractan bagimsiz kaba son kontrolun calistigi en az harf sayisi. Ayraclar
# atilinca kisa terimler kelime aralarinda rastgele belirir ("TSK" -> "text
# skipped", "DDO" -> ".md dosyasi"). Olcum (2026-10-16, 519 aktif terim):
# sinirsiz kontrol ilgisiz 6.376 dosyanin 398'ini onaya dusururdu; 6+ harfte
# 14'e, sol sinir duzeltmesiyle birlikte 4'e iniyor.
_SEPARATED_CHECK_MIN_LETTERS = 6


def allows_separated_check(term: str) -> bool:
    return sum(ch.isalpha() for ch in term) >= _SEPARATED_CHECK_MIN_LETTERS


def _leaked_term(rule_name: str, category: str, text: str, start: int, end: int) -> LeakedTerm:
    return LeakedTerm(
        rule_name=rule_name, category=category,
        line_number=text.count("\n", 0, start) + 1,
        column_number=start - text.rfind("\n", 0, start),
        matched_value=text[start:end],
    )


# Maskeleme deseninin goremedigi yazimlar: "Zeta.Ledger", "Ahmet,Yilmaz",
# satira bolunmus, harfleri ayrilmis ya da kacis koduyla yazilmis terim.
# Maskelenmis yer tutucular engeldir: iki yanindaki harfler birlestirilmez.
def _separated_term_leaks(
    masked_text: str,
    terms: list[tuple[str, str, str]],
    protected_spans: list[tuple[int, int]],
    reported_spans: list[tuple[int, int]],
) -> list[LeakedTerm]:
    needles = [(rule_name, category, term, compact_text(term)) for rule_name, category, term in terms
               if allows_separated_check(term)]
    # Kaynak eslemeli gorunum karakter karakter kurulur; cogu dosyada hicbir
    # terim gecmedigi icin once eslemesiz (C hizinda) on eleme yapilir.
    quick = compact_text(masked_text)
    needles = [item for item in needles if item[3] in quick]
    if not needles:
        return []
    view = compact_view(masked_text, protected_spans)
    leaks: list[LeakedTerm] = []
    for rule_name, category, term, needle in needles:
        for start, end in find_compact(view, needle):
            if any(start < right and left < end for left, right in reported_spans):
                continue
            leak = _leaked_term(rule_name, category, masked_text, start, end)
            parts = structural_parts(leak.matched_value, term)
            leaks.append(replace(leak, mask_parts=tuple(parts)) if len(parts) > 1 else leak)
    return leaks


# Maskelenmis metni tum aktif kurumsal-terim kurallarina karsi yeniden tarar -
# hala eslesen bir terim varsa ana tespit gecisi onu kacirmis demektir.
#
# exclude_path_spanning=True: yol kontrolu, mask_relative_path() gibi her
# bileseni ayri tarar. Boylece regex sinirlari da aynidir; birden fazla
# klasore yayilan terimler veya eksik legacy terim metadata'si sahte
# alarm uretmez. Dosya ICERIGINDE tam yollar hala denetlenir.
#
# include_separated=True: maskeleme deseninden BAGIMSIZ ikinci bir arama
# (bkz. _separated_term_leaks). Ayni desenle yapilan kontrol, maskelemenin
# kacirdigini da kacirir. Yalnizca dosya icerigi icin: bulunan deger once
# otomatik duzeltmeye gider; yollarda ise tek bulgu tum disa aktarmayi
# durdurur ve insan onay kapisi bir yanlis alarmi gecemezdi.
def find_leaked_terms(
    db: Session,
    masked_text: str,
    *,
    exclude_path_spanning: bool = False,
    path_placeholders: Iterable[str] | None = None,
    include_separated: bool = False,
) -> list[LeakedTerm]:
    """Check for open terms; paths may supply tokens used in that path.

    Content retains its strict placeholder boundaries. For paths, trust
    only exact supplied tokens and use the same matching as path restore:
    e.g. mask_kurumsal_ifade_1ServiceImpl.java contains a valid token even
    without a word boundary before ServiceImpl. Its suffix stays checked.
    """
    columns = [
        FilterRule.rule_name, FilterRule.category, FilterRule.regex_pattern,
        FilterRule.regex_flags, FilterRule.is_pattern_encrypted, FilterRule.corporate_term_encrypted,
    ]
    rows = db.execute(
        select(*columns).where(
            or_(*(FilterRule.rule_name.startswith(prefix, autoescape=True) for prefix in CORPORATE_RULE_PREFIXES)),
            FilterRule.is_active.is_(True),
            FilterRule.corporate_term_deleted_at.is_(None),
            FilterRule.pattern_type == "regex",
        )
    ).all()

    if path_placeholders is None:
        protected_spans = [match.span() for match in PLACEHOLDER_RE.finditer(masked_text)]
    else:
        resolver = PathPlaceholderResolver(dict.fromkeys(path_placeholders, ""))
        protected_spans = resolver.known_placeholder_spans(masked_text)
    regions = (
        [(part.start(), part.group()) for part in re.finditer(r"[^/\\]+", masked_text)]
        if exclude_path_spanning else [(0, masked_text)]
    )
    leaked: list[LeakedTerm] = []
    reported_spans: list[tuple[int, int]] = []
    separated_terms: list[tuple[str, str, str]] = []
    for rule_name, category, stored_pattern, regex_flags, is_encrypted, encrypted_term in rows:
        # SQLite LIKE is case-insensitive; apply the same exact prefix
        # policy as masking even when the DB query returns a lookalike.
        if not is_corporate_rule(rule_name):
            continue
        pattern = decrypt_value(stored_pattern) if is_encrypted else stored_pattern
        compiled = re.compile(pattern, _compile_flags(regex_flags))
        for offset, region in regions:
            for match in compiled.finditer(region):
                start, end = offset + match.start(), offset + match.end()
                # Ignore only matches wholly inside a recognized token.
                # Its surrounding prefix/suffix must still be checked.
                if any(left <= start and end <= right for left, right in protected_spans):
                    continue
                # Maskelemeyle ayni kural: camelCase dilimi yapisik yazim sayilmaz.
                if is_mixed_case_glued(masked_text, start, end):
                    continue
                leaked.append(_leaked_term(rule_name, category, masked_text, start, end))
                reported_spans.append((start, end))
        if include_separated and not exclude_path_spanning and encrypted_term:
            separated_terms.append((rule_name, category, decrypt_value(encrypted_term)))
    if separated_terms:
        leaked.extend(_separated_term_leaks(masked_text, separated_terms, protected_spans, reported_spans))
    return leaked
