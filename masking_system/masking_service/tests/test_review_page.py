from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from app.webapp import review_page

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
