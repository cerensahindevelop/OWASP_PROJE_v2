from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import re

import pytest
from sqlalchemy import select
from sqlalchemy import text as sqltext

from app.core.crypto import decrypt_value
from app.db.models import ValueMapping
from app.db.session import SessionLocal
from app.services.exporter import ExportValidationError, export_project
from app.services.mapping_service import (
    get_or_create_context,
    get_or_create_mapping,
    load_active_rules,
    mask_relative_path,
)
from app.services.unmasker import unmask_project


def _sha256_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _cleanup_identity(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": "P-TEST-0001", "b": "pytest-branch"},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


def test_project_name_references_paths_and_restore_are_consistent(tmp_path):
    project = "pytest_refint_poseidon"
    identity = (project, "P-TEST-0001", "pytest-branch")
    _cleanup_identity(project)

    source = tmp_path / "source"
    package = source / project
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (package / f"{project}_api.py").write_text("API_NAME = 'stable'\n", encoding="utf-8")
    (source / "consumer.py").write_text(
        f"from {project}.{project}_api import API_NAME\n"
        f"PACKAGE_NAME = '{project}'\n",
        encoding="utf-8",
    )

    masked = tmp_path / "masked"
    restored = tmp_path / "restored"

    with SessionLocal() as db:
        # export_project artik async - bkz. app/services/exporter.py modul dokstring'i.
        export_report = asyncio.run(
            export_project(
                db,
                source_path=str(source),
                project_name=identity[0],
                sicil_no=identity[1],
                branch_name=identity[2],
                target_path=str(masked),
                initiated_by=identity[1],
            )
        )
        db.commit()

    assert export_report.status == "completed"
    masked_files = sorted(p.relative_to(masked).as_posix() for p in masked.rglob("*") if p.is_file())
    masked_package_dirs = [p for p in masked.iterdir() if p.is_dir() and p.name.startswith("mask_proje_adi_")]
    assert len(masked_package_dirs) == 1
    masked_package = masked_package_dirs[0].name
    assert masked_files == sorted([
        ".masking-integrity.json",
        f"{masked_package}/__init__.py",
        f"{masked_package}/{project}_api.py",
        "consumer.py",
    ])

    consumer_text = (masked / "consumer.py").read_text(encoding="utf-8")
    assert f"from {project}.{project}_api import API_NAME" not in consumer_text
    assert f"from {masked_package}.{project}_api import API_NAME" in consumer_text
    assert f"PACKAGE_NAME = '{project}'" not in consumer_text
    assert "mask_proje_adi_" in consumer_text

    with SessionLocal() as db:
        unmask_report = unmask_project(
            db,
            source_path=str(masked),
            project_name=identity[0],
            sicil_no=identity[1],
            branch_name=identity[2],
            target_path=str(restored),
            initiated_by=identity[1],
        )
        db.commit()

    assert unmask_report.status == "completed"
    assert _sha256_tree(restored) == _sha256_tree(source)
    for rel in _sha256_tree(source):
        assert (restored / rel).read_bytes() == (source / rel).read_bytes()

    _cleanup_identity(project)


def test_path_masking_collision_fails_before_existing_target_is_touched(tmp_path, monkeypatch):
    project = "pytest_path_collision"
    identity = (project, "P-TEST-0001", "pytest-branch")
    _cleanup_identity(project)

    source = tmp_path / "source"
    (source / "first").mkdir(parents=True)
    (source / "second").mkdir(parents=True)
    (source / "first" / "one.txt").write_text("first\n", encoding="utf-8")
    (source / "second" / "two.txt").write_text("second\n", encoding="utf-8")

    # Collision guard is independent of any concrete word/casing heuristic:
    # simulate any future matcher/configuration mapping two sources to one
    # target and verify fail-closed behavior.
    import app.services.exporter as exporter_module

    monkeypatch.setattr(
        exporter_module,
        "mask_relative_path",
        lambda *args, **kwargs: (Path("same-target.txt"), []),
    )

    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep-me.txt"
    sentinel.write_text("existing output\n", encoding="utf-8")

    with SessionLocal() as db, pytest.raises(ExportValidationError, match="yol maskeleme cakismasi"):
        asyncio.run(
            export_project(
                db,
                source_path=str(source),
                project_name=identity[0],
                sicil_no=identity[1],
                branch_name=identity[2],
                target_path=str(target),
                initiated_by=identity[1],
            )
        )

    assert sentinel.read_text(encoding="utf-8") == "existing output\n"
    _cleanup_identity(project)


def test_path_masking_preserves_common_structural_main_directory():
    project = "pytest_distinctive_path_identity"
    _cleanup_identity(project)

    with SessionLocal() as db:
        context = get_or_create_context(db, project, "P-PATH-0001", "main")
        masked, mappings = mask_relative_path(
            db,
            context,
            Path(f"src/main/{project}/Config.java"),
            {"project_name": project, "sicil_no": "P-PATH-0001", "branch_name": "main"},
            load_active_rules(db),
        )
        db.rollback()

    assert masked.parts[:2] == ("src", "main")
    assert masked.parts[2].startswith("mask_proje_adi_")
    assert len(mappings) == 1
    _cleanup_identity(project)


def test_parametric_case_variants_use_exact_mappings_and_restore_paths_and_content(
    db_session, tmp_path
):
    """Case-insensitive detection must not imply canonicalized restoration.

    This is intentionally institution/word agnostic: one runtime identity is
    represented with three arbitrary casing variants. A pre-existing mapping
    simulates legacy context state and must not poison newly seen spellings.
    """
    runtime_value = "QzCasingIdentityR91"
    lower_value = runtime_value.lower()
    upper_value = runtime_value.upper()
    identity = (runtime_value, "P-CASE-EXACT-91", "case-exact-branch")

    context = get_or_create_context(db_session, *identity)
    project_rule = next(
        rule
        for rule in load_active_rules(db_session)
        if rule.pattern_type == "parametric" and rule.category == "project_name"
    )
    legacy_mapping, _created = get_or_create_mapping(
        db_session,
        context.id,
        project_rule,
        lower_value,
    )

    source = tmp_path / "case-source"
    source_file = source / upper_value / "identity.properties"
    source_file.parent.mkdir(parents=True)
    original_text = (
        f'upper="{upper_value}"\n'
        f'lower="{lower_value}"\n'
        f'mixed="{runtime_value}"\n'
    )
    source_file.write_text(original_text, encoding="utf-8")

    masked = tmp_path / "case-masked"
    report = asyncio.run(
        export_project(
            db_session,
            source_path=str(source),
            project_name=identity[0],
            sicil_no=identity[1],
            branch_name=identity[2],
            target_path=str(masked),
            initiated_by=identity[1],
        )
    )

    assert report.status == "completed"
    assert any("parser yok" in notice for notice in report.validation_notices)
    masked_files = list(masked.rglob("identity.properties"))
    assert len(masked_files) == 1
    masked_path_placeholder = masked_files[0].parent.name
    assert re.fullmatch(r"mask_proje_adi_\d+", masked_path_placeholder)
    path_mapping = db_session.scalar(select(ValueMapping).where(
        ValueMapping.run_id == report.run_id, ValueMapping.placeholder_value == masked_path_placeholder,
    ))
    assert path_mapping.id != legacy_mapping.id
    assert decrypt_value(path_mapping.original_value_encrypted) == upper_value
    assert decrypt_value(legacy_mapping.original_value_encrypted) == lower_value

    masked_text = masked_files[0].read_text(encoding="utf-8")
    content_placeholders = re.findall(r"mask_proje_adi_\d+", masked_text)
    assert len(set(content_placeholders)) == 3
    assert masked_path_placeholder in content_placeholders

    stored_variants = {
        decrypt_value(row.original_value_encrypted)
        for row in db_session.scalars(
            select(ValueMapping).where(ValueMapping.context_id == context.id, ValueMapping.run_id == report.run_id)
        ).all()
        if decrypt_value(row.original_value_encrypted).casefold() == runtime_value.casefold()
    }
    assert stored_variants == {lower_value, upper_value, runtime_value}

    restored = tmp_path / "case-restored"
    unmask_report = unmask_project(
        db_session,
        source_path=str(masked),
        project_name=identity[0],
        sicil_no=identity[1],
        branch_name=identity[2],
        target_path=str(restored),
        initiated_by=identity[1],
    )

    assert unmask_report.status == "completed"
    assert _sha256_tree(restored) == _sha256_tree(source)
