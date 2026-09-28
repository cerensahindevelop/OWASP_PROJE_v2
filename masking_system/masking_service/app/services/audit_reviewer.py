"""Katman 3'teki (tespit) LLM detector'undan TAMAMEN BAGIMSIZ, maskeleme
TAMAMLANDIKTAN SONRA calisan ikinci bir LLM katmani. Gorevi tespit degil
DENETIM: "Bu maskelenmis ciktida hala orijinal veriyi cagristiran bir sey
var mi?" sorusunu, kotu niyetli/adversarial bir bakis acisiyla sorar.

Ayni vLLM altyapisini (call_vllm, kill-switch) llm_recognizer.py ile
paylasir - o modul zaten bu HTTP/timeout/JSON-semasi mekanigini dogru
kurmustu, tekrar yazmaya gerek yok. Ayrilan tek sey PROMPT/ROL ve
DEGERLENDIRME SORUSU: llm_recognizer.py "bu metinde hassas deger var mi"
diye sorar, bu modul ise "BU DOSYA ZATEN MASKELENMIS, yine de bir ipucu
kalmis mi" diye sorar - farkli bir soru, farkli bir sonuc semasi.

Guvenlik ilkesi (asla degistirilmemeli): vLLM'e ulasilamazsa/zaman
asimina ugrarsa/bozuk JSON donerse, bu SESSIZCE "risk yok" olarak
yorumlanmaz - caller (exporter.py) bunu audit_failed=True olarak
isaretleyip dosyayi yine de karantinaya almalidir (fail-safe).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.services.llm_recognizer import LLMRecognitionError, call_vllm, chunk_text, require_complete_response
from app.services.llm_runtime import LLMScanMetrics

_AUDIT_PROMPT_PATH = Path(__file__).with_name("audit_prompt.txt")

_AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_var": {"type": "boolean"},
        "bulgular": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "aciklama": {"type": "string"},
                    "ilgili_bolum": {"type": "string"},
                },
                "required": ["aciklama", "ilgili_bolum"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["risk_var", "bulgular"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AuditFinding:
    aciklama: str
    ilgili_bolum: str


@dataclass(frozen=True)
class AuditVerdict:
    risky: bool
    findings: list[AuditFinding] = field(default_factory=list)

    # Bulgulari insan-okunur tek bir metne birlestirir.
    def reasoning_text(self) -> str:
        if not self.findings:
            return "Model risk oldugunu belirtti ama detay vermedi."
        return " | ".join(f"{f.aciklama} (ilgili bolum: '{f.ilgili_bolum}')" for f in self.findings)


# Denetim icin kullanilacak sistem promptunu dosyadan okur.
def load_audit_prompt() -> str:
    try:
        return _AUDIT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LLMRecognitionError(f"Denetim prompt dosyasi okunamadi ({_AUDIT_PROMPT_PATH}): {exc}") from exc


# vLLM'e gonderilecek denetim istegini (prompt + maskelenmis metin + sema) hazirlar.
def build_audit_request(
    masked_text: str, model: str, seed: int, max_tokens: int = 512, disable_thinking: bool = False,
) -> dict:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "seed": seed,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "denetim_semasi", "schema": _AUDIT_SCHEMA, "strict": True},
        },
        "messages": [
            {"role": "system", "content": load_audit_prompt()},
            {"role": "user", "content": masked_text},
        ],
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


# vLLM'in ham JSON yanitini AuditVerdict nesnesine cevirir; bozuk yanitta hata firlatir.
def parse_audit_response(raw_response: dict) -> AuditVerdict:
    require_complete_response(raw_response)
    try:
        content = raw_response["choices"][0]["message"]["content"]
        parsed: dict = json.loads(content)
        risky = parsed["risk_var"]
        findings_raw = parsed["bulgular"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMRecognitionError(f"Denetim yaniti beklenen sekilde degil: {exc}") from exc

    if not isinstance(risky, bool) or not isinstance(findings_raw, list):
        raise LLMRecognitionError("Denetim yaniti beklenen sekilde degil (alan tipi hatasi)")

    findings: list[AuditFinding] = []
    for item in findings_raw:
        if not isinstance(item, dict):
            continue
        aciklama = item.get("aciklama")
        ilgili_bolum = item.get("ilgili_bolum")
        if isinstance(aciklama, str) and aciklama and isinstance(ilgili_bolum, str):
            findings.append(AuditFinding(aciklama=aciklama, ilgili_bolum=ilgili_bolum))

    return AuditVerdict(risky=risky, findings=findings)


# Maskelenmis metni LLM ile denetler ("hala bir ipucu kalmis mi?"). LLM kapaliysa risksiz sayar.
async def audit_masked_text(masked_text: str, vllm_settings) -> AuditVerdict:
    if not vllm_settings.enabled:
        return AuditVerdict(risky=False)
    if not vllm_settings.host or not vllm_settings.model:
        raise LLMRecognitionError("VLLM_ENABLED=true iken VLLM_HOST ve VLLM_MODEL zorunludur")
    chunks = chunk_text(masked_text, vllm_settings.max_file_chars,
                        getattr(vllm_settings, "chunk_overlap_chars", 500))
    risky = False
    findings: dict[str, AuditFinding] = {}
    with LLMScanMetrics("audit", len(chunks)) as metrics:
        for index, (_, chunk) in enumerate(chunks, 1):
            payload = build_audit_request(
                chunk, vllm_settings.model, getattr(vllm_settings, "seed", 42),
                max_tokens=getattr(vllm_settings, "max_tokens", 512),
                disable_thinking=getattr(vllm_settings, "disable_thinking", False),
            )
            verdict = await metrics.request(vllm_settings, payload, call_vllm, parse_audit_response, index)
            risky = risky or verdict.risky
            for finding in verdict.findings:
                # Same cited section across overlapping chunks is one audit finding.
                findings.setdefault(finding.ilgili_bolum or finding.aciklama, finding)
    return AuditVerdict(risky=risky, findings=list(findings.values()))
