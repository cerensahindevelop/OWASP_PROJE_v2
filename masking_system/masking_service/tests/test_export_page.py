import io
import zipfile
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from app.webapp import export_page
from app.webapp.quick_text import AUTO_DETECT_LABEL

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


def test_waiting_job_is_visible_to_user(monkeypatch):
    states = iter([SimpleNamespace(status="queued"), SimpleNamespace(status="completed", result="done")])
    messages = []
    bar = SimpleNamespace(progress=lambda value, text: messages.append(text), empty=lambda: None)
    monkeypatch.setattr(export_page.st, "progress", lambda *a, **kw: bar)
    monkeypatch.setattr(export_page.api_client, "get_export_job", lambda _: next(states))
    monkeypatch.setattr(export_page.time, "sleep", lambda _: None)
    assert export_page._wait_for_export_job("queued-job") == "done"
    assert any("Sıradasınız" in message for message in messages)


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
    assert [code.value for code in app.code] == ["db.password=gercek-parola"]
    [masked_box] = app.text_area[1:]  # [0]: giris kutusu
    assert masked_box.value == "db.password=__SECRET_1__"
    assert any("Maskelenmiş Metin" in markdown.value for markdown in app.markdown)


def test_quick_text_output_is_editable_and_resettable(monkeypatch):
    _patch_export(monkeypatch, mask=lambda text: text.replace("gercek-parola", "__SECRET_1__"))
    app = _submit_quick_text("db.password=gercek-parola", "Properties (.properties)")
    masked_box = app.text_area[1]

    masked_box.input("db.password=__SECRET_1__\n# not eklendi").run()

    assert not app.exception
    assert app.text_area[1].value == "db.password=__SECRET_1__\n# not eklendi"
    reset = [b for b in app.button if "Geri Al" in b.label]
    assert reset, "duzenleme sonrasi geri alma dugmesi gorunmeli"
    reset[0].click().run()
    assert app.text_area[1].value == "db.password=__SECRET_1__"
    assert not [b for b in app.button if "Geri Al" in b.label]


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


