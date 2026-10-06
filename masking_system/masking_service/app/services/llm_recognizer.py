"""vLLM'in OpenAI-uyumlu sunucusunu kullanan, sabit formati olmayan hassas
verileri tespit eden LLM detector katmani.

rule_engine.find_matches'in TAM TERSINE bu modul LLM sunucusuna HTTP cagrisi
yapar - bilerek ayri tutuldu ki rule_engine saf/DB-siz/ag-siz kalsin.

Guvenlik/guvenilirlik ilkeleri (asla degistirilmemeli):
  1. LLM'in bildirdigi HER deger, orijinal metinde BIREBIR (re.escape +
     re.finditer) bulunmali. Bulunamayan deger sessizce atilir - model asla
     "uydurma" bir esleme uretemez, kendi bildirdigi ofsetlere hic
     guvenilmez (LLM'ler uzun metinde karakter sayimini guvenilir yapamaz).
  2. Regex/checksum ile zaten eslesmis (consumed) araliklarla cakisan LLM
     adaylari reddedilir - deterministik sonuc her zaman kazanir.
  3. LLM detector etkinse her metin dosyasinda calisir. Buyuk metinlerde
     atlama yapmaz; metni parcalara boler.
  4. Tamamlanamayan herhangi bir chunk LLMRecognitionError uretir;
     kismi sonuc basarili sayilmaz ve caller dosyayi karantinaya alir.
     Yanit yapisi saglam ama TEK bir bulgunun semasi bozuksa parca
     dusurulmez: degeri metinde birebir geciyorsa bulgu `orta` guven ve
     KURUMSAL_TANIMLAYICI tipiyle kabul edilir, gecmiyorsa yalnizca o bulgu
     atilir. Ikisi de FindingRepairStats ile sayilir ve AuditLog'a yazilir.
  5. LLM degeri yalnizca kelime/identifier sinirinda eslenir (camelCase ve
     harf-rakam gecisi sinir sayilir); `Alignment` icindeki `Ali` eslenmez.
     VLLM_MIN_AUTO_MASK_CHARS'tan kisa degerlerin guveni `dusuk`e iner.
  6. Katman 1'in kesin bulgulari LLM'e gecici yer tutucuyla gider (bkz.
     llm_input_view); bulgu ofsetleri orijinal metne geri eslenir, gecici
     yer tutucuyla cakisan bulgu atilir.

Bir dosyanin chunk'lari (ve farkli dosyalarin cagrilari) run_chunk_scans ile eszamanli taranir;
toplam eszamanlilik llm_runtime._gate ile sinirlidir (tespit ve audit ayni
endpoint admission sinirini paylasir). Sonuclar chunk sirasiyla birlestirilir.
max_tokens'ta kesilen (finish_reason=length) bir chunk ikiye bolunup yalnizca
o chunk yeniden taranir; derinlik/min boyut sinirinda hala kesilirse hata
yukari firlatilir (dosya karantinaya).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

# HTTP hata turleri; istemci ve baglanti havuzunun yasam dongusu llm_transport'ta.
import httpx

from app.core.http_diagnostics import http_error_detail
from app.services.detectors import LLM_FALLBACK_ENTITY_TYPE, DetectionResult, normalize_llm_entity_type
from app.services.rule_engine import _overlaps
from app.services.llm_input_view import LLMInputStats, RedactedView, build_llm_input_view
from app.services.text_chunking import DedupedText, chunk_text as _overlap_chunks, dedupe_for_llm
from app.services.tabular_scan import build_condensed, classify_columns, column_cells, find_tables, render_sample
from app.services.llm_runtime import LLMScanMetrics
from app.services.llm_transport import llm_http_scope


class LLMRecognitionError(RuntimeError):
    """Incomplete/failed LLM scan; caller must quarantine this file."""


class LLMTruncatedError(LLMRecognitionError):
    """Response cut at max_tokens (finish_reason=length); chunk may be split and rescanned."""


# Semasi bozuk LLM bulgularinin sayaci: `repaired` degeri metinde dogrulanip
# orta/KURUMSAL_TANIMLAYICI ile kabul edilen, `dropped` dogrulanamayip atilan.
# Yalnizca sayi tutar; deger/gerekce asla saklanmaz.
@dataclass
class FindingRepairStats:
    repaired: int = 0
    dropped: int = 0


_CONFIDENCE_LEVELS = ("yuksek", "orta", "dusuk")
_REPAIRED_CONFIDENCE = "orta"
_REPAIRED_REASON = "LLM bulgu semasi bozuktu; deger metinde birebir dogrulandi"


# Kesilen chunk'i bolme sinirlari: en fazla 3 kez bolunur, parca en az 800 karakter.
_MAX_SPLIT_DEPTH = 3
_MIN_SPLIT_CHARS = 800


_PROMPT_PATH = Path(__file__).with_name("llm_prompt.txt")

_FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "bulgular": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bulunan_deger": {"type": "string"},
                    "tip": {"type": "string"},
                    "guven_seviyesi": {"type": "string", "enum": ["yuksek", "orta", "dusuk"]},
                    "gerekce": {"type": "string"},
                },
                "required": ["bulunan_deger", "tip", "guven_seviyesi", "gerekce"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["bulgular"],
    "additionalProperties": False,
}


# LLM'e gonderilecek sistem promptunu diskten (llm_prompt.txt) okur.
def load_llm_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LLMRecognitionError(f"LLM prompt dosyasi okunamadi ({_PROMPT_PATH}): {exc}") from exc


# Temel LLM promptuna, DB'deki ozel (kurum tanimli) tarama talimatlarini ekler.
def _augment_prompt(base_prompt: str, extra_instructions: list[str] | None) -> str:
    instructions = [i.strip() for i in (extra_instructions or []) if i and i.strip()]
    if not instructions:
        return base_prompt
    bullet_list = "\n".join(f"- {instruction}" for instruction in instructions)
    return f"{base_prompt}\n\nAyrica, kurum tanimli su ozel kategorilere de dikkat et:\n{bullet_list}"


# Modele dosya turu ipucu verir (orn. "application.yml", "pom.xml"). Yalnizca
# dosya ADI gonderilir, dizin yolu gonderilmez; bulgular yine sadece taranan
# metinde birebir dogrulandigi icin bu satirdan deger uretilemez.
def describe_file_context(file_path: str | None) -> str | None:
    name = Path(str(file_path or "").replace("\\", "/")).name
    if not name:
        return None
    suffix = Path(name).suffix.lstrip(".").lower() or "yok"
    return f"Dosya baglami (yalnizca bilgi, taranacak metin degildir): ad={name!r} uzanti={suffix}"


# Sistem promptunun SONUNA dosya baglamini ekler. Sona eklemek, sabit prompt
# onekini tum isteklerde ayni tutar (vLLM --enable-prefix-caching isabeti).
def with_file_context(system_prompt: str, file_context: str | None) -> str:
    return f"{system_prompt}\n\n{file_context}" if file_context else system_prompt


# vLLM'e gonderilecek tespit istegini (prompt + JSON sema + metin) hazirlar.
def build_detection_request(
    text: str, model: str, seed: int, extra_instructions: list[str] | None = None,
    max_tokens: int = 512, disable_thinking: bool = False, presence_penalty: float = 0.0,
    file_context: str | None = None, reasoning_effort: str = "",
) -> dict:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "seed": seed,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "bulgular_semasi", "schema": _FINDINGS_SCHEMA, "strict": True},
        },
        "messages": [
            {
                "role": "system",
                "content": with_file_context(_augment_prompt(load_llm_prompt(), extra_instructions), file_context),
            },
            {"role": "user", "content": text},
        ],
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if presence_penalty:
        payload["presence_penalty"] = presence_penalty
    return payload


# vLLM sunucusuna istegi gonderir, ham JSON yaniti dondurur; her hatayi LLMRecognitionError'a cevirir.
async def call_vllm(host: str, timeout_seconds: float, payload: dict, api_key: str | None = None) -> dict:
    started = monotonic()
    try:
        async with llm_http_scope() as transport:
            response = await asyncio.wait_for(
                transport.post_completion(host, timeout_seconds, payload, api_key),
                timeout=timeout_seconds,
            )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        detail = http_error_detail(
            exc, layer="backend_llm", elapsed_seconds=monotonic() - started,
            timeout_seconds=timeout_seconds,
        )
        message = "vLLM yaniti gecerli JSON degil" if isinstance(exc, json.JSONDecodeError) else "vLLM istegi basarisiz"
        if isinstance(exc, TimeoutError):
            message = "LLM HTTP toplam sure siniri asildi"
        raise LLMRecognitionError(f"{message}: {detail}") from exc


def _truncation_hint(choice: object) -> str:
    """Explain the likely cause of a max_tokens cut, without echoing content.

    Kucuk bir dosyada (orn. 600 karakter) token tavaninin dolmasi neredeyse
    her zaman modelin dusunme (thinking) modunda calistigini ya da ayni
    ciktiyi tekrarlayan bir donguye girdigini gosterir.
    """
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        return ""
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    if (isinstance(reasoning, str) and reasoning.strip()) or content.lstrip().startswith("<think>"):
        return (
            " - model DUSUNME (thinking) modunda yanit uretti; token butcesi dusunmeye harcandi. "
            "VLLM_DISABLE_THINKING=true yapin ve vLLM'i --default-chat-template-kwargs "
            "'{\"enable_thinking\": false}' ile baslatin (Ollama'da VLLM_REASONING_EFFORT=none)"
        )
    stripped = content.strip()
    if len(stripped) > 400:
        tail = stripped[-400:]
        # Ayni kisa parcanin arka arkaya tekrari: donguye giren model.
        for size in range(8, 101):
            unit = tail[-size:]
            if tail.count(unit) >= max(3, 200 // size):
                return (
                    " - model ayni ciktiyi tekrarlayan bir donguye girdi. VLLM_PRESENCE_PENALTY "
                    "(orn. 1.5) ayarini deneyin"
                )
    return ""


def require_complete_response(raw_response: dict) -> None:
    try:
        choice = raw_response["choices"][0]
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise LLMRecognitionError("LLM yaniti beklenen sekilde degil") from exc
    # Older compatible servers may omit finish_reason. A supplied non-stop
    # reason (length/content_filter/tool_calls) never constitutes a full scan.
    if finish == "length":
        raise LLMTruncatedError(
            "LLM yaniti tamamlanmadi (finish_reason stop degil: length, max_tokens siniri)"
            + _truncation_hint(choice)
        )
    if finish is not None and finish != "stop":
        raise LLMRecognitionError("LLM yaniti tamamlanmadi (finish_reason stop degil)")


# `pos` bir kelime/identifier siniri mi? Harf-rakam gecisi, camelCase
# (`poseidonGateway` -> `Gateway`) ve kisaltma sonu (`HTTPServer` -> `Server`)
# sinir sayilir; kelimenin ortasi (`Alignment` icindeki `Ali`) sayilmaz.
def _is_token_boundary(text: str, pos: int) -> bool:
    if pos <= 0 or pos >= len(text):
        return True
    before, after = text[pos - 1], text[pos]
    if not (before.isalnum() and after.isalnum()):
        return True
    if before.isalpha() != after.isalpha():
        return True
    if before.islower() and after.isupper():
        return True
    return before.isupper() and after.isupper() and pos + 1 < len(text) and text[pos + 1].islower()


# LLM degerinin metindeki, kelime ortasina denk gelmeyen gecisleri. Kisa/genel
# bir deger (`core`, `Ali`) baska kelimelerin icinde eslesip - sinir dogrulayici
# eslesmeyi tum identifier'a genislettigi icin - ilgisiz kodu maskelemesin.
def _aligned_occurrences(value: str, text: str):
    for match in re.finditer(re.escape(value), text):
        if _is_token_boundary(text, match.start()) and _is_token_boundary(text, match.end()):
            yield match


# Cok kisa degerler (orn. 1-2 karakter) guvenle otomatik maskelenemez: guven
# `dusuk`e indirilir, boylece VLLM_LOW_CONFIDENCE_ACTION (ignore/review) karar verir.
def _effective_confidence(value: str, confidence: str, min_value_chars: int) -> str:
    return "dusuk" if len(value.strip()) < min_value_chars else confidence


# Model JSON'daki `\u00f6` gibi kacislari cozup `Tokgöz` dondurebilir; metinde
# yalnizca kacisli hali (`Tokg\u00f6z`) geciyorsa bulgu o yazilisla eslenir.
def _as_written_in(value: str, text: str) -> str:
    if value in text:
        return value
    escaped = json.dumps(value, ensure_ascii=True)[1:-1]
    return escaped if escaped != value and escaped in text else value


# vLLM yanitini ayristirir; her bulguyu metinde GERCEKTEN gecip gecmedigini kontrol ederek dogrular.
def parse_and_verify_detections(
    raw_response: dict,
    text: str,
    consumed: list[tuple[int, int]],
    *,
    base_offset: int = 0,
    source: str = "llm",
    repair_stats: FindingRepairStats | None = None,
    min_value_chars: int = 0,
) -> list[DetectionResult]:
    require_complete_response(raw_response)
    try:
        content = raw_response["choices"][0]["message"]["content"]
        parsed: dict = json.loads(content)
        findings = parsed["bulgular"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMRecognitionError("vLLM yaniti beklenen sekilde degil (JSON/yanit yapisi)") from exc

    if not isinstance(findings, list):
        raise LLMRecognitionError("vLLM yanitinda 'bulgular' liste degil")

    # `consumed` yalnizca mevcut placeholder gibi dokunulmaz span'leri
    # tasir. LLM'in kendi adaylarini burada tuketmek, merkezi resolver'in
    # uzunluk/otorite kararini daha adaylar toplanmadan vermek olurdu.
    protected_spans = list(consumed)
    detections: list[DetectionResult] = []
    seen: set[tuple[int, int, str, str]] = set()
    for item in findings:
        # Model ciktisi yerel olarak dogrulanir: istenen JSON semasi yerel
        # kontrolun yerini tutmaz (liste/dict guven degeri set uyeliginde
        # TypeError verir). Bozuk bulgu sessizce atilmaz - repair_stats'ta
        # sayilir. Hata/sayac metnine deger ya da indeks yazilmaz.
        value = item.get("bulunan_deger") if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            if repair_stats is not None:
                repair_stats.dropped += 1
            continue
        value = _as_written_in(value, text)
        raw_type = item.get("tip")
        confidence = item.get("guven_seviyesi")
        reason = item.get("gerekce")
        broken = (
            not isinstance(raw_type, str) or not raw_type
            or not isinstance(confidence, str) or confidence not in _CONFIDENCE_LEVELS
            or not isinstance(reason, str)
        )
        if broken:
            if value not in text:
                if repair_stats is not None:
                    repair_stats.dropped += 1
                continue
            entity_type = LLM_FALLBACK_ENTITY_TYPE
            confidence = _REPAIRED_CONFIDENCE
            reason = _REPAIRED_REASON
            # Bozuk alanlar (liste/dict olabilir) raw_result'a tasinmaz.
            raw_item = {"bulunan_deger": value, "tip": entity_type, "guven_seviyesi": confidence,
                        "gerekce": reason, "sema_onarildi": True}
            if repair_stats is not None:
                repair_stats.repaired += 1
        else:
            entity_type = normalize_llm_entity_type(raw_type)
            raw_item = dict(item)
        confidence = _effective_confidence(value, confidence, min_value_chars)

        for m in _aligned_occurrences(value, text):
            span = (base_offset + m.start(), base_offset + m.end())
            if _overlaps(span, protected_spans):
                continue
            dedup_key = (span[0], span[1], entity_type, confidence)
            if dedup_key in seen:
                continue
            detections.append(
                DetectionResult(
                    deger=value,
                    tip=entity_type,
                    guven_seviyesi=confidence,
                    kaynak_motor=source,
                    gerekce=reason,
                    start=span[0],
                    end=span[1],
                    raw_result=dict(raw_item),
                )
            )
            seen.add(dedup_key)

    detections.sort(key=lambda d: d.start or 0)
    return detections


# Buyuk metinleri vLLM'e tek seferde gonderilebilecek boyutta parcalara
# boler; mumkunse satir sinirindan keser (kelime/token'i ortadan bolmemek icin).
def chunk_text(text: str, max_chunk_chars: int, overlap_chars: int = 500) -> list[tuple[int, str]]:
    return _overlap_chunks(text, max_chunk_chars, overlap_chars)


# Kesilen chunk'i ortasina en yakin satir sonundan ikiye boler. Sag parca
# overlap kadar geriden baslar ki sinira denk gelen deger kacmasin.
# Donus: [(goreli_offset, parca), (goreli_offset, parca)] veya bolunemiyorsa None.
def _split_chunk(chunk: str, overlap_chars: int) -> list[tuple[int, str]] | None:
    if len(chunk) < 2 * _MIN_SPLIT_CHARS:
        return None
    lo, hi = _MIN_SPLIT_CHARS, len(chunk) - _MIN_SPLIT_CHARS
    mid = len(chunk) // 2
    candidates = [
        pos + 1 for pos in (chunk.rfind("\n", lo, mid), chunk.find("\n", mid, hi))
        if pos != -1 and lo <= pos + 1 <= hi
    ]
    cut = min(candidates, key=lambda c: abs(c - mid)) if candidates else mid
    overlap = max(0, min(overlap_chars, cut // 4))
    right_start = cut - overlap
    return [(0, chunk[:cut]), (right_start, chunk[right_start:])]


# Tek chunk'i `scan(offset, chunk)` ile tarar; yanit max_tokens'ta kesilirse
# (LLMTruncatedError) yalnizca bu chunk'i bolup parcalari yeniden tarar ve
# sonuclari birlestirir. Tespit ve denetim adimlari ortak kullanir. Sinira
# ragmen hala kesikse hata caller'a ulasir (karantina, fail-safe).
async def scan_with_split(scan, index: int, offset: int, chunk: str, overlap_chars: int,
                          depth: int = 0) -> list:
    try:
        return await scan(offset, chunk)
    except LLMTruncatedError as exc:
        parts = _split_chunk(chunk, overlap_chars) if depth < _MAX_SPLIT_DEPTH else None
        if parts is None:
            raise LLMTruncatedError(
                f"{exc} (chunk={index} offset={offset} uzunluk={len(chunk)} "
                f"bolme_derinligi={depth}; daha fazla bolunemiyor)"
            ) from exc
    results: list = []
    for rel, part in parts:
        results.extend(await scan_with_split(scan, index, offset + rel, part, overlap_chars, depth + 1))
    return results


# Bir dosyanin tum chunk'larini es zamanli tarar ve sonuclari chunk sirasiyla
# dondurur (toplam eszamanlilik llm_runtime._gate ile sinirli). Tespit ve
# denetim ortak kullanir. Ilk hatada kardes gorevler HEMEN iptal edilir -
# gather'in sonradan iptali, bosalan _gate slotunu kuyruktaki chunk'in
# almasina yetismez. TaskGroup yerine gather: ExceptionGroup sarmalamasi
# olmadan LLMRecognitionError caller'a aynen ulasir; kismi sonuc asla donmez.
async def run_chunk_scans(chunks: list[tuple[int, str]], scan_chunk) -> list:
    tasks: list[asyncio.Future] = []

    async def scan_or_cancel_siblings(index: int, offset: int, chunk: str):
        try:
            return await scan_chunk(index, offset, chunk)
        except BaseException:
            current = asyncio.current_task()
            for task in tasks:
                if task is not current:
                    task.cancel()
            raise

    tasks.extend(
        asyncio.ensure_future(scan_or_cancel_siblings(index, offset, chunk))
        for index, (offset, chunk) in enumerate(chunks, 1)
    )
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# Tekrarsiz metindeki bulguyu gorunum metnine tasir: (1) ayni siniftaki her
# satirda konumsal karsiligi (`PRJ-ALFA-7` -> atlanan satirdaki `PRJ-ALFA-8`),
# (2) degerin metnin baska yerlerindeki birebir gecisleri. Rakamlar genel bir
# kaliba cevrilmez: bulunan bir port numarasi tum sayilari maskeletmez.
def _expand_to_all_occurrences(
    detections: list[DetectionResult], deduped: DedupedText, text: str,
    protected_spans: list[tuple[int, int]],
) -> list[DetectionResult]:
    expanded: list[DetectionResult] = []
    literal_done: set[tuple[str, str, str]] = set()
    for detection in detections:
        spans = deduped.member_spans(text, detection.start or 0, detection.end or 0) or []
        key = (detection.deger, detection.tip, detection.guven_seviyesi)
        if key not in literal_done:
            literal_done.add(key)
            spans += [(match.start(), match.end()) for match in _aligned_occurrences(detection.deger, text)]
        for span in spans:
            if not _overlaps(span, protected_spans):
                expanded.append(replace(detection, start=span[0], end=span[1], deger=text[span[0]:span[1]]))
    return expanded


# Ornek turda secilen sutunlarin her hucresi icin bulgu uretir (gorunum
# koordinatlarinda). Hucre basi/sonu bosluk maskelenmez.
def _column_detections(
    text: str, tables, decisions, protected_spans: list[tuple[int, int]], min_value_chars: int,
) -> list[DetectionResult]:
    results = []
    for table, column, cell, decision in column_cells(tables, decisions):
        raw = text[cell.start:cell.end]
        value = raw.strip()
        if not value:
            continue
        start = cell.start + raw.index(value)
        span = (start, start + len(value))
        if _overlaps(span, protected_spans):
            continue
        confidence = _effective_confidence(value, decision.guven_seviyesi, min_value_chars)
        results.append(DetectionResult(
            deger=value, tip=decision.tip, guven_seviyesi=confidence, kaynak_motor="llm",
            gerekce=decision.gerekce, start=span[0], end=span[1],
            raw_result={"bulunan_deger": value, "tip": decision.tip, "guven_seviyesi": confidence,
                        "gerekce": decision.gerekce, "sutun": table.label(column)},
        ))
    return results


# Gorunum koordinatlarindaki bulguyu orijinal metne tasir; gecici yer
# tutucuyla cakisan bulgu eslenemez ve atilir.
def _to_original(view: RedactedView, detection: DetectionResult) -> DetectionResult | None:
    if view.is_identity:
        return detection
    span = view.to_original(detection.start or 0, detection.end or 0)
    if span is None:
        return None
    return replace(detection, start=span[0], end=span[1])


# Metni parcalara bolup her parcayi LLM ile tarar, sonuclari birlestirir. LLM kapaliysa bos liste doner.
@llm_http_scope()
async def find_llm_detections(
    text: str,
    consumed: list[tuple[int, int]],
    vllm_settings,
    metadata: dict | None = None,
    extra_instructions: list[str] | None = None,
    repair_stats: FindingRepairStats | None = None,
    known_spans: list[tuple[int, int, str]] | None = None,
    blob_spans: list[tuple[int, int]] | None = None,
    input_stats: LLMInputStats | None = None,
) -> list[DetectionResult]:
    if not vllm_settings.enabled or not text.strip():
        return []
    if not vllm_settings.host or not vllm_settings.model:
        raise LLMRecognitionError("VLLM_ENABLED=true iken VLLM_HOST ve VLLM_MODEL zorunludur")

    file_path = (metadata or {}).get("file_path")
    view = build_llm_input_view(
        text, vllm_settings, known_spans, blob_spans, phase="detection", file_path=file_path,
    )
    view_consumed = view.to_view_spans(consumed)
    overlap_chars = getattr(vllm_settings, "chunk_overlap_chars", 500)
    seed = getattr(vllm_settings, "seed", 42)
    min_value_chars = getattr(vllm_settings, "min_auto_mask_chars", 0)
    file_context = describe_file_context(file_path)

    # Parcalari LLM'e gonderir; kesilen parca scan_with_split ile bolunup
    # yalnizca o parca yeniden taranir (basarili parcalar tekrar gonderilmez).
    async def scan_chunks(phase_chunks, metrics, parse_consumed) -> list[list[DetectionResult]]:
        async def scan_chunk(index: int, offset: int, chunk: str) -> list[DetectionResult]:
            async def scan(part_offset: int, part: str) -> list[DetectionResult]:
                payload = build_detection_request(
                    part, vllm_settings.model, seed, extra_instructions,
                    max_tokens=getattr(vllm_settings, "max_tokens", 1024),
                    disable_thinking=getattr(vllm_settings, "disable_thinking", False),
                    presence_penalty=getattr(vllm_settings, "presence_penalty", 0.0),
                    reasoning_effort=getattr(vllm_settings, "reasoning_effort", ""),
                    file_context=file_context,
                )
                return await metrics.request(
                    vllm_settings, payload, call_vllm,
                    lambda raw: parse_and_verify_detections(
                        raw, part, parse_consumed, base_offset=part_offset,
                        repair_stats=repair_stats, min_value_chars=min_value_chars,
                    ),
                    index,
                )

            return await scan_with_split(scan, index, offset, chunk, overlap_chars)

        return await run_chunk_scans(phase_chunks, scan_chunk)

    view_results: list[list[DetectionResult]] = []
    tables = find_tables(view.text, file_path) if len(view.text) > vllm_settings.max_file_chars else []
    sample_chunk_count = 0
    if tables:
        # Tablolu veri: ornek satirlarda hucresinin tamami hassas cikan sutunlar
        # butunuyle maskelenir; diger sutunlarin farkli degerleri asagida taranir.
        sample_chunks = chunk_text(render_sample(view.text, tables), vllm_settings.max_file_chars, overlap_chars)
        sample_chunk_count = len(sample_chunks)
        with LLMScanMetrics("detection", sample_chunk_count, file_path) as metrics:
            sample_results = await scan_chunks(sample_chunks, metrics, [])
        decisions = classify_columns(view.text, tables, [
            (d.deger, d.tip, d.guven_seviyesi, d.gerekce or "")
            for chunk_detections in sample_results for d in chunk_detections
        ])
        view_results.append(_column_detections(view.text, tables, decisions, view_consumed, min_value_chars))
        deduped = build_condensed(view.text, tables, decisions)
    else:
        # Tekrarli buyuk dosyada (log, veri dokumu) yalnizca rakamlari farkli
        # satirlar bir kez taranir; bulgular asagida her satirdaki karsiligina tasinir.
        deduped = dedupe_for_llm(view.text, vllm_settings.max_file_chars, normalize_digits=True)
    scan_text = deduped.text if deduped is not None else view.text
    chunks = chunk_text(scan_text, vllm_settings.max_file_chars, overlap_chars)
    if input_stats is not None:
        input_stats.record(text, view, sample_chunk_count + len(chunks))
    detections: dict[tuple, DetectionResult] = {}
    confidence_rank = {"dusuk": 0, "orta": 1, "yuksek": 2}

    with LLMScanMetrics("detection", len(chunks), file_path) as metrics:
        chunk_results = await scan_chunks(chunks, metrics, [] if deduped is not None else view_consumed)
    if deduped is not None:
        chunk_results = [
            _expand_to_all_occurrences(chunk_detections, deduped, view.text, view_consumed)
            for chunk_detections in chunk_results
        ]
    view_results.extend(chunk_results)

    # Parca sirasiyla birlestir -> deterministik cikti.
    for chunk_detections in view_results:
        for view_detection in chunk_detections:
            detection = _to_original(view, view_detection)
            if detection is None:
                continue
            key = (detection.start, detection.end, detection.tip)
            previous = detections.get(key)
            if previous is None or confidence_rank[detection.guven_seviyesi] > confidence_rank[previous.guven_seviyesi]:
                detections[key] = detection
    return sorted(detections.values(), key=lambda detection: detection.start or 0)
