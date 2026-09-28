"""Bolum 4 tam regresyon test seti (Test A-G).

Test G, bu dosyada YENIDEN YAZILMAZ - "daha once yazilmis testleri tekrar
calistir" gereksinimi, tum test suite'inin (`pytest tests/`) birlikte
calistirilmasiyla saglanir; bu dosyadaki testler A-F'yi kapsar.
"""

from __future__ import annotations

import ast
import asyncio
import re

import pytest
from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.mapping_service import MaskingRunContext, get_or_create_context, mask_text
from app.services.syntax_validator import validate_masked_syntax

_IDENTITY_PREFIX = "pytest-part4"


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


def _mask(content: str, project_name: str, file_path: str = "sample.py") -> tuple[str, list]:
    identity = (project_name, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        context = get_or_create_context(db, *identity)
        run_ctx = MaskingRunContext(
            context=context,
            runtime_params={"project_name": identity[0], "sicil_no": identity[1], "branch_name": identity[2]},
        )
        masked_text, mappings = mask_text(db, run_ctx, content, file_path=file_path)
        db.commit()
    return masked_text, mappings


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    for project_name in created:
        _cleanup_identity(project_name)


# --------------------------------------------------------------------------
# Test A - syntax_koruma
# --------------------------------------------------------------------------

_SAMPLE_PY = '''\
import requests
import psycopg2
from flask import Flask

app = Flask(__name__)

GOOGLE_API_KEY = 'AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere'


@app.route('/req_1')
def handler():
    headers = {}
    results = requests.get("http://internal.example.com/api", headers=headers)
    conn = psycopg2.connect(host="db.internal", dbname="prod", user="admin", password="S3cr3tPassw0rdVal!123")
    return results, conn


if __name__ == "__main__":
    app.run(debug=True)
'''


def test_a_syntax_koruma(cleanup):
    project = f"{_IDENTITY_PREFIX}-a"
    cleanup.append(project)

    masked, mappings = _mask(_SAMPLE_PY, project)

    ast.parse(masked)  # ana dogrulama: gecerli Python syntax'i
    assert len(mappings) > 0, "hic bir sey maskelenmedi - test kurgusu hatali olabilir"

    for intact in ("@app.route(", "requests.get(", "psycopg2.connect("):
        assert intact in masked, f"kutuphane cagrisi bozulmus: {intact!r}"
    assert "app.run(debug=True)" in masked

    # Fonksiyon/metod isimlerinin HICBIRI placeholder icermemeli.
    for identifier in ("route", "get", "connect", "run", "handler"):
        assert not re.search(rf"{identifier}mask_[a-z_]*_\d+", masked)

    # API key/password GERCEKTEN maskelenmis olmali (recall dogrulamasi).
    assert "AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actualKeyHere" not in masked
    assert "S3cr3tPassw0rdVal!123" not in masked


# --------------------------------------------------------------------------
# Test B - string_butunlugu
# --------------------------------------------------------------------------


def test_b_string_butunlugu(cleanup):
    project = f"{_IDENTITY_PREFIX}-b"
    cleanup.append(project)

    content = "API_SECRET = 'AbCdEfGhIjKlMnOpQrSt1234567890'\n"
    masked, mappings = _mask(content, project)

    assert len(mappings) == 1
    line = masked.strip()
    # Tam olarak bir tirnak cifti, orjinal deger tamamen kaybolmus, aralarinda
    # duz bir placeholder token var - kismi/dengesiz tirnak YOK.
    match = re.match(r"^API_SECRET = '([a-z0-9_]+)'$", line)
    assert match is not None, f"string literal butunlugu bozulmus: {line!r}"
    assert "AbCdEfGhIjKlMnOpQrSt1234567890" not in masked
    assert line.count("'") == 2


# --------------------------------------------------------------------------
# Test C - ortusen_eslesme (ayni araligi Katman 2 CRYPTO, Katman 3
# GIZLI_ANAHTAR olarak isaretlesin diye kurgulanmis senaryo)
# --------------------------------------------------------------------------


def test_c_ortusen_eslesme(cleanup, monkeypatch):
    project = f"{_IDENTITY_PREFIX}-c"
    cleanup.append(project)

    content = "PRIVATE_KEY_BLOB = 'MIIEpAIBAAKCAQEA7X9nP2vQm5Kj3RtY8wXz1LpN4sB6dH0gC2fE9uV7aI5oT3kM'\n"
    content_start = content.index("MIIEpAIBAAKCAQEA")
    content_end = content.rindex("'")
    span = (content_start, content_end)

    crypto_result = DetectionResult(
        deger=content[span[0]:span[1]], tip="CRYPTO", guven_seviyesi="orta",
        kaynak_motor="katman2_presidio", start=span[0], end=span[1], rule=synthetic_llm_rule("CRYPTO"),
    )
    gizli_anahtar_result = DetectionResult(
        deger=content[span[0]:span[1]], tip="GIZLI_ANAHTAR", guven_seviyesi="yuksek",
        kaynak_motor="llm", start=span[0], end=span[1],
    )

    class _FakeOrchestrator:
        async def scan(self, text, metadata=None):
            return DetectorOutput(results=[crypto_result, gizli_anahtar_result])

    import app.services.mapping_service as mapping_service_module

    monkeypatch.setattr(mapping_service_module, "build_orchestrator", lambda *a, **kw: _FakeOrchestrator())

    masked, mappings = _mask(content, project)

    # TEK bir tutarli placeholder uretilmeli. Presidio ve LLM ayni sezgisel
    # otorite grubundadir; yuksek confidence'li LLM bulgusu kazanir.
    assert len(mappings) == 1
    placeholder_count = len(re.findall(r"mask_[a-z_]+_\d+", masked))
    assert placeholder_count == 1, f"birden fazla/cakisan placeholder uretildi: {masked!r}"
    assert "gizli_anahtar" in masked
    assert "crypto" not in masked
    assert "MIIEpAIBAAKCAQEA" not in masked


# --------------------------------------------------------------------------
# Test D - presidio_kategori_kisitlamasi
# --------------------------------------------------------------------------

_HEX_BLOB = "4f8a9c21e6b3d0157fa2c8e93b41d6a07c5e2f918b34a6d0e7c19f2b5a83d604"


def test_d_presidio_kategori_kisitlamasi_py(cleanup):
    project = f"{_IDENTITY_PREFIX}-d-py"
    cleanup.append(project)

    content = f"SESSION_TOKEN_HEX = '{_HEX_BLOB}'\n"
    masked, _mappings = _mask(content, project)

    for wrong in ("mask_personel", "mask_organization", "mask_us_driver_license"):
        assert wrong not in masked, f".py dosyasinda hala yanlis kategori uretiliyor: {wrong}"


def test_d_presidio_kategori_kisitlamasi_md_person_still_works(cleanup):
    project = f"{_IDENTITY_PREFIX}-d-md"
    cleanup.append(project)

    identity = (project, "P-TEST-0001", "pytest-branch")
    content = f"Rastgele kod: {_HEX_BLOB}\nBu belge John Smith tarafindan hazirlanmistir.\n"
    with SessionLocal() as db:
        context = get_or_create_context(db, *identity)
        run_ctx = MaskingRunContext(
            context=context,
            runtime_params={"project_name": identity[0], "sicil_no": identity[1], "branch_name": identity[2]},
        )
        masked, _mappings = mask_text(db, run_ctx, content, file_path="README.md")
        db.commit()

    for wrong in ("mask_organization", "mask_us_driver_license"):
        assert wrong not in masked, f".md dosyasinda hex blob yanlislikla {wrong} olarak isaretlendi"
    assert "John Smith" not in masked, "PERSON kategorisi .md dosyasinda calismiyor (gercek isim maskelenmedi)"


# --------------------------------------------------------------------------
# Test E - post_export_dogrulama
# --------------------------------------------------------------------------


def test_e_post_export_dogrulama(cleanup, monkeypatch, tmp_path):
    project = f"{_IDENTITY_PREFIX}-e"
    cleanup.append(project)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "broken.py").write_text('password = "S3cr3tPassw0rdVal!123"\n', encoding="utf-8")
    (source_dir / "clean.py").write_text("clean_value = 2\n", encoding="utf-8")
    target_dir = tmp_path / "target"

    import app.services.exporter as exporter_module

    # exporter.py artik dogrudan mask_text() cagirmiyor - detect_matches
    # (async, saf) + apply_detections (senkron, DB yazan) olarak ikiye
    # bolundu (bkz. app/services/mapping_service.py). Bu testin niyeti
    # AYNI: gercek mapping'ler DEGISMEDEN, SADECE donen masked_text'i bir
    # dosya icin bozmak - artik apply_detections'i sarmaliyoruz.
    original_apply_detections = exporter_module.apply_detections

    def _fake_apply_detections(db, run_ctx, text, outcome, file_path=None, **kwargs):
        masked_text, mappings = original_apply_detections(db, run_ctx, text, outcome, file_path=file_path, **kwargs)
        if file_path == "broken.py":
            # Gercek eslemeler (mappings) DB'ye zaten yazildi ve degismeden
            # donduruluyor - SADECE donen METIN kasitli olarak bozuk
            # sozdizimiyle degistiriliyor, bu testin amaci sadece
            # post-export dogrulama gatesinin calistigini kanitlamak.
            # NOT: bu sahte metin gercek placeholder'i (mappings) hic
            # icermiyor - bu yuzden artik SIRADAKI (daha erken calisan)
            # round-trip dogrulamasi (bkz. roundtrip_validator.py) bunu
            # sozdizimi kontrolune gelmeden yakalar: geri cozulmus metin
            # (bu sahte metinde hic placeholder olmadigi icin degismeden
            # kalir) orijinalle uyusmaz. Bu, syntax_validator'dan DAHA
            # temel bir kontrol oldugu icin beklenen/dogru davranistir.
            return "def f(:\n    broken syntax here\n", mappings
        return masked_text, mappings

    monkeypatch.setattr(exporter_module, "apply_detections", _fake_apply_detections)

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        # export_project artik async (bkz. exporter.py modul dokstring'i,
        # vLLM coklu-istek gerekcesi) - tek seferlik asyncio.run() ile sarmalanir.
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
        run_id = report.run_id

    assert report.files_failed_round_trip_validation == 1
    assert report.status == "completed_with_warnings"
    assert "round-trip" in report.summary_text().lower()

    failed_dir = target_dir.parent / f"basarisiz_dosyalar_{run_id}"
    assert (failed_dir / "broken.py").exists(), "basarisiz dosya basarisiz_dosyalar/ altina tasinmadi"
    assert not (target_dir / "broken.py").exists(), "basarisiz dosya YANLISLIKLA hedef klasore yazilmis"
    assert (target_dir / "clean.py").exists(), "saglikli dosya normal sekilde disa aktarilmali"

    with SessionLocal() as db:
        rows = db.execute(
            sqltext("SELECT eylem FROM denetim_kaydi WHERE calisma_id=:r AND eylem='round_trip_hatasi'"),
            {"r": run_id},
        ).all()
    assert len(rows) == 1, "round_trip_hatasi audit log kaydi olusmadi"


