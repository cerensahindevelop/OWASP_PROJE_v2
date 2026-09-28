"""Genel guvenilirlik duzeltmesinin (bkz. app/services/file_classifier.py,
app/services/file_type.py, app/services/exporter.py) regresyon testleri.

Iddia: AYNI hassas/kuruma ozgu deger, bulundugu dosya turu (Java/Python/
JavaScript kaynak kodu, JSON/XML/YAML, Markdown, .properties/config) veya
encoding (UTF-8, UTF-16, BOM'lu/BOM'suz, Turkce unicode karakterleri)
degistigi icin KACIRILMAMALI. Kullanilan degerler (sirket adi, ic servis
kod adi) BILEREK daha once kodda/sozlukte hic gecmemis, uydurma/yeni
degerlerdir - amac ezberlenmis ozel kelimeleri degil, genel pipeline
davranisini kanitlamaktir (bkz. gorev tanimindaki 8. ilke).

LLM katmani gercek bir vLLM sunucusuna baglanmadan test edilir:
`app.services.llm_recognizer.call_vllm` sahte bir yanitla degistirilir
(ayni yontem tests/test_llm_detector.py'de kullanilir) - ama asil dogrulama
(deger metinde birebir geciyor mu, span dogru mu, overlap/boundary/
encoding katmanlari) TAMAMEN gercek kodla calisir.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import settings
from app.services.file_pipeline import ReadStatus, read_scanned_file
from app.services.file_type import write_text_preserving_encoding
from app.services.llm_detector import LLMDetector
from app.services.mapping_service import build_orchestrator, detect_matches
from app.services.scanner import ScannedFile

# Daha once kodda/sozlukte hic gecmemis, uydurma degerler.
VENDOR = "Vardanis Endüstri A.Ş."
CODENAME = "NovaQuasarInternal"

_TARGET_ENTITY_TYPES = {VENDOR: "KURUM_ADI", CODENAME: "IC_SERVIS_ADI"}


def _fake_settings():
    from types import SimpleNamespace

    return SimpleNamespace(
        enabled=True,
        host="http://fake-vllm.local",
        model="fake-model",
        api_key=None,
        timeout_seconds=5.0,
        max_file_chars=50_000,
        max_chunk_chars=50_000,
        seed=42,
        max_concurrent_requests=4,
    )


async def _fake_call_vllm(host, timeout_seconds, payload, api_key=None):
    """Gercek vLLM sunucusu yerine gecen sahte cagri: gonderilen parca
    metninde bilinen hedef degerlerden hangisi GERCEKTEN geciyorsa (harf
    harf) onu bildirir - modelin kendisini degil, sadece agi/HTTP'yi
    taklit eder. Asil dogrulama (parse_and_verify_detections) gercek koddur."""
    chunk_text = payload["messages"][1]["content"]
    bulgular = [
        {
            "bulunan_deger": value,
            "tip": entity_type,
            "guven_seviyesi": "yuksek",
            "gerekce": "test mock",
        }
        for value, entity_type in _TARGET_ENTITY_TYPES.items()
        if value in chunk_text
    ]
    return {"choices": [{"message": {"content": json.dumps({"bulgular": bulgular})}}]}


def _build_llm_only_orchestrator(monkeypatch):
    """RuleBasedDetector/PresidioDetector KASITLI olarak devre disi
    birakilmiyor - gercek build_orchestrator() cagrilir (bkz. asagida),
    ama bu testin iddiasi SADECE LLM katmaninin (Presidio/sozlugun
    yakalamayacagi uydurma kurum/servis adlari icin) her dosya turunde/
    encoding'de calistigidir; digerlerinin bir seyi YANLISLIKLA
    yakalamamasi/engellememesi de testin gecerliligi icin gereklidir."""
    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call_vllm)
    # build_orchestrator() LLMDetector'i DAIMA global settings.vllm ile
    # kurar (parametre olarak verilemez) - testte gercek bir vLLM sunucusu
    # olmadan bu katmanin devreye girmesi icin kill-switch'i (VLLM_ENABLED
    # varsayilani false - bkz. app/core/config.py) test suresince acmamiz gerekir.
    monkeypatch.setattr(settings.vllm, "enabled", True)
    monkeypatch.setattr(settings.vllm, "host", "http://fake-vllm.local")
    monkeypatch.setattr(settings.vllm, "model", "fake-model")
    return build_orchestrator(rules=[], runtime_params={}, presidio_rules=[], category_restrictions={})


def _assert_both_targets_detected(matches, sample_label: str) -> None:
    found_values = {m.original_value for m in matches}
    assert VENDOR in found_values, f"[{sample_label}] VENDOR kacirildi: {found_values}"
    assert CODENAME in found_values, f"[{sample_label}] CODENAME kacirildi: {found_values}"


# --------------------------------------------------------------------------
# Ayni dosya turu matrisi - Java/Python/JavaScript/JSON/XML/YAML/Markdown/
# properties. Her biri hedef degerleri farkli bir sozdizimsel rolde
# (yorum/string/yapilandirilmis-veri-degeri) tasir.
# --------------------------------------------------------------------------

_SAMPLES: dict[str, str] = {
    "java": (
        "package com.example.internal;\n\n"
        f"// TODO: Zeus modulunu {CODENAME} projesine tasi\n"
        "public class InternalConfig {\n"
        f'    private static final String VENDOR_NAME = "{VENDOR}";\n'
        f'    private static final String SERVICE_CODENAME = "{CODENAME}";\n'
        "}\n"
    ),
    "python": (
        f"# Vendor sozlesmesi: {VENDOR}\n"
        f"# internal codename: {CODENAME}\n"
        f'VENDOR_NAME = "{VENDOR}"\n'
        f'SERVICE_CODENAME = "{CODENAME}"\n'
    ),
    "javascript": (
        f"// vendor: {VENDOR}\n"
        f"const SERVICE_CODENAME = '{CODENAME}';\n"
        f"const VENDOR_NAME = '{VENDOR}';\n"
        "module.exports = { SERVICE_CODENAME, VENDOR_NAME };\n"
    ),
    "json": json.dumps({"vendor": VENDOR, "internalService": CODENAME}, ensure_ascii=False) + "\n",
    "xml": (
        "<config>\n"
        f"  <vendor>{VENDOR}</vendor>\n"
        f'  <service name="{CODENAME}"/>\n'
        "</config>\n"
    ),
    "yaml": f"vendor: \"{VENDOR}\"\ninternal_service: \"{CODENAME}\"\n",
    "markdown": (
        "# Internal Notes\n\n"
        f"Vendor: {VENDOR}. Service codename: {CODENAME}.\n"
    ),
    "properties": f"vendor.name={VENDOR}\ninternal.service={CODENAME}\n",
}


@pytest.mark.parametrize("file_shape", sorted(_SAMPLES))
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_same_value_detected_across_file_type_and_encoding(monkeypatch, tmp_path, file_shape, encoding):
    content = _SAMPLES[file_shape]

    # 1) Gercek dosya sistemi round-trip: yaz -> tara -> oku. Encoding
    #    farki burada devreye girer (bkz. app/services/file_type.py) -
    #    UTF-16 gibi NUL-agirlikli encoding'ler eskiden bu adimda sessizce
    #    binary sanilip TEXT_READY'e hic ulasmazdi.
    src = tmp_path / f"sample.{file_shape}"
    src.write_bytes(content.encode(encoding))
    scanned = ScannedFile(absolute_path=src, relative_path=src.relative_to(tmp_path), is_symlink=False)
    read_outcome = read_scanned_file(scanned, tmp_path / "dst" / src.name, max_inline_size=1 << 20)

    assert read_outcome.status == ReadStatus.TEXT_READY, (
        f"[{file_shape}/{encoding}] dosya taranmadan binary/undecodable sayildi: {read_outcome.status}"
    )
    assert read_outcome.text == content

    # 3) Tespit: LLM katmani (sahte ag cagrisiyla) her iki uydurma degeri de
    #    bulmali - kaynak kod/yapilandirilmis-veri/dogal-dil metni farketmeksizin.
    orchestrator = _build_llm_only_orchestrator(monkeypatch)
    metadata = {
        "file_path": src.name,
    }
    outcome = asyncio.run(detect_matches(orchestrator, read_outcome.text, metadata))

    _assert_both_targets_detected(outcome.matches, f"{file_shape}/{encoding}")


def test_bare_identifier_context_is_detected():
    """Principle 4: LLM sadece dogal cumlelerde degil, class/variable/
    identifier baglaminda da (tirnaksiz, bare kod ifadesi olarak) kuruma
    ozgu degerleri degerlendirmeli. TokenBoundaryValidator'in bare-kod
    reddini (attribute erisimi/fonksiyon cagrisi) TETIKLEMEYECEK sekilde
    kurgulandi (deger ne "." ile ne de "(" ile bitisik)."""
    content = f"const {CODENAME} = require('./internal-service');\n"

    async def _fake_call(host, timeout_seconds, payload, api_key=None):
        chunk_text = payload["messages"][1]["content"]
        bulgular = []
        if CODENAME in chunk_text:
            bulgular.append(
                {"bulunan_deger": CODENAME, "tip": "IC_SERVIS_ADI", "guven_seviyesi": "yuksek", "gerekce": "test mock"}
            )
        return {"choices": [{"message": {"content": json.dumps({"bulgular": bulgular})}}]}

    detector = LLMDetector(_fake_settings())
    import app.services.llm_recognizer as llm_recognizer_module

    orig = llm_recognizer_module.call_vllm
    llm_recognizer_module.call_vllm = _fake_call
    try:
        output = asyncio.run(detector.detect(content, metadata={"file_path": "internal.js"}))
    finally:
        llm_recognizer_module.call_vllm = orig

    values = {r.deger for r in output.results}
    assert CODENAME in values


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32"],
)
def test_turkish_unicode_value_survives_every_common_encoding_round_trip(tmp_path, encoding):
    """Farkli Unicode karakterleri (Turkce Ş/ı/İ/ğ) + farkli encodingler
    (BOM'lu/BOM'suz, LE/BE) icin dosya okuma/yazma katmaninin ayni
    normalize edilmis Python str'i urettigini kanitlar (bkz. ilke 2)."""
    text = f"sirket_unvani = \"{VENDOR}\"\n"
    src = tmp_path / "sirket.py"
    src.write_bytes(text.encode(encoding))
    scanned = ScannedFile(absolute_path=src, relative_path=src.relative_to(tmp_path), is_symlink=False)

    read_outcome = read_scanned_file(scanned, tmp_path / "dst" / "sirket.py", max_inline_size=1 << 20)

    assert read_outcome.status == ReadStatus.TEXT_READY, encoding
    assert read_outcome.text == text
    assert VENDOR in read_outcome.text

    # Yazma da (write_text_preserving_encoding) ayni degeri korumali - unmask
    # / export cikti dosyasi da orijinal Unicode karakterleri kaybetmemeli.
    dest = tmp_path / "roundtrip.py"
    used_encoding = write_text_preserving_encoding(dest, read_outcome.text, read_outcome.encoding)
    assert dest.read_text(encoding=used_encoding) == text
