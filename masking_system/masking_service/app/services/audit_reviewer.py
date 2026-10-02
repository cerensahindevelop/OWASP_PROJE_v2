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

from app.core.config import settings
from app.services.llm_recognizer import (
    LLMRecognitionError, call_vllm, chunk_text, describe_file_context, require_complete_response,
    run_chunk_scans, scan_with_split, with_file_context,
)
from app.services.encoded_blobs import find_encoded_blobs
from app.services.llm_input_view import build_llm_input_view
from app.services.llm_runtime import LLMScanMetrics, current_llm_file
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

# verify_audit_findings davranisi degistiginde artirilir (bkz. audit_record_key).
_VERIFIER_VERSION = 2

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
    # False: model hic cagrilmadi (LLM kapali ya da denetim atlandi). Boyle bir
    # sonuc kaydedilip yeniden kullanilmaz - denetimin yerini tutmaz.
    audited: bool = True

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
    presence_penalty: float = 0.0, file_context: str | None = None,
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
            {"role": "system", "content": with_file_context(load_audit_prompt(), file_context)},
            {"role": "user", "content": masked_text},
        ],
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if presence_penalty:
        payload["presence_penalty"] = presence_penalty
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


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Alinti "anahtar = deger" / "anahtar: deger" bicimindeyse hassas olan degerdir.
_KEY_VALUE_RE = re.compile(r"^[\"']?([A-Za-z_][\w.]*)[\"']?\s*(?:=(?![=>])|:)\s*[@$]?[\"']?(.+)$")
# Satir sonu atamasinda deger kod degil duz veri olmali (orn. .env, .properties).
_CODE_PUNCTUATION = frozenset("()[]{};")


def _clean_values(text: str, raw: str) -> list[str]:
    return [
        segment for segment in _placeholder_free_segments(raw)
        if segment in text and _is_substantive(segment)
    ]


def _identifier_assignments(text: str, name: str) -> list[re.Match] | None:
    """`name` kodda atanan/anahtar olarak kullanilan bir tanimlayiciysa atama
    eslesmelerini dondurur; tanimlayici olarak kullanilmiyorsa None."""
    if not _IDENTIFIER_RE.fullmatch(name):
        return None
    escaped = re.escape(name)
    assignments = list(re.finditer(
        rf"(?:(?<![\w\"'.]){escaped}[ \t]*=(?![=>])"            # anaMusteriAd = ...
        rf"|[\"']{escaped}[\"'][ \t]*:"                         # "anaMusteriAd": ...
        rf"|^[ \t]*(?:export[ \t]+)?{escaped}[ \t]*[=:](?!=))"  # anaMusteriAd: ... (YAML/.env)
        rf"[ \t]*(?:[@$]?([\"'])((?:\\.|(?!\1).)*)\1|([^\n]*))",
        text, re.MULTILINE,
    ))
    return assignments or None


def resolve_audit_values(text: str, quote: str) -> list[str]:
    """Modelin alintisini metinde ACIK duran somut degerlere cevirir.

    Hassas olan degisken/alan adi degil, ona atanan degerdir: model
    `anaMusteriAd` gosterirse metindeki `anaMusteriAd = "Ayse Yilmaz"`
    atamasindan `Ayse Yilmaz` dondurulur. Tanimlayiciya acik bir deger
    atanmamissa (bos, yer tutucu, calisma aninda okunan) bos liste doner -
    yalnizca bir ad, sizinti degildir.
    """
    values: list[str] = []
    for segment in _clean_values(text, quote):
        pair = _KEY_VALUE_RE.match(segment)
        if pair and _identifier_assignments(text, pair.group(1)) is not None:
            narrowed = _clean_values(text, pair.group(2))
            if narrowed:
                values.extend(narrowed)
                continue
        assignments = _identifier_assignments(text, segment)
        if assignments is None:
            values.append(segment)
            continue
        for match in assignments:
            literal, bare = match.group(2), match.group(3)
            if literal is not None:
                values.extend(_clean_values(text, literal))
            elif bare is not None:
                bare = bare.split(" #", 1)[0].split(" //", 1)[0].strip()
                # Baska bir degiskene/ifadeye atama (x = y; x = f()) deger degildir.
                if bare and not _CODE_PUNCTUATION.intersection(bare) \
                        and _identifier_assignments(text, bare) is None:
                    values.extend(_clean_values(text, bare))
    return list(dict.fromkeys(values))


