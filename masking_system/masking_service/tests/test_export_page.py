import io
import zipfile
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from app.webapp import export_page

APP_TEST_TIMEOUT_SECONDS = 30
IDENTITY = {"sicil_no": "TEST-1"}


def _report(**overrides):
    values = dict(
        status="completed", degraded_detectors=[], files_scanned=1, files_masked=1,
        files_copied_text_no_match=0, files_copied_binary=0, files_copied_undecodable=0,
        files_skipped_too_large=0, files_skipped_unsupported=0, files_excluded=0, files_skipped_symlink=0,
        files_errored=0, target_overwritten=False, outcomes=[], run_id=7,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _patch_export(monkeypatch, *, mask, quarantined_count=0):
    calls = []

    def fake_start(uploaded_files, **kwargs):
        calls.append((uploaded_files, kwargs))
        return "job-1"

    def fake_job(job_id):
        result = SimpleNamespace(
            report=_report(), pending_count=0, quarantined_count=quarantined_count,
            validation_failed_count=0, output_token="ab" * 16,
        )
        return SimpleNamespace(status="completed", result=result)

    def fake_download(token):
        uploaded = calls[-1][0][0]
        masked = mask(uploaded.getvalue().decode("utf-8"))
        return _zip({} if masked is None else {uploaded.name: masked})

    monkeypatch.setattr(export_page, "get_identity", lambda: IDENTITY)
    monkeypatch.setattr(export_page.api_client, "start_export_job_upload", fake_start)
    monkeypatch.setattr(export_page.api_client, "get_export_job", fake_job)
    monkeypatch.setattr(export_page.api_client, "download_export_output", fake_download)
    return calls


def _submit_quick_text(text: str, type_label: str) -> AppTest:
    app = AppTest.from_string(
        "from app.webapp.export_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    ).run()
    # Kullanici sicille girer; proje/branch maskeleme ekraninda secilir.
    app.text_input(key="pb_export_project").input("test")
    app.text_input(key="pb_export_branch").input("main")
    app.radio(key="export_mode").set_value(export_page._MODE_TEXT).run()
    app.text_area(key="export_text_input").input(text)
    app.selectbox(key="export_text_type").set_value(type_label)
    app.button[0].click().run()
    return app


def test_quick_text_mode_shows_original_and_masked_side_by_side(monkeypatch):
    calls = _patch_export(monkeypatch, mask=lambda text: text.replace("gercek-parola", "__SECRET_1__"))

    app = _submit_quick_text("db.password=gercek-parola", "Properties (.properties)")

    assert not app.exception
    [(uploaded, kwargs)] = calls
    assert uploaded[0].name == "hizli_metin.properties"
    assert kwargs["is_directory_upload"] is False
    assert (kwargs["project_name"], kwargs["sicil_no"], kwargs["branch_name"]) == ("test", "TEST-1", "main")
    assert [code.value for code in app.code] == ["db.password=gercek-parola", "db.password=__SECRET_1__"]
    assert any("Maskelenmiş Metin" in markdown.value for markdown in app.markdown)


def test_quick_text_not_shown_when_file_is_withheld(monkeypatch):
    _patch_export(monkeypatch, mask=lambda text: None, quarantined_count=1)

    app = _submit_quick_text("sunucu=10.0.0.5", "Düz metin (.txt)")

    assert not app.exception
    assert not app.code
    assert any("güvenlik kontrollerinden geçemediği" in warning.value for warning in app.warning)


def test_masked_quick_text_reads_entry_by_name():
    data = _zip({"hizli_metin.java": "String p = \"__SECRET_1__\";"})
    assert export_page._masked_quick_text(data, "hizli_metin.java") == "String p = \"__SECRET_1__\";"
    assert export_page._masked_quick_text(_zip({}), "hizli_metin.java") is None


def test_export_requires_project_and_branch(monkeypatch):
    calls = _patch_export(monkeypatch, mask=lambda text: text)
    app = AppTest.from_string(
        "from app.webapp.export_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    ).run()
    app.radio(key="export_mode").set_value(export_page._MODE_TEXT).run()
    app.text_area(key="export_text_input").input("x=1")
    app.button[0].click().run()

    assert not app.exception
    assert calls == []
    assert any("Proje Adı boş" in error.value for error in app.error)
