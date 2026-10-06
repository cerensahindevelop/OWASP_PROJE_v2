"""Kurumsal terim sozlugu ozelligi / Adim 7: uctan uca entegrasyon testi.

Kod DEGISIKLIGI yok - sadece Adim 1-6'nin (sema + sifreli saklama +
repository decrypt + rule_engine + export/unmask pipeline'i) GERCEKTEN
birlikte calistigini kanitlar: bir terim yukle -> export_project calistir
-> dosyada maskelendigini gor -> unmask_project ile geri getir -> orijinal
metnin AYNEN geri geldigini dogrula.
"""

from __future__ import annotations

import asyncio

from app.services.exporter import export_project
from app.services.term_upload import commit_term_upload
from app.services.unmasker import unmask_project

_IDENTITY = ("pytest-term-integration", "P-TERM-0001", "pytest-branch")


def test_actual_dictionary_leak_still_quarantines_with_precise_location(db_session, tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.db.models import AuditWarning
    from app.services import exporter
    from app.services.detectors import DetectorOutput
    from app.services.llm_recognizer import LLMRecognitionError

    commit_term_upload(db_session, filename="terms.txt", content=b"ZetaLedger\n", category="pytest_real_leak")

    class MissedDetection:
        async def scan(self, text, metadata=None):
            return DetectorOutput(results=[])

    async def failed_audit(*args, **kwargs):
        # Temiz bir denetimde acik terim otomatik maskelenir (bkz.
        # tests/test_auto_remediation.py); denetim tamamlanamazsa otomatik
        # duzeltme denenmez ve sizinti konumuyla insan onayina duser.
        raise LLMRecognitionError("denetim tamamlanamadi")

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *args, **kwargs: MissedDetection())
    monkeypatch.setattr(exporter, "audit_masked_text", failed_audit)
    source = tmp_path / "src"
    source.mkdir()
    (source / "query.sql").write_text("SELECT *\nFROM PUBLIC.T_ZetaLedger;\n", encoding="utf-8")
    target = tmp_path / "masked"
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="pytest-real-leak", sicil_no="P-LEAK", branch_name="test", initiated_by="P-LEAK",
    ))
    assert report.files_quarantined_pending_audit == 1
    assert not (target / "query.sql").exists()
    item = db_session.scalar(select(AuditWarning).where(AuditWarning.run_id == report.run_id))
    assert item is not None
    assert "Satır 2, sütun 15" in item.reasoning
    assert not item.audit_failed


def test_nineteen_files_with_compound_dictionary_identifiers_export_and_restore(db_session, tmp_path):
    """Dictionary substrings beside dots/calls must survive boundary validation.

    The category also contains the term; neither may leak into placeholders.
    """
    commit_term_upload(
        db_session, filename="terms.txt", content=b"ZetaLedger\n",
        category="pytest_zetaledger",
    )
    samples = [
        ("sql", "SELECT *\nFROM PUBLIC.T_ZetaLedger;\n"),
        ("sql", "SELECT * FROM ZetaLedgerSchema.T_ZetaLedger;\n"),
        ("java", "package org.ZetaLedgerService;\n"),
        ("py", "value = client.ZetaLedgerService()\n"),
        ("json", '{"table": "T_ZetaLedger"}\n'),
        ("future_format", "source=T_ZetaLedger.Member\n"),
        ("txt", "ZetaLedgerService()\n"),
    ]
    originals = {f"file_{i}.{samples[i % len(samples)][0]}": samples[i % len(samples)][1] for i in range(19)}
    source, masked, restored = (tmp_path / name for name in ("src", "masked", "restored"))
    source.mkdir()
    for name, content in originals.items():
        (source / name).write_text(content, encoding="utf-8")
    identity = dict(project_name="pytest-nineteen", sicil_no="P-NINETEEN", branch_name="test")
    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(masked),
        initiated_by="P-NINETEEN", **identity,
    ))
    assert report.files_quarantined_pending_audit == 0, report.summary_text()
    assert report.files_masked == 19, report.summary_text()
    from app.services.term_upload import find_leaked_terms
    for name in originals:
        text = (masked / name).read_text(encoding="utf-8")
        assert "mask_kurumsal_ifade_" in text
        assert "zetaledger" not in text.casefold()
        assert find_leaked_terms(db_session, text) == []
    result = unmask_project(
        db_session, source_path=str(masked), target_path=str(restored),
        initiated_by="P-NINETEEN", **identity,
    )
    assert not result.has_unresolved_placeholders
    for name, original in originals.items():
        assert (restored / name).read_text(encoding="utf-8") == original


