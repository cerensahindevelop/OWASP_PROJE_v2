from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from app.webapp import review_page
from app.webapp.api_client import ApiError

# AppTest varsayilan 3 sn: yuk altindaki makinede (tam test takimi, CI)
# sayfanin ilk cizimi bunu asabiliyor ("AppTest script run timed out after
# 3(s)"). Sinir yalnizca asili kalan bir calistirmayi yakalamak icindir.
APP_TEST_TIMEOUT_SECONDS = 30


@pytest.fixture(autouse=True)
def _no_background_revalidation(monkeypatch):
    # Sayfa her cizimde bekleyen uyarilari yeniden dogrulamaya aday gosterir;
    # testlerde backend yok, hicbir dosya otomatik dogrulamada degil.
    monkeypatch.setattr(review_page.api_client, "revalidate_pending_audit_warnings",
                        lambda **_: SimpleNamespace(scheduled=0, active_ids=[]))


def _review_app() -> AppTest:
    return AppTest.from_string(
        "from app.webapp.review_page import render\nrender()", default_timeout=APP_TEST_TIMEOUT_SECONDS,
    )


def test_review_page_shows_file_location_reason_and_separate_actions(monkeypatch):
    monkeypatch.setattr(review_page, "get_identity", lambda: {
        "project_name": "test", "sicil_no": "TEST-1", "branch_name": "test",
    })
    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings", lambda **_: [
        SimpleNamespace(
            id=7, run_id=42, file_path="sql/query.sql", audit_failed=False,
            reasoning="Technical detail", summary="Maskelenmeden kalan kurumsal terim: proje.",
            location="Satır 9, sütun 20", next_step="Projeyi yeniden dışa aktarın.",
            evidence=[SimpleNamespace(line=9, column=20, found_value="T_PROJE", excerpt="FROM X.⟦T_PROJE⟧",
                                      label="Kurumsal terim")],
        ),
    ])
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    app = _review_app().run()
    assert not app.exception
    assert any("query.sql" in markdown.value for markdown in app.markdown)
    assert any("sql/query.sql" in caption.value for caption in app.caption)
    assert any("Satır 9" in caption.value for caption in app.caption)
    assert any("Kurumsal terim:** `T_PROJE` — satır 9" in markdown.value for markdown in app.markdown)
    assert [button.label for button in app.button] == [
        "Maskele ve çıktıya ekle", "Sorun yok, çıktıya ekle", "Çıktıya ekleme",
    ]


def test_equivalent_findings_are_grouped_with_one_pair_of_actions(monkeypatch):
    monkeypatch.setattr(review_page, "get_identity", lambda: {
        "project_name": "test", "sicil_no": "TEST-1", "branch_name": "main",
    })
    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [
        SimpleNamespace(id=1, file_path="src/a.py", line_number=3, found_value="PersonelSicilNo",
                        entity_type="INTERNAL_ID", confidence_level="orta", reason="kurum içi kimlik",
                        surrounding_context="x = PersonelSicilNo"),
        SimpleNamespace(id=2, file_path="src/b.py", line_number=8, found_value="personelsicilno",
                        entity_type="INTERNAL_ID", confidence_level="orta", reason="kurum içi kimlik",
                        surrounding_context="y = personelsicilno"),
    ])
    app = _review_app().run()
    assert not app.exception
    assert any("2 dosya / 2 kullanım" in caption.value for caption in app.caption)
    assert [button.label for button in app.button] == ["Tümünü Gizle", "src/a.py — Maskele", "src/b.py — Maskele", "Yanlış Alarm"]
    assert len(app.get("popover")) == 1


