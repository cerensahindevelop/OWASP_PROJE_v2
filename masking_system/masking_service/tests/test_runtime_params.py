"""Calisma zamani parametreleri (proje/sicil/branch) icin tek kaynak.

Test DB'si `alembic upgrade head` ile kurulur (scripts/run_tests_isolated.py);
bu testler taze kurulumda sicil'in icerikte ve yolda maskelendigini ve
parametrik kural kategorilerinin runtime_params.RuntimeParam ile uyumlu
oldugunu dogrular (personnel_no/sicil_no sizintisinin regresyon testi).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import FilterRule
from app.services import exporter
from app.services.audit_reviewer import AuditVerdict
from app.services.detectors import DetectorOutput
from app.services.exporter import export_project
from app.services.rule_admin import RuleValidationError, add_rule
from app.services.runtime_params import RuntimeParam, build_runtime_params


def test_alembic_parametric_rules_match_runtime_params(db_session):
    rules = db_session.scalars(select(FilterRule).where(FilterRule.pattern_type == "parametric")).all()
    assert {rule.category for rule in rules} <= set(RuntimeParam)
    for param in RuntimeParam:
        assert any(rule.category == param and rule.is_active for rule in rules), param


def test_build_runtime_params_uses_rule_categories():
    params = build_runtime_params("p", "s", "b")
    assert params == {"project_name": "p", "sicil_no": "s", "branch_name": "b"}
    assert set(params) == set(RuntimeParam)


async def _clean_audit(*args, **kwargs):
    return AuditVerdict(risky=False)


@pytest.mark.parametrize("sicil", ["A123456", "7654321"])
def test_fresh_install_masks_sicil_in_content_and_path(db_session, tmp_path, monkeypatch, sicil):
    monkeypatch.setattr(exporter, "audit_masked_text", _clean_audit)
    source = tmp_path / "kaynak"
    (source / "ekip" / sicil).mkdir(parents=True)
    # "sicil no:" gibi bir etiket bilerek yok: baglamsal personel kurali degil,
    # parametrik sicil kurali sinanir.
    (source / "ekip" / sicil / "notlar.md").write_text(f"Kaydi acan: {sicil}\nDurum: taslak\n",
                                                       encoding="utf-8")
    target = tmp_path / "cikti"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="pytest-sicil-regresyon", sicil_no=sicil, branch_name="main", initiated_by=sicil,
    ))

    outputs = [p for p in target.rglob("*") if p.is_file() and p.name != ".masking-integrity.json"]
    assert len(outputs) == 1 and report.outcomes[0].final_state == "READY"
    rel = outputs[0].relative_to(target).as_posix()
    assert sicil not in rel and "mask_personel_no_" in rel
    text = outputs[0].read_text(encoding="utf-8")
    assert sicil not in text and "mask_personel_no_" in text and "Durum: taslak" in text


def test_parametric_rule_with_unknown_category_is_rejected(db_session):
    with pytest.raises(RuleValidationError, match="calisma zamani parametresi"):
        add_rule(db_session, rule_name="personnel_no_kopya", placeholder_format="mask_personel_no_kopya",
                 pattern_type="parametric", category="personnel_no")
