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

Bir dosyanin chunk'lari (ve farkli dosyalarin cagrilari) eszamanli taranir;
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
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

# httpx: LLM sunucusunun /v1/chat/completions uc noktasina istek atmak icin
# kullanilan HTTP istemcisi - bu dosyadaki tek ag erisimi burasidir.
# AsyncClient: es zamanli (concurrent) coklu istek icin - bkz. modul dokstring'i.
import httpx

from app.core.http_diagnostics import http_error_detail
from app.services.detectors import LLM_FALLBACK_ENTITY_TYPE, DetectionResult, normalize_llm_entity_type
from app.services.rule_engine import _overlaps
from app.services.text_chunking import chunk_text as _overlap_chunks
from app.services.llm_runtime import LLMScanMetrics


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


# vLLM'e gonderilecek tespit istegini (prompt + JSON sema + metin) hazirlar.
def build_detection_request(
    text: str, model: str, seed: int, extra_instructions: list[str] | None = None,
    max_tokens: int = 512, disable_thinking: bool = False, presence_penalty: float = 0.0,
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
            {"role": "system", "content": _augment_prompt(load_llm_prompt(), extra_instructions)},
            {"role": "user", "content": text},
        ],
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if presence_penalty:
        payload["presence_penalty"] = presence_penalty
    return payload


# vLLM sunucusuna istegi gonderir, ham JSON yaniti dondurur; her hatayi LLMRecognitionError'a cevirir.
async def call_vllm(host: str, timeout_seconds: float, payload: dict, api_key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    started = monotonic()
    try:
        async with httpx.AsyncClient() as client:
            response = await asyncio.wait_for(
                client.post(
                    f"{host.rstrip('/')}/v1/chat/completions", json=payload,
                    headers=headers, timeout=timeout_seconds,
                ), timeout=timeout_seconds,
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
            "'{\"enable_thinking\": false}' ile baslatin"
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


# vLLM yanitini ayristirir; her bulguyu metinde GERCEKTEN gecip gecmedigini kontrol ederek dogrular.
def parse_and_verify_detections(
    raw_response: dict,
    text: str,
    consumed: list[tuple[int, int]],
    *,
    base_offset: int = 0,
    source: str = "llm",
    repair_stats: FindingRepairStats | None = None,
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

        for m in re.finditer(re.escape(value), text):
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


# Metni parcalara bolup her parcayi LLM ile tarar, sonuclari birlestirir. LLM kapaliysa bos liste doner.
async def find_llm_detections(
    text: str,
    consumed: list[tuple[int, int]],
    vllm_settings,
    metadata: dict | None = None,
    extra_instructions: list[str] | None = None,
    repair_stats: FindingRepairStats | None = None,
) -> list[DetectionResult]:
    if not vllm_settings.enabled:
        return []
    if not vllm_settings.host or not vllm_settings.model:
        raise LLMRecognitionError("VLLM_ENABLED=true iken VLLM_HOST ve VLLM_MODEL zorunludur")

    overlap_chars = getattr(vllm_settings, "chunk_overlap_chars", 500)
    chunks = chunk_text(text, vllm_settings.max_file_chars, overlap_chars)
    seed = getattr(vllm_settings, "seed", 42)
    detections: dict[tuple, DetectionResult] = {}
    confidence_rank = {"dusuk": 0, "orta": 1, "yuksek": 2}

    with LLMScanMetrics("detection", len(chunks), (metadata or {}).get("file_path")) as metrics:
        # Tek chunk'i tarar; kesilirse scan_with_split yalnizca bu chunk'i
        # bolup yeniden tarar (basarili chunk'lar tekrar gonderilmez).
        async def scan_chunk(index: int, offset: int, chunk: str) -> list[DetectionResult]:
            async def scan(part_offset: int, part: str) -> list[DetectionResult]:
                payload = build_detection_request(
                    part, vllm_settings.model, seed, extra_instructions,
                    max_tokens=getattr(vllm_settings, "max_tokens", 1024),
                    disable_thinking=getattr(vllm_settings, "disable_thinking", False),
                    presence_penalty=getattr(vllm_settings, "presence_penalty", 0.0),
                )
                return await metrics.request(
                    vllm_settings, payload, call_vllm,
                    lambda raw: parse_and_verify_detections(
                        raw, part, consumed, base_offset=part_offset, repair_stats=repair_stats,
                    ),
                    index,
                )

            return await scan_with_split(scan, index, offset, chunk, overlap_chars)

        # Hata veren chunk kardeslerini HEMEN iptal eder: gather'in sonradan
        # iptali, bosalan _gate slotunu kuyruktaki chunk'in almasina yetismez.
        async def scan_or_cancel_siblings(index: int, offset: int, chunk: str) -> list[DetectionResult]:
            try:
                return await scan_chunk(index, offset, chunk)
            except BaseException:
                current = asyncio.current_task()
                for task in tasks:
                    if task is not current:
                        task.cancel()
                raise

        # Tum chunk'lar es zamanli baslar (sinir: metrics.request icindeki _gate).
        # TaskGroup yerine gather: ExceptionGroup sarmalamasi olmadan
        # LLMRecognitionError caller'a aynen ulasir; ilk hatada kalan
        # gorevler iptal edilir - kismi sonuc asla dondurulmez.
        tasks: list[asyncio.Future] = []
        tasks.extend(asyncio.ensure_future(scan_or_cancel_siblings(index, offset, chunk))
                     for index, (offset, chunk) in enumerate(chunks, 1))
        try:
            chunk_results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        # Chunk sirasiyla birlestir -> deterministik cikti.
        for chunk_detections in chunk_results:
            for detection in chunk_detections:
                key = (detection.start, detection.end, detection.tip)
                previous = detections.get(key)
                if previous is None or confidence_rank[detection.guven_seviyesi] > confidence_rank[previous.guven_seviyesi]:
                    detections[key] = detection
    return sorted(detections.values(), key=lambda detection: detection.start or 0)
