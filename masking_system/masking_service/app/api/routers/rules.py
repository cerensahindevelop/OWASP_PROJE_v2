from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_request_db
from app.api.schemas import RuleActiveIn, RuleCreateIn, RuleOut
from app.services.rule_admin import add_rule, list_rules, set_rule_active

router = APIRouter(prefix="/rules", tags=["rules"])


# Tum kurallari (istege bagli sadece aktifleri) oncelik sirasiyla listeler.
@router.get("", response_model=list[RuleOut])
def get_rules(include_inactive: bool = True, db: Session = Depends(get_request_db)) -> list[RuleOut]:
    rules = list_rules(db, include_inactive=include_inactive)
    return [RuleOut.model_validate(r) for r in rules]


# Yeni bir filtre kurali ekler.
@router.post("", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleCreateIn, db: Session = Depends(get_request_db)) -> RuleOut:
    rule = add_rule(db, **payload.model_dump())
    return RuleOut.model_validate(rule)


# Bir kurali aktif/pasif olarak isaretler.
@router.patch("/{rule_name}/active", response_model=RuleOut)
def patch_rule_active(rule_name: str, payload: RuleActiveIn, db: Session = Depends(get_request_db)) -> RuleOut:
    rule = set_rule_active(db, rule_name, payload.is_active)
    return RuleOut.model_validate(rule)