def test_e2_syntax_validation_gate_in_isolation(cleanup, monkeypatch, tmp_path):
    # test_e_post_export_dogrulama'daki senaryo (mask_text'in mappings'le
    # ILGISIZ, tamamen bozuk bir metin dondurmesi) artik round-trip
    # dogrulamasi tarafindan (syntax kontrolunden ONCE) yakalaniyor - bu
    # yuzden syntax_validator'in KENDI gate'ini (validate_masked_syntax'in
    # dondurdugu hata -> dosya karantinaya alinip raporlaniyor) izole
    # test etmek icin round-trip'i etkilemeden SADECE syntax kontrolunu
    # sahteliyoruz.
    project = f"{_IDENTITY_PREFIX}-e2"
    cleanup.append(project)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "broken.py").write_text('password = "S3cr3tPassw0rdVal!123"\n', encoding="utf-8")
    (source_dir / "clean.py").write_text("clean_value = 2\n", encoding="utf-8")
    target_dir = tmp_path / "target"

    import app.services.exporter as exporter_module

    original_validate = exporter_module.validate_masked_syntax

    def _fake_validate(relative_path, masked_text, original_text=None, **kwargs):
        if relative_path == "broken.py":
            return "Python sozdizimi hatasi: sahte hata (satir 1)"
        return original_validate(relative_path, masked_text, original_text=original_text, **kwargs)

    monkeypatch.setattr(exporter_module, "validate_masked_syntax", _fake_validate)

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()
        run_id = report.run_id

    assert report.files_failed_syntax_validation == 1
    assert report.files_failed_round_trip_validation == 0
    assert report.status == "completed_with_warnings"
    assert "sozdizimini bozdu, bu dosya disa aktarilmadi" in report.summary_text()

    failed_dir = target_dir.parent / f"basarisiz_dosyalar_{run_id}"
    assert (failed_dir / "broken.py").exists(), "basarisiz dosya basarisiz_dosyalar/ altina tasinmadi"
    assert not (target_dir / "broken.py").exists(), "basarisiz dosya YANLISLIKLA hedef klasore yazilmis"
    assert (target_dir / "clean.py").exists(), "saglikli dosya normal sekilde disa aktarilmali"


# --------------------------------------------------------------------------
# Test F - tutarli_recall
# --------------------------------------------------------------------------

_TWO_FUNCTIONS = '''\
def connect_primary():
    password = "S3cr3tPassw0rdVal!123"
    return password


def connect_secondary():
    db_password = "An0therSecretVal!456"
    return db_password
'''


def test_f_tutarli_recall(cleanup):
    project = f"{_IDENTITY_PREFIX}-f"
    cleanup.append(project)

    masked, mappings = _mask(_TWO_FUNCTIONS, project)

    assert "S3cr3tPassw0rdVal!123" not in masked, "duz 'password=' maskelenmedi"
    assert "An0therSecretVal!456" not in masked, "onekli 'db_password=' maskelenmedi - tutarsiz recall"
    assert len(mappings) == 2
