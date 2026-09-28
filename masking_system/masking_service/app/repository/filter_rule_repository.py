from __future__ import annotations

from typing import Protocol

# Sorgu insasi (select) ve DB session tipi icin cekirdek SQLAlchemy bilesenleri.
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_value
from app.db.models import FilterRule
from app.services.presidio_detector import PresidioRuleSpec
from app.services.rule_engine import RuleSpec
from app.services.placeholder_policy import public_placeholder_prefix


# FilterRule (filtre_kurallari) tablosuna erisimin soyut sozlesmesi - rule
# engine ve Presidio detector'i somut SQLAlchemy implementasyonuna baglamaz.
class FiltreKuraliRepository(Protocol):
    # Katman 1 (regex/parametric/llm) icin aktif tespit kurallarini dondurur.
    def list_active_detection_rules(self) -> list[RuleSpec]:
        ...

    # Katman 2 Presidio icin aktif pattern kurallarini dondurur.
    def list_active_presidio_rules(self) -> list[PresidioRuleSpec]:
        ...


# FiltreKuraliRepository sozlesmesinin SQLAlchemy implementasyonu.
class SqlAlchemyFiltreKuraliRepository:
    # Cagiran taraftan gelen aktif DB session'ini saklar.
    def __init__(self, db: Session) -> None:
        self.db = db

    # Katman 1 icin aktif (regex/parametric/llm) kurallari oncelik sirasina
    # gore okuyup rule_engine'in kullandigi RuleSpec listesine cevirir;
    # sifreli desenleri _resolve_pattern ile cozer.
    def list_active_detection_rules(self) -> list[RuleSpec]:
        rows = self.db.scalars(
            select(FilterRule)
            .where(
                FilterRule.is_active.is_(True),
                FilterRule.corporate_term_deleted_at.is_(None),
                FilterRule.source_layer.in_(["katman1", "llm"]),
            )
            .order_by(FilterRule.priority)
        ).all()
        return [
            RuleSpec(
                id=r.id,
                rule_name=r.rule_name,
                category=r.category,
                pattern_type=r.pattern_type,
                regex_pattern=self._resolve_pattern(r),
                regex_flags=r.regex_flags,
                placeholder_prefix=public_placeholder_prefix(r.rule_name, r.placeholder_prefix),
                priority=r.priority,
                validator_name=r.validator_name,
                description=r.description,
            )
            for r in rows
        ]

    # Kural sifreliyse (kurumsal terim sozlugu) desenin sifresini cozer, degilse oldugu gibi doner.
    @staticmethod
    def _resolve_pattern(rule: FilterRule) -> str | None:
        if rule.is_pattern_encrypted and rule.regex_pattern is not None:
            return decrypt_value(rule.regex_pattern)
        return rule.regex_pattern

    # Katman 2 icin aktif Presidio pattern kurallarini okuyup PresidioRuleSpec
    # listesine cevirir; regex_deseni bos olan satirlar (gecersiz kural) atlanir.
    def list_active_presidio_rules(self) -> list[PresidioRuleSpec]:
        rows = self.db.scalars(
            select(FilterRule)
            .where(
                FilterRule.is_active.is_(True),
                FilterRule.corporate_term_deleted_at.is_(None),
                FilterRule.source_layer == "katman2_presidio",
                FilterRule.pattern_type == "presidio",
            )
            .order_by(FilterRule.priority)
        ).all()
        return [
            PresidioRuleSpec(
                rule_id=r.id,
                rule_name=r.rule_name,
                regex_pattern=r.regex_pattern or "",
                regex_flags=r.regex_flags,
                entity_type=r.entity_type or r.category,
                confidence_score=r.confidence_score,
                is_allow_list=r.is_allow_list,
                placeholder_prefix=r.placeholder_prefix,
                priority=r.priority,
            )
            for r in rows
            if r.regex_pattern
        ]