def test_conflict_offers_stop_and_restart(monkeypatch):
    from app.webapp.api_client import ApiError

    attempts, cancelled = [], []

    def fake_start(uploaded_files, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ApiError("devam eden tarama var", None, 409, active_job_id="old-job")
        return "new-job"

    def fake_job(job_id):
        if job_id == "old-job":
            return SimpleNamespace(status="cancelled", error_message="Tarama durduruldu.")
        result = SimpleNamespace(report=_report(), pending_count=0, quarantined_count=0,
                                 validation_failed_count=0, output_token="ab" * 16)
        return SimpleNamespace(status="completed", result=result)

    monkeypatch.setattr(export_page, "get_identity", lambda: IDENTITY)
    monkeypatch.setattr(export_page.api_client, "start_export_job_upload", fake_start)
    monkeypatch.setattr(export_page.api_client, "get_export_job", fake_job)
    monkeypatch.setattr(export_page.api_client, "cancel_export_job", lambda job_id: cancelled.append(job_id))
    monkeypatch.setattr(export_page.api_client, "download_export_output", lambda token: _zip({"hizli_metin.txt": "x"}))

    app = _submit_quick_text("x=1", "Düz metin (.txt)")
    assert not app.exception
    assert any("devam eden bir tarama var" in warning.value for warning in app.warning)

    app.button(key="export_conflict_restart").click().run()

    assert not app.exception
    assert cancelled == ["old-job"]
    assert len(attempts) == 2
    assert app.session_state["export_last_result"]["quick_text"]["original"] == "x=1"
    assert not any("devam eden bir tarama var" in warning.value for warning in app.warning)


def test_running_job_survives_rerun_and_can_be_stopped(monkeypatch):
    states = {"status": "running"}
    cancelled = []

    def fake_cancel(job_id):
        cancelled.append(job_id)
        states["status"] = "cancelled"

    monkeypatch.setattr(export_page, "get_identity", lambda: IDENTITY)
    monkeypatch.setattr(export_page.api_client, "cancel_export_job", fake_cancel)
    def fake_wait(job_id):
        if states["status"] == "cancelled":
            raise export_page._JobCancelled("Tarama durduruldu.")
        # Gercekte bekleme, kullanici bir seye tiklayip betik yeniden
        # calisana kadar surer; burada o ani st.stop() ile taklit ederiz.
        export_page.st.stop()

    monkeypatch.setattr(export_page, "_wait_for_export_job", fake_wait)

    app = AppTest.from_string(
        "from app.webapp.export_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    )
    app.session_state["export_active_job"] = {"job_id": "job-9", "quick_text": None}
    # Takip edilen is varken sayfa (yeniden) acilinca Durdur dugmesi gorunur.
    app.run()
    assert not app.exception
    app.button(key="export_stop_job-9").click().run()

    assert not app.exception
    assert cancelled == ["job-9"]
    assert app.session_state["export_active_job"] is None
    assert any("durduruldu" in info.value for info in app.info)


def test_quick_text_auto_detects_type_and_says_so(monkeypatch):
    calls = _patch_export(monkeypatch, mask=lambda text: text)
    app = _submit_quick_text('{"db": {"host": "10.0.0.5"}}', AUTO_DETECT_LABEL)
    assert not app.exception
    [(uploaded, _kwargs)] = calls
    assert uploaded[0].name == "hizli_metin.json"
    assert any("Algılanan dosya türü" in caption.value and "JSON" in caption.value for caption in app.caption)


def test_quick_text_defaults_to_auto_detect():
    app = AppTest.from_string(
        "from app.webapp.export_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    ).run()
    app.radio(key="export_mode").set_value(export_page._MODE_TEXT).run()
    assert app.selectbox(key="export_text_type").value == AUTO_DETECT_LABEL


def _run_upload_script() -> AppTest:
    # file_uploader AppTest'te surulemedigi icin yukleme akisini dogrudan cagiririz.
    script = (
        "from app.webapp import export_page\n"
        "from app.webapp.quick_text import InMemoryUpload\n"
        "import streamlit as st\n"
        "if not st.session_state.get('submitted'):\n"
        "    st.session_state['submitted'] = True\n"
        "    export_page._run_export_upload([InMemoryUpload('veri.csv', b'1,ayse\\n' * 10)],\n"
        "                                   project_name='test', branch_name='main')\n"
        "export_page._render_slow_files()\n"
    )
    return AppTest.from_string(script, default_timeout=APP_TEST_TIMEOUT_SECONDS).run()


def _patch_preflight(monkeypatch, slow_files):
    manifests, starts = [], []

    def fake_preflight(manifest):
        manifests.append(manifest)
        return SimpleNamespace(warn_mb=2.0, slow_files=[SimpleNamespace(**item) for item in slow_files])

    monkeypatch.setattr(export_page, "get_identity", lambda: IDENTITY)
    monkeypatch.setattr(export_page.api_client, "export_preflight_upload", fake_preflight)
    monkeypatch.setattr(export_page.api_client, "start_export_job_upload",
                        lambda files, **kwargs: starts.append(kwargs) or "job-1")
    return manifests, starts


def test_large_file_asks_before_starting_and_can_continue(monkeypatch):
    manifests, starts = _patch_preflight(monkeypatch, [{"path": "veri.csv", "size_bytes": 12 * 1024 * 1024}])

    app = _run_upload_script()

    assert not app.exception
    # Dosyanin kendisi degil yalnizca yol/boyut/metin-mi bilgisi gonderilir.
    assert manifests == [[{"path": "veri.csv", "size_bytes": 70, "is_text": True}]]
    assert starts == []
    warning = " ".join(w.value for w in app.warning)
    assert "Uzun sürebilir" in warning and "Ne yapmalı" in warning and "2 MB sınırından" in warning
    assert any("veri.csv" in m.value and "12,0 MB" in m.value for m in app.markdown)

    app.button(key="export_slow_continue").click().run()

    assert not app.exception
    assert len(starts) == 1
    assert starts[0]["skip_paths"] == []
    assert app.session_state["export_active_job"]["job_id"] == "job-1"
    assert not any("Uzun sürebilir" in w.value for w in app.warning)


def test_large_file_can_be_skipped_without_reuploading(monkeypatch):
    _manifests, starts = _patch_preflight(monkeypatch, [{"path": "veri.csv", "size_bytes": 12 * 1024 * 1024}])

    app = _run_upload_script()
    assert app.button(key="export_slow_skip").label == "Dosyayı çıkarıp başlat"
    app.button(key="export_slow_skip").click().run()

    assert not app.exception
    assert len(starts) == 1
    assert starts[0]["skip_paths"] == ["veri.csv"]


def test_user_picks_which_large_files_to_skip(monkeypatch):
    slow = [{"path": "veri/a.csv", "size_bytes": 12 * 1024 * 1024}, {"path": "kontrol/b.jsonl", "size_bytes": 49 * 1024 * 1024}]
    _manifests, starts = _patch_preflight(monkeypatch, slow)

    app = _run_upload_script()
    assert app.multiselect(key="export_slow_pick").value == ["veri/a.csv", "kontrol/b.jsonl"]
    app.multiselect(key="export_slow_pick").unselect("veri/a.csv").run()
    app.button(key="export_slow_skip").click().run()

    assert not app.exception
    assert starts[0]["skip_paths"] == ["kontrol/b.jsonl"]


def test_result_names_files_the_user_skipped(monkeypatch):
    report = _report(files_excluded=3, user_skipped_paths=["veri/a.csv"])
    monkeypatch.setattr(export_page.st, "caption", lambda text: captions.append(text))
    captions = []
    export_page._render_result({"report": report, "pending_count": 0, "quarantined_count": 0})
    assert any("2 dosya hariç tutma kuralı" in c for c in captions)
    assert any("Taramadan çıkardığınız 1 büyük dosya" in c and "veri/a.csv" in c for c in captions)


def test_large_file_warning_can_be_dismissed(monkeypatch):
    _manifests, starts = _patch_preflight(monkeypatch, [{"path": "veri.csv", "size_bytes": 12 * 1024 * 1024}])

    app = _run_upload_script()
    app.button(key="export_slow_dismiss").click().run()

    assert not app.exception
    assert starts == []
    assert not any("Uzun sürebilir" in w.value for w in app.warning)


def test_small_upload_starts_without_asking(monkeypatch):
    _manifests, starts = _patch_preflight(monkeypatch, [])

    app = _run_upload_script()

    assert not app.exception
    assert len(starts) == 1
    assert not app.warning


def test_size_uses_same_mb_as_scan_limit():
    # 51.885.863 bayt, SCAN_MAX_FILE_MB=50 (50 x 1.048.576) sinirinin altindadir;
    # uyari onu sinirdan buyukmus gibi "51,9 MB" gostermemeli.
    assert export_page._format_mb(51_885_863) == "49,5 MB"
    assert export_page._format_mb(50 * 1024 * 1024) == "50,0 MB"
