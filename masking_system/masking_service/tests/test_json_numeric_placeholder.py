"""JSON tip koruma ozelligi: tirnaksiz (bare) bir JSON sayi konumunda
maskelenen bir deger, harf-tabanli placeholder yerine gecerli bir JSON
number olarak temsil edilmeli - aksi halde dosya gecersiz JSON'a doner.

Gercek raporlanan hata: bir sicil numarasi (orn. 9500205) projenin baska
bir dosyasinda metin/tirnakli baglamda hassas olarak tespit edilip harf-
tabanli bir mapping aliyordu; ayni deger daha sonra bir JSON dosyasinda
tirnaksiz bir sayi olarak gectiginde, consistency-pass o AYNI (yanlis
sekilli) mapping'i buraya da uygulamaya calisiyor, sonuc dosyayi
gecersiz JSON'a cevirip export'u basarisiz kiliyordu (bkz.
mapping_service.get_or_create_mapping - artik `numeric` bayragina gore
AYRI bir mapping/placeholder BICIMI kullanir).
"""

from __future__ import annotations

import asyncio
import json

from app.services.exporter import export_project
from app.services.term_upload import commit_term_upload
from app.services.unmasker import unmask_project


def test_bare_json_number_masked_as_valid_json_number_and_round_trips(db_session, tmp_path, monkeypatch):
    """Katman 1/2/3'un TAM olarak hangi tespiti yapacagina (orn. Presidio'nun
    olasiliksal heuristigine) baglanmadan, "bir sayi tirnaksiz JSON konumunda
    tespit edildi" durumunu deterministik/kontrollu uretmek icin orchestrator
    sahte (fake) bir tanesiyle degistirilir - gercek DB'ye kayitli bir kural
    (rule_id FK gecerliligi icin) kullanilir, sadece HANGI span'in eslesecegi
    kontrol edilir."""
    from app.services import exporter
    from app.services.detectors import DetectionResult, DetectorOutput
    from app.services.mapping_service import load_active_rules

    commit_term_upload(
        db_session, filename="terms.txt", content=b"jsonnumq1a2b\n", category="pytest_json_numeric_direct"
    )
    rule = next(r for r in load_active_rules(db_session) if r.category == "pytest_json_numeric_direct")

    source = tmp_path / "src"
    source.mkdir()
    original_text = '{\n  "aktif": true,\n  "kayitNumarasi": 1234567\n}\n'
    (source / "config.json").write_text(original_text, encoding="utf-8")

    class FakeOrchestrator:
        async def scan(self, text, metadata=None):
            start = text.index("1234567")
            return DetectorOutput(results=[
                DetectionResult(
                    deger="1234567", tip="KURUMSAL_TERIM", guven_seviyesi="yuksek",
                    kaynak_motor="dictionary", start=start, end=start + len("1234567"), rule=rule,
                )
            ])

    monkeypatch.setattr(exporter, "build_orchestrator", lambda *args, **kwargs: FakeOrchestrator())

    target = tmp_path / "masked"
    report = asyncio.run(
        export_project(
            db_session, source_path=str(source), project_name="pytest-json-numeric-direct",
            sicil_no="P-JSONNUM-1", branch_name="pytest-branch", target_path=str(target),
            initiated_by="P-JSONNUM-1",
        )
    )
    assert report.files_quarantined_pending_audit == 0
    assert report.files_failed_syntax_validation == 0

    masked_text = (target / "config.json").read_text(encoding="utf-8")
    parsed = json.loads(masked_text)  # gecersizse burada patlar
    assert "1234567" not in masked_text
    assert isinstance(parsed["aktif"], bool)
    assert isinstance(parsed["kayitNumarasi"], int)  # string DEGIL - tip korunmus

    restored = tmp_path / "restored"
    unmask_report = unmask_project(
        db_session, source_path=str(target), project_name="pytest-json-numeric-direct",
        sicil_no="P-JSONNUM-1", branch_name="pytest-branch", target_path=str(restored),
        initiated_by="P-JSONNUM-1",
    )
    assert (restored / "config.json").read_text(encoding="utf-8") == original_text
    assert not unmask_report.has_unresolved_placeholders