def test_automatic_file_action_uses_backend_without_text_editor(monkeypatch):
    monkeypatch.setattr(review_page, "get_identity", lambda: {
        "project_name": "test", "sicil_no": "T", "branch_name": "main",
    })
    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [
        SimpleNamespace(id=9, run_id=42, file_path=".env", line_number=1,
                        found_value="SyntheticSecret", entity_type="SECRET", confidence_level="orta",
                        reason="risk", surrounding_context="TOKEN=SyntheticSecret"),
    ])
    calls = []
    def mask_file(review_id):
        calls.append(review_id)
        return SimpleNamespace(written=True, message="Dosya sistem tarafından maskelendi ve çıktıya eklendi.")
    monkeypatch.setattr(review_page.api_client, "mask_review_file", mask_file)
    app = _review_app().run()
    app.button(key="mask_file_0_9").click().run()
    assert not app.exception
    assert calls == [9]
    assert not app.text_input and not app.text_area
    assert any("çıktıya eklendi" in item.value for item in app.success)


def test_syntax_failure_shows_exact_location_and_can_be_added_to_output(monkeypatch):
    monkeypatch.setattr(review_page, "get_identity", lambda: {
        "project_name": "test", "sicil_no": "TEST-1", "branch_name": "test",
    })
    syntax = SimpleNamespace(
        id=31, run_id=47, file_path="araclar/veri_uret.py", audit_failed=True, failed_check="sozdizimi",
        reasoning="Sözdizimi doğrulaması başarısız: Python sozdizimi hatasi (satir 31, sutun 70)",
        summary="Sözdizimi: maskeleme sonrası dosya Python kurallarına uymuyor.",
        location="Maskelenmiş dosyada satır 31, sütun 70. Bu satırda `mask_date_time_1` yer tutucusu "
                 "`islemler=` değerinin yerine konmuş.",
        next_step="Projeyi yeniden dışa aktarmak sonucu değiştirmez.",
        evidence=[SimpleNamespace(line=31, column=70, found_value="islemler=", label="Sözdizimini bozan maskeleme",
                                  excerpt="kartlar=[]; ⟦mask_date_time_1⟧[]; sayac=0")],
    )
    other = SimpleNamespace(
        id=32, run_id=47, file_path="lib/App.class", audit_failed=True, failed_check="sozdizimi",
        reasoning="ClassFormatError", summary="Sözdizimi: maskeleme dosyanın yapısını bozdu.",
        location="Dosya geneli.", next_step="Bildirin.", evidence=[],
    )
    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings", lambda **_: [syntax, other])
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    accepted = []
    monkeypatch.setattr(review_page.api_client, "accept_syntax_audit_warning", accepted.append)

    app = _review_app().run()

    assert not app.exception
    table = app.dataframe[0].value
    assert "Konum" in table.columns and "satır 31, sütun 70" in table["Konum"][0]
    assert any("`islemler=`" in m.value for m in app.markdown)
    assert any("⟦mask_date_time_1⟧" in code.value for code in app.code)
    # Java .class bozuk haliyle yeniden kurulamaz: secenek yalnizca .py dosyasinda.
    assert [b.key for b in app.button if b.key.startswith("audit_accept_syntax_")] == ["audit_accept_syntax_31"]

    app.button(key="audit_accept_syntax_31").click().run()

    assert not app.exception
    assert accepted == [31]


def test_file_opened_from_export_result_is_first_and_expanded(monkeypatch):
    monkeypatch.setattr(review_page, "get_identity", lambda: {
        "project_name": "test", "sicil_no": "TEST-1", "branch_name": "test",
    })

    def warning(id_, path):
        return SimpleNamespace(
            id=id_, run_id=49, file_path=path, audit_failed=False, failed_check="llm_denetimi",
            reasoning="risk", summary="Açık kalan bilgi.", location="Satır 1", next_step="Karar verin.",
            evidence=[],
        )

    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings",
                        lambda **_: [warning(1, "a/ilk.md"), warning(2, "veri/Profiller.json")])
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    app = _review_app()
    app.session_state["review_focus"] = {"run_id": 49, "file_path": "veri/Profiller.json"}
    app.run()

    assert not app.exception
    labels = [(e.label, e.proto.expanded) for e in app.expander if "İncele ve karar ver" in e.label]
    assert labels == [("📄 Profiller.json — İncele ve karar ver", True), ("📄 ilk.md — İncele ve karar ver", False)]


