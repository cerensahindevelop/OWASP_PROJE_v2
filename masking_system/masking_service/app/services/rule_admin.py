"""Admin operations on filter_rules: add a new rule, enable/disable an
existing one. This is the concrete implementation of the "kural = veri"
principle - every function here only ever inserts/updates a row in
filter_rules. No caller of these functions needs a code change or a
deploy to introduce a brand-new detection pattern.

Disabling a rule (kural-pasif) never deletes it and never touches
value_mappings: rule_id foreign keys on existing mapping rows stay valid,
so history and reversibility for anything already masked under that rule
are unaffected. Only NEW matching stops (mapping_service.load_active_rules
filters on is_active).
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# FilterRule: filter_rules tablosunun ORM modeli - bu modul sadece bu
# tabloya satir ekler/gunceller. _compile_flags: regex flag harflerini
# derlemede kullanmak icin rule_engine ile PAYLASILIR (regex on-dogrulamasi
# icin). VALIDATORS: validator_name girisinin bilinen bir dogrulayiciya
# karsilik geldigini kontrol etmek icin.
from app.db.models import FilterRule
from app.services.runtime_params import RuntimeParam
from app.services.rule_engine import _compile_flags
from app.services.validators import VALIDATORS

PLACEHOLDER_TOKEN = "{sayac}"
# "mask_" onekini sart kosar: sistem genelindeki tum placeholder'lari
# (otomatik uretilenler dahil) ayni benzersiz isaretciyle tanimlar - bkz.
# rule_engine.PLACEHOLDER_RE.
_VALID_PREFIX_RE = re.compile(r"^mask_[a-z][a-z0-9_]*$")


# Kural ekleme/guncelleme sirasinda gecersiz girdi verildiginde firlatilan hata.
class RuleValidationError(ValueError):
    pass


# 'mask_<prefix>_{sayac}' bicimindeki placeholder-format string'ini dogrulayip bare prefix'i cikartir.
def parse_placeholder_format(placeholder_format: str) -> str:
    suffix = f"_{PLACEHOLDER_TOKEN}"
    if not placeholder_format.endswith(suffix):
        raise RuleValidationError(
            f"placeholder-format '{suffix}' ile bitmelidir (ornek: 'mask_tc_kimlik_{PLACEHOLDER_TOKEN}'), "
            "boylece sistem genelindeki mask_<prefix>_<sayac> placeholder formatiyla tutarli kalir."
        )
    prefix = placeholder_format[: -len(suffix)]
    if not prefix or not _VALID_PREFIX_RE.match(prefix):
        raise RuleValidationError(
            f"placeholder-format 'mask_' ile baslamali, ardindan kucuk harf/rakam/alt cizgi "
            f"icermelidir: '{prefix}'"
        )
    return prefix


# Bir regex pattern'inin gecerli/derlenebilir olup olmadigini kural
# eklenmeden ONCE kontrol eder - bozuk bir regex tum sistemi etkilemesin diye.
def _validate_regex(pattern: str, flags: str | None) -> None:
    try:
        re.compile(pattern, _compile_flags(flags))
    except re.error as exc:
        raise RuleValidationError(f"gecersiz regex pattern: {exc}") from exc


# Yeni bir filtre kurali ekler ("kural-ekle" komutunun servis katmani).
# Sadece filter_rules tablosuna satir ekler - kod degisikligi/deploy gerektirmez.
def add_rule(
    db: Session,
    *,
    rule_name: str,
    placeholder_format: str,
    pattern_type: str = "regex",
    regex_pattern: str | None = None,
    regex_flags: str | None = None,
    validator_name: str | None = None,
    category: str | None = None,
    priority: int | None = None,
    description: str | None = None,
    is_active: bool = True,
    source_layer: str = "katman1",
    entity_type: str | None = None,
    confidence_score: float = 0.85,
    is_allow_list: bool = False,
) -> FilterRule:
    if not rule_name or not rule_name.strip():
        raise RuleValidationError("rule_name (--tip) bos olamaz")

    if source_layer not in ("katman1", "katman2_presidio", "llm"):
        raise RuleValidationError(
            f"source_layer 'katman1', 'katman2_presidio' ya da 'llm' olmalidir, verilen: '{source_layer}'"
        )

    if pattern_type not in ("regex", "parametric", "llm", "presidio"):
        raise RuleValidationError(
            f"pattern_type 'regex', 'parametric', 'llm' ya da 'presidio' olmalidir, verilen: '{pattern_type}'"
        )

    if pattern_type == "regex":
        if not regex_pattern:
            raise RuleValidationError("pattern_type='regex' icin --pattern zorunludur")
        _validate_regex(regex_pattern, regex_flags)
        if validator_name is not None and validator_name not in VALIDATORS:
            bilinen = ", ".join(sorted(VALIDATORS)) or "(kayitli dogrulayici yok)"
            raise RuleValidationError(
                f"bilinmeyen validator_name '{validator_name}'. Bilinen dogrulayicilar: {bilinen}"
            )
    elif pattern_type == "parametric":
        if regex_pattern:
            raise RuleValidationError("pattern_type='parametric' kurallarda --pattern verilmemelidir")
        if validator_name:
            raise RuleValidationError("pattern_type='parametric' kurallarda --dogrulayici verilmemelidir")
        # Parametrik kural yalnizca kategorisi bir runtime parametresiyse
        # eslesir; aksi halde hicbir zaman calismayan sessiz bir kural olur
        # (personnel_no/sicil_no uyusmazligi, bkz. app/services/runtime_params.py).
        if (category or rule_name) not in set(RuntimeParam):
            bilinen = ", ".join(param.value for param in RuntimeParam)
            raise RuleValidationError(
                f"pattern_type='parametric' kurallarin kategorisi bir calisma zamani parametresi "
                f"olmalidir ({bilinen}); verilen: '{category or rule_name}'"
            )
    elif pattern_type == "llm":
        if regex_pattern:
            raise RuleValidationError("pattern_type='llm' kurallarda --pattern verilmemelidir")
        if validator_name:
            raise RuleValidationError("pattern_type='llm' kurallarda --dogrulayici verilmemelidir")
        if not description or not description.strip():
            raise RuleValidationError(
                "pattern_type='llm' icin --aciklama zorunludur (LLM'e verilecek tarama talimati)"
            )
    else:  # 'presidio'
        if source_layer != "katman2_presidio":
            raise RuleValidationError("pattern_type='presidio' icin source_layer='katman2_presidio' olmalidir")
        if not regex_pattern:
            raise RuleValidationError("pattern_type='presidio' icin --pattern zorunludur")
        if not entity_type:
            raise RuleValidationError("pattern_type='presidio' icin entity_type zorunludur")
        _validate_regex(regex_pattern, regex_flags)
        if not 0 <= confidence_score <= 1:
            raise RuleValidationError("confidence_score 0 ile 1 arasinda olmalidir")

    placeholder_prefix = parse_placeholder_format(placeholder_format)

    existing = db.scalars(select(FilterRule).where(FilterRule.rule_name == rule_name)).one_or_none()
    if existing is not None:
        raise RuleValidationError(f"'{rule_name}' adinda bir kural zaten mevcut (id={existing.id})")

    if priority is None:
        max_priority = db.scalar(select(func.max(FilterRule.priority)))
        priority = (max_priority or 0) + 10

    rule = FilterRule(
        rule_name=rule_name,
        category=category or rule_name,
        source_layer=source_layer,
        pattern_type=pattern_type,
        regex_pattern=regex_pattern,
        regex_flags=regex_flags,
        validator_name=validator_name,
        placeholder_prefix=placeholder_prefix,
        entity_type=entity_type,
        confidence_score=confidence_score,
        is_allow_list=is_allow_list,
        priority=priority,
        is_active=is_active,
        description=description,
    )
    db.add(rule)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise RuleValidationError(f"kural eklenemedi (muhtemelen tekrar eden rule_name): {exc}") from exc
    return rule


# Bir kurali aktif/pasif olarak isaretler - SATIRI SILMEZ, gecmis
# eslemeler (value_mappings) bu sayede bozulmadan kalir.
def set_rule_active(db: Session, rule_name: str, is_active: bool) -> FilterRule:
    rule = db.scalars(select(FilterRule).where(FilterRule.rule_name == rule_name)).one_or_none()
    if rule is None:
        raise RuleValidationError(f"'{rule_name}' adinda bir kural bulunamadi")
    if rule.corporate_term_deleted_at is not None:
        raise RuleValidationError(
            "Silinmis kurumsal ifade aktif/pasif yapilamaz; yeniden kullanmak icin terim dosyasini tekrar yukleyin"
        )
    rule.is_active = is_active
    db.flush()
    return rule


# Tum kurallari (istege bagli olarak sadece aktifleri) oncelik sirasiyla listeler.
def list_rules(db: Session, include_inactive: bool = True) -> list[FilterRule]:
    stmt = select(FilterRule).order_by(FilterRule.priority)
    if not include_inactive:
        stmt = stmt.where(FilterRule.is_active.is_(True))
    return list(db.scalars(stmt).all())
