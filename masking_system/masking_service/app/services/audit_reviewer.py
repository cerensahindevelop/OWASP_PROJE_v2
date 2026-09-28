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
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.llm_recognizer import LLMRecognitionError, call_vllm, chunk_text, require_complete_response
from app.services.llm_runtime import LLMScanMetrics
from app.services.rule_engine import JSON_NUMERIC_PLACEHOLDER_RE, PLACEHOLDER_RE

logger = logging.getLogger("uvicorn.error.llm")

# Bir denetim bulgusunun gercek bir sizinti sayilabilmesi icin, yer
# tutucular cikarildiktan sonra en az bu kadar harf/rakam icermesi gerekir.
_CONTENT_RE = re.compile(r"[^\W_]{2,}")
_SEGMENT_STRIP = " \t\r\n:,;=\"'`()[]{}<>/\\#*-"
# Modelin sik sik "risk" diye isaretledigi ama tek basina hicbir kurumu/kisiyi
# tanimlamayan dil anahtar kelimeleri ve teknik sabitler. Bir bulgunun TUM
# kelimeleri bu kumedeyse bulgu yok sayilir.
_GENERIC_TOKENS = frozenset("""
public private protected static final class interface enum extends implements import package return
void int long string boolean true false null none self this new def function const let var async await
if else for while try catch except finally raise throw throws select from where insert update delete
localhost example com org net http https www api v1 v2 id ids name names value values key keys todo fixme
""".split())

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


def _placeholder_free_segments(quote: str) -> list[str]:
    parts = PLACEHOLDER_RE.split(quote)
    segments: list[str] = []
    for part in parts:
        for piece in JSON_NUMERIC_PLACEHOLDER_RE.split(part):
            piece = piece.strip(_SEGMENT_STRIP)
            if piece:
                segments.append(piece)
    return segments


def _is_substantive(segment: str) -> bool:
    words = _CONTENT_RE.findall(segment)
    if not words:
        return False
    return any(word.casefold() not in _GENERIC_TOKENS for word in words)


def verify_audit_findings(text: str, findings: list[AuditFinding]) -> tuple[list[AuditFinding], int]:
    """Keep only findings whose cited clear-text value really exists in `text`.

    Tespit katmanindaki ilkenin aynisi: modelin soyledigine degil, metinde
    birebir dogrulanabilen alintiya guvenilir. Yer tutucular alintidan
    cikarilir (onlar zaten guvenli); geriye anlamli, metinde gecen bir parca
    kalmazsa bulgu "somut sizinti" sayilmaz. Donus: (dogrulanan, atilan_sayisi).
    """
    verified: list[AuditFinding] = []
    dropped = 0
    for finding in findings:
        quote = finding.ilgili_bolum or ""
        kept = [
            segment for segment in _placeholder_free_segments(quote)
            if segment in text and _is_substantive(segment)
        ]
        if not kept:
            dropped += 1
            continue
        for segment in kept:
            verified.append(AuditFinding(aciklama=finding.aciklama, ilgili_bolum=segment))
    return verified, dropped


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
            verified, dropped = verify_audit_findings(chunk, verdict.findings)
            if dropped or (verdict.risky and not verified):
                # Icerik degil, sadece sayilar loglanir.
                logger.info(
                    "llm_audit_unverified file=%r chunk=%d model_risky=%s dropped_findings=%d kept_findings=%d",
                    metrics.file_path, index, verdict.risky, dropped, len(verified),
                )
            # Risk yalnizca metinde dogrulanan somut bir alintiya dayanir;
            # modelin "risk var" deyip dogrulanabilir alinti vermemesi
            # dosyayi karantinaya almaz.
            risky = risky or bool(verified)
            for finding in verified:
                # Same cited section across overlapping chunks is one audit finding.
                findings.setdefault(finding.ilgili_bolum or finding.aciklama, finding)
    return AuditVerdict(risky=risky, findings=list(findings.values()))