def test_uploaded_corporate_term_is_masked_on_export_and_restored_on_unmask(db_session, tmp_path):
    commit_term_upload(
        db_session,
        filename="kurumsal-terimler.txt",
        content=b"AtlasProjesi\n",
        category="pytest_entegrasyon_kat",
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    original_text = (
        "# AtlasProjesi sunucu konfigurasyonu\n"
        "SERVICE_NAME = 'AtlasProjesi'\n"
        "print('AtlasProjesi baslatiliyor')\n"
    )
    (source_dir / "config.py").write_text(original_text, encoding="utf-8")

    masked_dir = tmp_path / "masked"
    export_report = asyncio.run(
        export_project(
            db_session,
            source_path=str(source_dir),
            project_name=_IDENTITY[0],
            sicil_no=_IDENTITY[1],
            branch_name=_IDENTITY[2],
            target_path=str(masked_dir),
            initiated_by=_IDENTITY[1],
        )
    )

    masked_text = (masked_dir / "config.py").read_text(encoding="utf-8")
    assert "AtlasProjesi" not in masked_text
    assert "mask_kurumsal_ifade_" in masked_text
    assert export_report.total_matches >= 3  # yorum + atama + print icinde uc kez gecti

    restored_dir = tmp_path / "restored"
    unmask_report = unmask_project(
        db_session,
        source_path=str(masked_dir),
        project_name=_IDENTITY[0],
        sicil_no=_IDENTITY[1],
        branch_name=_IDENTITY[2],
        target_path=str(restored_dir),
        initiated_by=_IDENTITY[1],
    )

    restored_text = (restored_dir / "config.py").read_text(encoding="utf-8")
    assert restored_text == original_text
    assert not unmask_report.has_unresolved_placeholders


def test_approved_corporate_aliases_mask_package_content_and_directory_path(db_session, tmp_path):
    aliases = ("ZETAQ91", "zetasecureq91")
    commit_term_upload(
        db_session,
        filename="onayli-aliaslar.txt",
        content=("\n".join(aliases) + "\n").encode("utf-8"),
        category="pytest_path_alias",
    )

    source_dir = tmp_path / "alias-source"
    relative = "src/main/java/com/ZETAQ91/zetasecureq91/kayit/Config.java"
    source_file = source_dir / relative
    source_file.parent.mkdir(parents=True)
    original_text = "package com.ZETAQ91.zetasecureq91.kayit;\n"
    source_file.write_text(original_text, encoding="utf-8")

    masked_dir = tmp_path / "alias-masked"
    report = asyncio.run(
        export_project(
            db_session,
            source_path=str(source_dir),
            project_name="pytest-path-alias-project",
            sicil_no="P-PATH-ALIAS-91",
            branch_name="main",
            target_path=str(masked_dir),
            initiated_by="P-PATH-ALIAS-91",
        )
    )

    masked_files = list(masked_dir.rglob("Config.java"))
    assert report.status == "completed"
    assert any("bracket/quote" in notice for notice in report.validation_notices)
    assert len(masked_files) == 1
    masked_relative = masked_files[0].relative_to(masked_dir).as_posix()
    assert masked_relative.startswith("src/main/java/com/")
    assert all(alias.casefold() not in masked_relative.casefold() for alias in aliases)
    assert masked_relative.count("mask_kurumsal_ifade_") == 2
    masked_text = masked_files[0].read_text(encoding="utf-8")
    assert all(alias.casefold() not in masked_text.casefold() for alias in aliases)
    assert masked_text.count("mask_kurumsal_ifade_") == 2

    restored_dir = tmp_path / "alias-restored"
    unmask_report = unmask_project(
        db_session,
        source_path=str(masked_dir),
        project_name="pytest-path-alias-project",
        sicil_no="P-PATH-ALIAS-91",
        branch_name="main",
        target_path=str(restored_dir),
        initiated_by="P-PATH-ALIAS-91",
    )

    assert (restored_dir / relative).read_text(encoding="utf-8") == original_text
    assert not unmask_report.has_unresolved_placeholders


def test_camelcase_adjacent_term_is_masked_and_round_trips(db_session, tmp_path):
    """Orijinal bug raporu: "sicil" gibi bir terim, "girisYapanSicil" gibi
    ayracsiz (camelCase) bitisik bir tanimlayicinin icinde hic
    maskelenmiyordu (bkz. term_upload.py compound_aware_boundary_pattern
    kullanimi). Bu test hem (a) bu tur bir bitisik gecisin export'ta
    GERCEKTEN yakalandigini, hem de (b) TokenBoundaryValidator'in span'i
    tam identifier'a genisletip placeholder'i temiz bir sinira
    yerlestirdigini - yani unmask'in kayipsiz/tam olarak geri
    donebildigini - kanitlar (bkz. token_boundary_validator.py _WORD_CHARS
    Turkce-uyumluluk notu).

    Terim BILEREK sentetik/essiz sectildi ("sicil" degil) - gercek kurumsal
    terim sozlugunde ayni kelimeyle (gercek, kalici) baska bir kural zaten
    aktif olabilir; bu durumda IKI kural da ayni degeri eslestirip HANGISI
    kazanirsa o kuralin placeholder onekini uretir - test kendi eklediginin
    KAZANDIGINI varsaydigi icin, testler-arasi/gercek-veri cakismasina karsi
    kirilgan olmamasi icin essiz bir dize kullanilir."""
    unique_term = "sicilqx9f3"
    commit_term_upload(
        db_session,
        filename="kurumsal-terimler.txt",
        content=f"{unique_term}\n".encode("utf-8"),
        category="pytest_camelcase_kat",
    )

    source_dir = tmp_path / "src3"
    source_dir.mkdir()
    original_text = (
        f"girisYapan{unique_term.capitalize()} = kaydet()\n"
        f"{unique_term}Numarasi = '12345'\n"
        f"print(girisYapan{unique_term.capitalize()})\n"
    )
    (source_dir / "app.py").write_text(original_text, encoding="utf-8")

    masked_dir = tmp_path / "masked3"
    export_report = asyncio.run(
        export_project(
            db_session,
            source_path=str(source_dir),
            project_name="pytest-term-camelcase",
            sicil_no="P-TERM-0003",
            branch_name="pytest-branch",
            target_path=str(masked_dir),
            initiated_by="P-TERM-0003",
        )
    )

    masked_text = (masked_dir / "app.py").read_text(encoding="utf-8")
    # tam identifier genisleyip maskelenmis olmali
    assert f"girisYapan{unique_term.capitalize()}" not in masked_text
    assert f"{unique_term}Numarasi" not in masked_text
    assert "mask_kurumsal_ifade_" in masked_text
    assert export_report.total_matches >= 3

    restored_dir = tmp_path / "restored3"
    unmask_report = unmask_project(
        db_session,
        source_path=str(masked_dir),
        project_name="pytest-term-camelcase",
        sicil_no="P-TERM-0003",
        branch_name="pytest-branch",
        target_path=str(restored_dir),
        initiated_by="P-TERM-0003",
    )

    restored_text = (restored_dir / "app.py").read_text(encoding="utf-8")
    assert restored_text == original_text
    assert not unmask_report.has_unresolved_placeholders


def test_suspicious_term_stays_inactive_and_is_never_masked(db_session, tmp_path):
    """'data' gibi supheli bir terim pasif eklendigi icin export sirasinda
    HIC eslesmemeli - varsayilan pasif ekleme davranisinin gercek pipeline
    uzerinde de gecerli oldugunu kanitlar."""
    commit_term_upload(
        db_session,
        filename="supheli.txt",
        content=b"data\n",
        category="pytest_entegrasyon_supheli",
    )

    source_dir = tmp_path / "src2"
    source_dir.mkdir()
    original_text = "data = load_data()\n"
    (source_dir / "app.py").write_text(original_text, encoding="utf-8")

    masked_dir = tmp_path / "masked2"
    asyncio.run(
        export_project(
            db_session,
            source_path=str(source_dir),
            project_name="pytest-term-integration-supheli",
            sicil_no="P-TERM-0002",
            branch_name="pytest-branch",
            target_path=str(masked_dir),
            initiated_by="P-TERM-0002",
        )
    )

    masked_text = (masked_dir / "app.py").read_text(encoding="utf-8")
    assert masked_text == original_text  # pasif kural hicbir sey maskelemedi


def test_authoritative_qualified_identifiers_are_masked_in_sql_and_unknown_text_files(
    db_session, tmp_path
):
    """A trusted full-token term next to ``.`` must not make the whole file
    disappear.  The policy is content based: SQL, a future/unknown extension
    and an extensionless text file all take the same safe replacement path.
    """
    schema_name = "ZETA_SECURE_SCHEMA_Q91"
    table_name = "T_LEDGER_PRIVATE_Q91"
    commit_term_upload(
        db_session,
        filename="qualified-identifiers.txt",
        content=f"{schema_name}\n{table_name}\n".encode("utf-8"),
        category="pytest_qualified_identifier",
    )

    source_dir = tmp_path / "qualified-src"
    original_files = {
        # Dosya yolu, gercek kurumsal terim sozlugunde COK YAYGIN olabilecek
        # gundelik kelimeler (orn. "kurum") ICERMEMELI - path masking bu tur
        # bir kelimeyi baska (test disi, gercek) bir aktif kurala karsi
        # yanlislikla eslestirip testi kirilgan hale getirebilir.
        "sql/query_q91zz.sql": f"SELECT *\nFROM {schema_name}.{table_name};\n",
        "mock/records.mockdb": f"source={schema_name}.{table_name}\n",
        "definitions/query.future_format": f"lookup {schema_name}.{table_name}\n",
        "NO_EXTENSION": f"qualified-name: {schema_name}.{table_name}\n",
    }
    for relative_path, original_text in original_files.items():
        path = source_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original_text, encoding="utf-8")

    masked_dir = tmp_path / "qualified-masked"
    report = asyncio.run(
        export_project(
            db_session,
            source_path=str(source_dir),
            project_name="pytest-qualified-identifier",
            sicil_no="P-TERM-Q91",
            branch_name="pytest-branch",
            target_path=str(masked_dir),
            initiated_by="P-TERM-Q91",
        )
    )

    assert report.files_quarantined_pending_audit == 0
    assert report.files_failed_consistency_validation == 0
    for relative_path in original_files:
        output_path = masked_dir / relative_path
        assert output_path.is_file(), f"maskelenmis cikti dosyasi eksik: {relative_path}"
        masked_text = output_path.read_text(encoding="utf-8")
        assert schema_name not in masked_text
        assert table_name not in masked_text
        assert "." in masked_text
        assert masked_text.count("mask_kurumsal_ifade_") == 2

    restored_dir = tmp_path / "qualified-restored"
    unmask_report = unmask_project(
        db_session,
        source_path=str(masked_dir),
        project_name="pytest-qualified-identifier",
        sicil_no="P-TERM-Q91",
        branch_name="pytest-branch",
        target_path=str(restored_dir),
        initiated_by="P-TERM-Q91",
    )

    assert not unmask_report.has_unresolved_placeholders
    for relative_path, original_text in original_files.items():
        assert (restored_dir / relative_path).read_text(encoding="utf-8") == original_text