def _llm_finds_number_only_in_notes(monkeypatch):
    from app.services import audit_reviewer, exporter, llm_recognizer

    async def fake(host, timeout, payload, api_key=None):
        if payload["response_format"]["json_schema"]["name"] == "denetim_semasi":
            data = {"risk_var": False, "bulgular": []}
        elif "personel jsonnumq9z8y" in payload["messages"][1]["content"]:
            data = {"bulgular": [{"bulunan_deger": "7650321", "tip": "KIMLIK_NO",
                                  "guven_seviyesi": "yuksek", "gerekce": "personel numarasi"}]}
        else:
            data = {"bulgular": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}

    vllm = exporter.settings.vllm
    monkeypatch.setattr(vllm, "enabled", True)
    monkeypatch.setattr(vllm, "host", "http://fake-llm")
    monkeypatch.setattr(vllm, "model", "fake")
    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)
    monkeypatch.setattr(audit_reviewer, "call_vllm", fake)


def test_consistency_pass_masks_value_already_mapped_as_text_elsewhere(db_session, tmp_path, monkeypatch):
    """Ayni deger BIR dosyada metin/tirnakli baglamda (once tespit edilip
    harf-tabanli bir mapping alir), BASKA bir JSON dosyasinda tirnaksiz bir
    sayi olarak gecer - consistency-pass bu ikinci konum icin AYRI, sayisal
    bir mapping kullanmali; var olan harf-tabanli mapping'i oraya
    uygulamamali (uygularsa gecersiz JSON uretir)."""
    commit_term_upload(
        db_session, filename="terms.txt", content=b"jsonnumq9z8y\n", category="pytest_json_numeric_conflict"
    )

    source = tmp_path / "src"
    source.mkdir()
    # Ilk dosya: deger metin baglaminda - ilk turda BURADA tespit edilip
    # harf-tabanli (PREFIX_TEST_N) bir mapping alir.
    # Deger yalnizca notes.txt'de, baglamindan (yuksek guvenli LLM bulgusu
    # olarak) tespit edilir. Tutarlilik registry'si zayif kaynaklari (orn.
    # Presidio US_DRIVER_LICENSE) yaymaz; uzun sayisal kimlikleri yuksek
    # guvenli LLM'den kabul eder.
    _llm_finds_number_only_in_notes(monkeypatch)
    (source / "notes.txt").write_text("personel jsonnumq9z8y numarasi: 7650321\n", encoding="utf-8")
    # Ikinci dosya: AYNI sayi (7650321), JSON'da TIRNAKSIZ - bu dosyanin
    # kendisinde "jsonnumq9z8y" kelimesi hic gecmiyor, bu deger ancak
    # consistency-pass'in "baska yerde kayitli hassas deger" taramasiyla
    # yakalanabilir.
    config_dir = source / "config"
    config_dir.mkdir()
    # NOT: anahtar adi kasitli "kayitNumarasi" - gercek gelistirme
    # veritabaninda "sicil"/"kurum" gibi aktif kurumsal terimler mevcut
    # olabilir (bkz. bu oturumdaki onceki bulgu); anahtar adinin onlarla
    # tesadufen cakisip AYRICA maskelenmesini (ve testi kirilgan hale
    # getirmesini) onlemek icin notr bir isim secildi.
    original_json = '{\n  "birimAdi": "Bilgi Sistemleri",\n  "kayitNumarasi": 7650321,\n  "aktif": true\n}\n'
    (config_dir / "app.json").write_text(original_json, encoding="utf-8")

    target = tmp_path / "masked"
    report = asyncio.run(
        export_project(
            db_session, source_path=str(source), project_name="pytest-json-numeric-conflict",
            sicil_no="P-JSONNUM-2", branch_name="pytest-branch", target_path=str(target),
            initiated_by="P-JSONNUM-2",
        )
    )
    assert report.files_quarantined_pending_audit == 0
    assert report.files_failed_consistency_validation == 0
    assert (target / "config" / "app.json").is_file()

    masked_json_text = (target / "config" / "app.json").read_text(encoding="utf-8")
    parsed = json.loads(masked_json_text)  # gecersizse burada patlar (bkz. orijinal bug raporu)
    assert "7650321" not in masked_json_text
    assert isinstance(parsed["kayitNumarasi"], int)

    restored = tmp_path / "restored"
    unmask_report = unmask_project(
        db_session, source_path=str(target), project_name="pytest-json-numeric-conflict",
        sicil_no="P-JSONNUM-2", branch_name="pytest-branch", target_path=str(restored),
        initiated_by="P-JSONNUM-2",
    )
    assert (restored / "config" / "app.json").read_text(encoding="utf-8") == original_json
    assert (restored / "notes.txt").read_text(encoding="utf-8") == "personel jsonnumq9z8y numarasi: 7650321\n"
    assert not unmask_report.has_unresolved_placeholders
