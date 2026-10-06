import base64
import io
import zipfile
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from app.webapp import import_page
from app.webapp.quick_text import InMemoryUpload

APP_TEST_TIMEOUT_SECONDS = 30
IDENTITY = {"sicil_no": "TEST-1"}


def _report(**overrides):
    values = dict(
        has_unresolved_placeholders=False, status="completed", validation_warnings=[],
        total_placeholders_found=1, total_placeholders_resolved=1, total_placeholders_unresolved=0,
        unresolved_by_placeholder={}, identity_mismatch_suggestion=None, files_excluded=0,
        files_skipped_symlink=0, files_copied_undecodable=0, files_errored=0, target_overwritten=False,
        job_id=None, run_id=5,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _import_app() -> AppTest:
    return AppTest.from_string(
        "from app.webapp.import_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    )


def test_quick_text_mode_shows_masked_and_restored_side_by_side(monkeypatch):
    calls = []

    def fake_unmask_upload(uploaded_files, **kwargs):
        calls.append((uploaded_files, kwargs))
        restored = uploaded_files[0].getvalue().replace(b"__SECRET_1__", b"gercek-parola")
        return SimpleNamespace(
            report=_report(), download_base64=base64.b64encode(restored).decode("ascii"),
            download_filename=f"geri_donusturulmus_{uploaded_files[0].name}",
        )

    monkeypatch.setattr(import_page, "get_identity", lambda: IDENTITY)
    monkeypatch.setattr(import_page.api_client, "unmask_upload", fake_unmask_upload)

    app = _import_app().run()
    app.radio(key="import_mode").set_value(import_page._MODE_TEXT).run()
    app.text_area(key="import_text_input").input("db.password=__SECRET_1__")
    app.selectbox(key="import_text_type").set_value("Properties (.properties)")
    app.button[0].click().run()

    assert not app.exception
    [(uploaded, kwargs)] = calls
    assert uploaded[0].name == "hizli_metin.properties"
    assert kwargs["is_directory_upload"] is False
    # Proje/branch gonderilmez: paketin islem kaydindan okunur.
    assert kwargs["sicil_no"] == "TEST-1" and "project_name" not in kwargs
    assert [code.value for code in app.code] == ["db.password=__SECRET_1__", "db.password=gercek-parola"]
    assert any("Orijinaline Dönüştürülmüş" in markdown.value for markdown in app.markdown)


def test_zip_preview_pairs_only_changed_text_files_by_path():
    masked_zip = io.BytesIO()
    with zipfile.ZipFile(masked_zip, "w") as zf:
        zf.writestr("src/app.properties", "key=__SECRET_1__")
        zf.writestr("src/README.md", "degismedi")
    restored_zip = io.BytesIO()
    with zipfile.ZipFile(restored_zip, "w") as zf:
        zf.writestr("src/app.properties", "key=gercek")
        zf.writestr("src/README.md", "degismedi")

    upload = InMemoryUpload("proje.zip", masked_zip.getvalue())
    preview = import_page._build_preview(
        [upload], is_directory_upload=False, download_bytes=restored_zip.getvalue(),
    )

    assert preview == {"src/app.properties": ("key=__SECRET_1__", "key=gercek")}


def test_preview_skips_binary_content():
    upload = InMemoryUpload("veri.bin", b"\x00\x01__SECRET_1__")
    preview = import_page._build_preview(
        [upload], is_directory_upload=False, download_bytes=b"\x00\x01gercek",
    )
    assert preview == {}
