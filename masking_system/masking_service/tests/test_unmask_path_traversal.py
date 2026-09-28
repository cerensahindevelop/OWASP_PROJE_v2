"""unmask_project artik dosya YOLUNU (icerigi degil) geri cozerken path
traversal'a izin vermiyor. Kok neden: Path('/x') / Path('/etc/passwd') ==
Path('/etc/passwd') (sol taraf atilir) - eger placeholder_map'ten cozulen
bir deger '/' iceriyorsa, TUM goreli yolu tek bir string olarak
reverse_text'e verip sonucu dogrudan target/Path(...) ile birlestirmek,
hedef klasorun disina yazmaya izin verirdi. Duzeltme: her path BILESENI
ayri ayri cozulur ve '/', '\\', '.', '..' iceren sonuclar reddedilir.

Once pure-logic (DB'siz) testler, sonra unmask_project uzerinden uctan uca
bir entegrasyon testi.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services.unmasker import (
    UnsafeUnmaskPathError,
    _reverse_path_component,
    _reverse_relative_path,
)

_IDENTITY_PREFIX = "pytest-unmask-traversal"


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
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    for project_name in created:
        _cleanup_identity(project_name)


class TestReversePathComponentIsPathSafe:
    def test_normal_placeholder_resolves_normally(self):
        result = _reverse_path_component("mask_proje_adi_1", {"mask_proje_adi_1": "Poseidon"})
        assert result == "Poseidon"

    def test_component_with_no_placeholder_is_unchanged(self):
        assert _reverse_path_component("config.py", {}) == "config.py"

    def test_resolved_value_containing_forward_slash_is_rejected(self):
        with pytest.raises(UnsafeUnmaskPathError):
            _reverse_path_component("mask_proje_adi_1", {"mask_proje_adi_1": "../../etc/passwd"})

    def test_resolved_value_containing_backslash_is_rejected(self):
        with pytest.raises(UnsafeUnmaskPathError):
            _reverse_path_component("mask_proje_adi_1", {"mask_proje_adi_1": "..\\..\\windows"})

    def test_resolved_value_equal_to_dotdot_is_rejected(self):
        with pytest.raises(UnsafeUnmaskPathError):
            _reverse_path_component("mask_branch_1", {"mask_branch_1": ".."})


class TestReverseRelativePathAppliesPerComponent:
    def test_multi_segment_path_resolves_each_component_independently(self):
        placeholder_map = {"mask_proje_adi_1": "Poseidon", "mask_personel_no_1": "EMP-1001"}
        result = _reverse_relative_path(Path("mask_proje_adi_1/mask_personel_no_1/config.py"), placeholder_map)
        assert result == Path("Poseidon/EMP-1001/config.py")

    def test_malicious_value_in_one_component_does_not_escape_via_join(self):
        # Onceki (hatali) davranista tum goreli yol TEK bir string olarak
        # reverse_text'e verilip target/Path(...) ile birlestiriliyordu - bu
        # test o senaryoyu bilerek TEK bir bilesende kurar.
        placeholder_map = {"mask_proje_adi_1": "../../etc"}
        with pytest.raises(UnsafeUnmaskPathError):
            _reverse_relative_path(Path("mask_proje_adi_1/passwd"), placeholder_map)


def test_unmask_project_skips_file_instead_of_escaping_target_when_value_has_slash(cleanup, tmp_path):
    from app.core.crypto import encrypt_value, hash_value
    from app.db.models import ValueMapping
    from app.services.mapping_service import get_or_create_context
    from app.services.unmasker import unmask_project

    project = f"{_IDENTITY_PREFIX}-slash"
    cleanup.append(project)
    identity = (project, "P-TEST-0001", "pytest-branch")

    # Gercekci olmayan ama DB seviyesinde tamamen gecerli bir deger: bir
    # onceki export sirasinda proje adi olarak '/' iceren bir string
    # kaydedilmis olabilir (parametrik kurallar proje/sicil/branch
    # adinda '/' karakterini reddetmiyor). Bu deger bir KLASOR ADI
    # placeholder'ina (mask_proje_adi_1) baglaniyor.
    malicious_value = "../../outside"
    with SessionLocal() as db:
        context = get_or_create_context(db, *identity)
        db.add(
            ValueMapping(
                context_id=context.id,
                rule_id=None,
                original_value_encrypted=encrypt_value(malicious_value),
                original_value_plain=malicious_value,
                original_value_hash=hash_value(context.id, malicious_value),
                placeholder_value="mask_proje_adi_1",
            )
        )
        db.commit()

    source_dir = tmp_path / "masked_source"
    (source_dir / "mask_proje_adi_1").mkdir(parents=True)
    (source_dir / "mask_proje_adi_1" / "secret.txt").write_text("icerik\n", encoding="utf-8")
    target_dir = tmp_path / "unmasked_target"
    outside_marker = tmp_path / "outside"  # traversal hedefi olarak kullanilacak dizin - VAR OLMAMALI

    with SessionLocal() as db:
        report = unmask_project(
            db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
            branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
        )
        db.commit()

    # Guvenlik ihlali OLMADI: '../../outside' hedef klasorun DISINA hicbir
    # dosya yazmadi.
    assert not outside_marker.exists()
    # Dosya sessizce atlanmadi - basarisiz/uyarili olarak rapor edildi.
    assert report.status in ("completed_with_warnings", "failed") or report.files_errored >= 1