def verify_audit_findings(text: str, findings: list[AuditFinding]) -> tuple[list[AuditFinding], int]:
    """Keep only findings whose cited clear-text value really exists in `text`.

    Tespit katmanindaki ilkenin aynisi: modelin soyledigine degil, metinde
    birebir dogrulanabilen alintiya guvenilir. Yer tutucular alintidan
    cikarilir (onlar zaten guvenli); degisken adlari atanan degere cevrilir
    (bkz. resolve_audit_values). Geriye anlamli, metinde gecen bir deger
    kalmazsa bulgu "somut sizinti" sayilmaz. Donus: (dogrulanan, atilan_sayisi).
    """
    verified: list[AuditFinding] = []
    dropped = 0
    for finding in findings:
        kept = resolve_audit_values(text, finding.ilgili_bolum or "")
        if not kept:
            dropped += 1
            continue
        for value in kept:
            verified.append(AuditFinding(aciklama=finding.aciklama, ilgili_bolum=value))
    return verified, dropped


# Maskelenmis metni LLM ile denetler ("hala bir ipucu kalmis mi?"). LLM kapaliysa risksiz sayar.
# Chunk'lar tespit adimiyla ayni sekilde es zamanli denetlenir (toplam sinir:
# llm_runtime._gate); ilk hatada kalan chunk'lar iptal edilir ve dosya karantinaya gider.
async def audit_masked_text(
    masked_text: str, vllm_settings, file_path: str | None = None, blob_min_chars: int | None = None,
) -> AuditVerdict:
    if not vllm_settings.enabled:
        return AuditVerdict(risky=False, audited=False)
    if not vllm_settings.host or not vllm_settings.model:
        raise LLMRecognitionError("VLLM_ENABLED=true iken VLLM_HOST ve VLLM_MODEL zorunludur")
    file_path = file_path or current_llm_file()
    # Gomulu ikili veri bloklari (base64 resim/ikon) denetime de gonderilmez;
    # alintilar gorunum metninde dogrulanir, gizlenmeyen metin birebir aynidir.
    if blob_min_chars is None:
        blob_min_chars = settings.scan.encoded_blob_min_chars
    blobs = find_encoded_blobs(masked_text, blob_min_chars)
    view = build_llm_input_view(masked_text, vllm_settings, blob_spans=blobs, phase="audit", file_path=file_path)
    overlap_chars = getattr(vllm_settings, "chunk_overlap_chars", 500)
    chunks = chunk_text(view.text, vllm_settings.max_file_chars, overlap_chars)
    file_context = describe_file_context(file_path)
    with LLMScanMetrics("audit", len(chunks), file_path) as metrics:
        async def audit_chunk(index: int, offset: int, chunk: str) -> list[AuditFinding]:
            # Tek parcayi denetler, yalnizca metinde dogrulanan bulgulari doner.
            async def scan(_part_offset: int, part: str) -> list[AuditFinding]:
                payload = build_audit_request(
                    part, vllm_settings.model, getattr(vllm_settings, "seed", 42),
                    max_tokens=getattr(vllm_settings, "max_tokens", 1024),
                    disable_thinking=getattr(vllm_settings, "disable_thinking", False),
                    presence_penalty=getattr(vllm_settings, "presence_penalty", 0.0),
                    file_context=file_context,
                )
                verdict = await metrics.request(vllm_settings, payload, call_vllm, parse_audit_response, index)
                verified, dropped = verify_audit_findings(part, verdict.findings)
                if dropped or (verdict.risky and not verified):
                    # Icerik degil, sadece sayilar loglanir.
                    logger.info(
                        "llm_audit_unverified file=%r chunk=%d model_risky=%s dropped_findings=%d kept_findings=%d",
                        metrics.file_path, index, verdict.risky, dropped, len(verified),
                    )
                return verified

            # Yanit max_tokens'ta kesilirse parca bolunup yeniden denetlenir
            # (tespit adimiyla ayni sinirlar); sinirda hala kesikse hata
            # caller'a ulasir ve dosya karantinaya gider.
            return await scan_with_split(scan, index, offset, chunk, overlap_chars)

        chunk_findings = await run_chunk_scans(chunks, audit_chunk)

    # Risk yalnizca metinde dogrulanan somut bir alintiya dayanir; modelin
    # "risk var" deyip dogrulanabilir alinti vermemesi dosyayi karantinaya
    # almaz. Chunk sirasiyla birlestirilir -> deterministik sonuc; overlap'li
    # chunk'larda ayni alinti tek bulgudur.
    findings: dict[str, AuditFinding] = {}
    for verified in chunk_findings:
        for finding in verified:
            findings.setdefault(finding.ilgili_bolum or finding.aciklama, finding)
    return AuditVerdict(risky=bool(findings), findings=list(findings.values()))