def _quarantined_file(monkeypatch, pending):
    monkeypatch.setattr(review_page, "get_identity", lambda: {"project_name": "p", "sicil_no": "S", "branch_name": "b"})
    monkeypatch.setattr(review_page.api_client, "list_pending_audit_warnings", lambda **_: list(pending))
    monkeypatch.setattr(review_page.api_client, "list_pending_reviews", lambda **_: [])
    monkeypatch.setattr(review_page.api_client, "list_runs", lambda **_: [])
    pending.append(SimpleNamespace(
        id=32, run_id=49, file_path="veri/a.json", audit_failed=False, failed_check="llm_denetimi",
        reasoning="ad (ilgili bolum: 'Ankara Şubesi')", summary="Maskelenmemiş şube adı.", location="Satır 3",
        next_step="", evidence=[SimpleNamespace(line=3, column=1, found_value="Ankara Şubesi", excerpt="e", label="Kurum")],
    ))


def test_failed_mask_explains_why_inside_the_files_box(monkeypatch):
    # Hata panelin basinda gosterilirse kullanici (asagidaki dosya kutusunda)
    # gormez ve "hicbir sey olmadi" sanip butona tekrar tekrar basar.
    pending = []
    _quarantined_file(monkeypatch, pending)

    def fail(warning_id):
        raise ApiError("Otomatik maskeleme sonrası dosya çıktıya eklenmedi: Riskli ifade mevcut bir yer tutucuyla çakışıyor", None, 400)
    monkeypatch.setattr(review_page.api_client, "mask_audit_warning", fail)
    app = _review_app().run()
    app.button(key="audit_mask_32").click().run()
    assert not app.exception
    box = app.expander[0]
    assert any("yer tutucuyla çakışıyor" in w.value and "Ne yapmalı" in w.value for w in box.warning)
    assert [toast.value for toast in app.toast] == ["Dosya çıktıya eklenmedi; sebebi dosyanın kutusunda yazıyor."]


def test_successful_mask_is_announced(monkeypatch):
    pending = []
    _quarantined_file(monkeypatch, pending)
    monkeypatch.setattr(review_page.api_client, "mask_audit_warning", lambda warning_id: pending.clear())
    app = _review_app().run()
    app.button(key="audit_mask_32").click().run()
    assert not app.exception
    assert any("dosya çıktıya eklendi" in s.value for s in app.success)
    assert [toast.value for toast in app.toast] == ["İşlem tamamlandı."]


def test_new_final_findings_after_mask_are_shown_with_their_values(monkeypatch):
    # Karar uygulandi, son kontrol yeni bilgi buldu: "ayni butona basmak ayni
    # sonucu verir" denmez; yeni deger ve ne yapilacagi dosyanin kutusunda yazar.
    pending = []
    _quarantined_file(monkeypatch, pending)

    def updated(warning_id):
        pending[0].summary = "Seçtiğiniz 1 değer maskelendi, ancak son kontrol dosyada yeni bilgi buldu."
        pending[0].evidence = [
            SimpleNamespace(line=2, column=1, found_value="AKB-M-0001", excerpt="e", label="Kişi adı"),
            SimpleNamespace(line=9, column=1, found_value="AKB-M-0002", excerpt="e", label="Kişi adı"),
        ]
        raise ApiError("Son kontrol dosyada yeni bilgi buldu (kişi adı: 72 değer). Seçtiğiniz 1 değer maskelendi, "
                       "ancak dosya henüz çıktıya eklenmedi.", None, 400, code="audit_findings_updated")
    monkeypatch.setattr(review_page.api_client, "mask_audit_warning", updated)
    app = _review_app().run()
    app.button(key="audit_mask_32").click().run()

    assert not app.exception
    box = app.expander[0]
    assert box.proto.expanded
    [message] = [w.value for w in box.warning]
    assert "kişi adı: 72 değer" in message and "**Kişi adı:** `AKB-M-0001` …" in message
    assert "Önceki kararınız kaydedildi" in message and "Aynı butona tekrar basmak" not in message
    assert [toast.value for toast in app.toast] == ["Kararınız uygulandı; son kontrol yeni bilgi buldu."]