# ---------------------------------------------------------------------------
# Ayni icerik, ayni karar: denetim sonucunun kaydi
# ---------------------------------------------------------------------------
# Model temperature=0 ile bile calismadan calismaya farkli alinti/bulgu
# dondurebilir. Export'ta denetlenen icerik serbest birakma aninda BIREBIR
# ayniysa, kayitli sonuc kullanilir; icerik ya da denetimi etkileyen bir ayar
# (model, prompt, parcalama, ikili veri esigi, dosya adi) degistiyse anahtar
# tutmaz ve model yeniden cagrilir. Kayit alintilari (acik degerleri) icerdigi
# icin sifrelenir.

def audit_record_key(content: str, file_path: str, vllm_settings, blob_min_chars: int | None = None) -> str:
    import hashlib
    if blob_min_chars is None:
        blob_min_chars = settings.scan.encoded_blob_min_chars
    material = {
        "content": hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest(),
        "file": describe_file_context(file_path),
        "model": vllm_settings.model,
        "prompt": hashlib.sha256(load_audit_prompt().encode("utf-8")).hexdigest(),
        "max_file_chars": vllm_settings.max_file_chars,
        "overlap": getattr(vllm_settings, "chunk_overlap_chars", 500),
        "max_tokens": getattr(vllm_settings, "max_tokens", 1024),
        "seed": getattr(vllm_settings, "seed", 42),
        "disable_thinking": getattr(vllm_settings, "disable_thinking", False),
        "presence_penalty": getattr(vllm_settings, "presence_penalty", 0.0),
        "blob_min_chars": blob_min_chars,
        # Bulgu dogrulama mantigi degisince eski kayitlar yeniden kullanilmaz.
        "verifier": _VERIFIER_VERSION,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def encode_audit_record(verdict: AuditVerdict, key: str) -> str | None:
    if not verdict.audited:
        return None
    from app.core.crypto import encrypt_value
    payload = {
        "version": 1, "key": key, "risky": verdict.risky,
        "findings": [[f.aciklama, f.ilgili_bolum] for f in verdict.findings],
    }
    return encrypt_value(json.dumps(payload, ensure_ascii=False))


def decode_audit_record(record: str | None, key: str) -> AuditVerdict | None:
    """Kayit bu icerik+ayar anahtarina aitse sonucu dondurur; aksi halde None."""
    if not record:
        return None
    from app.core.crypto import decrypt_value
    try:
        payload = json.loads(decrypt_value(record))
        if payload.get("version") != 1 or payload.get("key") != key or not isinstance(payload.get("risky"), bool):
            return None
        findings = [AuditFinding(aciklama=str(a), ilgili_bolum=str(b)) for a, b in payload["findings"]]
    except Exception:
        # Bozuk/eski anahtarla sifrelenmis kayit: yeniden denetlenir (asla "temiz" sayilmaz).
        return None
    return AuditVerdict(risky=payload["risky"], findings=findings)
