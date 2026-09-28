"""Detector registry/orchestrator shared by all detection layers.

Each layer returns DetectionResult objects. The orchestrator does not know
whether a result came from the corporate dictionary/rule layer, a future
Presidio+regex layer, or the LLM layer; adding a new layer means registering a
new Detector implementation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from traceback import walk_tb
from typing import Protocol

# Katman 1 (sozluk/regex) kural motoru: RuleSpec/Match veri tipleri ve
# kurallari derleyip eslestiren fonksiyonlar buradan alinir.
from app.services.rule_engine import Match, RuleSpec, compile_rules, find_matches_compiled


# Bir bulgunun guven seviyesini ifade eden basit string takma adi (orn. "yuksek").
ConfidenceLevel = str

logger = logging.getLogger("uvicorn.error.detectors")


# Herhangi bir detector katmaninin (sozluk/Presidio/LLM) urettigi TEK bir
# hassas veri bulgusunu temsil eden, katmandan bagimsiz ortak veri yapisi.
@dataclass(frozen=True)
class DetectionResult:
    deger: str
    tip: str
    guven_seviyesi: ConfidenceLevel
    kaynak_motor: str
    gerekce: str | None = None
    start: int | None = None
    end: int | None = None
    rule: RuleSpec | None = None
    raw_result: dict | None = None


# Bir detector'in tek bir dosya/metin uzerinde calismasinin toplu sonucu:
# bulgular, zaten maskelenmis (placeholder) araliklar ve olusan hatalar.
@dataclass(frozen=True)
class DetectorOutput:
    results: list[DetectionResult] = field(default_factory=list)
    already_masked_spans: list[tuple[int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # A detector layer raising an unexpected exception (bkz.
    # DetectionOrchestrator.scan) - distinct from `errors` (bkz. LLMDetector,
    # kendi hatalarini zaten yakalayip buraya DEGIL errors'a yazar). Bu alan
    # SADECE gercekten beklenmeyen bir CRASH icin kullanilir ve quarantine
    # kararini (bkz. exporter.py _finalize_file) settings.vllm.enabled
    # durumundan BAGIMSIZ olarak tetikler - bir Katman 1/2 cokmesi LLM'in
    # acik/kapali olmasiyla ilgisizdir.
    crashes: list[str] = field(default_factory=list)


# Her detection katmaninin (sozluk/Presidio/LLM) uymasi gereken ortak arayuz -
# orchestrator, hangi katmanla konustugunu bilmeden bu arayuz uzerinden calisir.
#
# async: uc katman da (RuleBasedDetector/PresidioDetector/LLMDetector) ayni
# imzayi paylasir ama SADECE LLMDetector gercekten `await` eder (agdan
# vLLM'e gider) - Rule/Presidio CPU-bound oldugu icin `await`siz,
# aninda doner. Tekdüze (uniform) async arayuz sayesinde DetectionOrchestrator.scan()
# hangi katmanin gercekten I/O yaptigini bilmeden hepsini `await`leyebilir;
# cagiran taraf (exporter.py) birden fazla DOSYANIN scan() cagrisini
# `asyncio.gather` ile es zamanli tetikleyerek gercek concurrency kazanir.
class Detector(Protocol):
    name: str

    # Verilen metni tarayip bu katmana ozgu bulgulari DetectorOutput olarak dondurur.
    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        ...


# Aktif detector katmanlarinin (Detector implementasyonlarinin) kayit defteri.
class DetectorRegistry:
    # Bos bir detector listesiyle baslar.
    def __init__(self) -> None:
        self._detectors: list[Detector] = []

    # Yeni bir detector katmanini (orn. RuleBasedDetector) kayda ekler.
    def register(self, detector: Detector) -> None:
        self._detectors.append(detector)

    # Kayitli tum detector'larin (siralarini koruyan) bir kopyasini dondurur.
    def enabled_detectors(self) -> list[Detector]:
        return list(self._detectors)


# Kayitli tum detector katmanlarini sirayla calistirip sonuclarini birlestiren
# ust-duzey koordinator - katmanlar-arasi cakisma cozumunu KENDISI yapmaz
# (bkz. asagidaki scan metodunun NOT'u).
class DetectionOrchestrator:
    # Calistirilacak detector'larin kayitli oldugu registry'yi saklar.
    def __init__(self, registry: DetectorRegistry) -> None:
        self.registry = registry

    # Tum kayitli detector katmanlarini (Rule->Presidio->LLM) sirayla calistirip
    # bulgularini birlestirir. Cakisma cozumu burada degil, OverlapResolver'da yapilir.
    async def scan(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        metadata = dict(metadata or {})
        base_consumed: list[tuple[int, int]] = list(metadata.get("consumed_spans", []))
        results: list[DetectionResult] = []
        already_masked: list[tuple[int, int]] = []
        errors: list[str] = []
        crashes: list[str] = []

        for detector in self.registry.enabled_detectors():
            metadata["consumed_spans"] = list(base_consumed) + list(already_masked)
            try:
                output = await detector.detect(content, metadata)
            except Exception as exc:
                # Keep code locations, not exception text, locals or source
                # lines: those may contain the scanned file's secrets.
                frames = [
                    f"{Path(frame.f_code.co_filename).name}:{line}:{frame.f_code.co_name}"
                    for frame, line in walk_tb(exc.__traceback__)
                ]
                logger.error(
                    "detector_crash detector=%s file=%r error_type=%s frames=%s",
                    detector.name, metadata.get("file_path"), type(exc).__name__,
                    " > ".join(frames[-8:]),
                )
                # A single detector layer crashing on one file must not take
                # down the whole batch (bkz. exporter.py'nin asyncio.gather
                # ile es zamanli tarattigi TUM dosyalar - bounded olmayan bir
                # istisna hepsini iptal ederdi). Diger katmanlarla taramaya
                # devam edilir (kismi kapsam tam kapsamdan iyidir), ama bu
                # dosya HER HALUKARDA karantinaya alinir (bkz. crashes alani
                # dokstringi) - never log exc's message: it ran over real
                # file content and could echo a fragment of it back.
                crashes.append(
                    f"{detector.name} detector katmani beklenmeyen bir hatayla durdu "
                    f"({type(exc).__name__}); bu dosya icin tarama kapsami guvenilir sayilamaz"
                )
                continue
            results.extend(output.results)
            already_masked.extend(output.already_masked_spans)
            errors.extend(output.errors)
            crashes.extend(output.crashes)

        results.sort(key=lambda r: (r.start is None, r.start or 0, r.end or 0))
        return DetectorOutput(results=results, already_masked_spans=already_masked, errors=errors, crashes=crashes)


class RuleBasedDetector:
    """Layer 1: current deterministic corporate dictionary/rule matching."""

    name = "dictionary"

    def __init__(self, rules: list[RuleSpec], runtime_params: dict[str, str] | None = None) -> None:
        self.rules = rules
        self.runtime_params = runtime_params or {}
        # Bir kere derlenir (regex compile + validator lookup), bu detector
        # yasadigi surece (bkz. mapping_service.build_orchestrator - run
        # basina bir kez kurulur) her dosyada yeniden kullanilir.
        self._compiled_rules = compile_rules(rules, self.runtime_params)

    # Derlenmis kurallari icerige uygulayip DetectionResult listesine cevirir.
    # async: gercek await YOK (CPU-bound, aninda doner) - sadece Detector
    # protokolunun tekdüze arayuzune uymak icin (bkz. protokol dokstring'i).
    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        matches, already_masked = find_matches_compiled(self._compiled_rules, content)
        return DetectorOutput(
            results=[
                DetectionResult(
                    deger=match.original_value,
                    tip=match.rule.category,
                    guven_seviyesi="yuksek",
                    kaynak_motor=self.name,
                    gerekce=f"rule={match.rule.rule_name}",
                    start=match.start,
                    end=match.end,
                    rule=match.rule,
                )
                for match in matches
            ],
            already_masked_spans=already_masked,
        )


# Katman-bagimsiz DetectionResult'i, rule_engine'in beklendigi Match tipine
# cevirir - orn. Presidio/LLM bulgularini placeholder uretim/mapping
# adimlarinda sozluk-katmani araclariyla ayni sekilde islemek icin.
def detection_to_match(result: DetectionResult) -> Match:
    if result.rule is None or result.start is None or result.end is None:
        raise ValueError("DetectionResult cannot be converted to Match without rule and span")
    return Match(
        rule=result.rule,
        original_value=result.deger,
        start=result.start,
        end=result.end,
        entity_type=result.tip,
        source_detector=result.kaynak_motor,
    )


# Turkce isimlendirme konvansiyonuna uymayan (Ingilizce) varlik tipi
# adlarinin placeholder metninde Turkce karsiligiyla gorunmesi icin - orn.
# Presidio'nun yerlesik "PERSON" kategorisi "mask_person_N" yerine
# "mask_personel_N" uretir.
_ENTITY_TYPE_TR_ALIASES = {
    "person": "personel",
}


# LLM'in urettigi bir varlik tipini gecerli bir placeholder on-ekine cevirir.
def placeholder_prefix_for_type(entity_type: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", entity_type.lower()).strip("_")
    if normalized.startswith("mask_"):
        normalized = normalized[len("mask_"):]
    if not normalized:
        normalized = "llm_bulgu"
    normalized = _ENTITY_TYPE_TR_ALIASES.get(normalized, normalized)
    if normalized[0].isdigit():
        normalized = "llm_" + normalized
    # Truncate the category, never the "mask_" marker reverse_text.PLACEHOLDER_RE depends on.
    return "mask_" + normalized[:45].rstrip("_")


# Katman 3 (LLM) tarafindan DB'de onceden tanimli olmayan bir varlik tipi
# bulundugunda, onu diger katmanlarla ayni sekilde islemek icin "sahte" (DB'siz)
# bir RuleSpec uretir.
def synthetic_llm_rule(entity_type: str) -> RuleSpec:
    return RuleSpec(
        id=None,  # type: ignore[arg-type]
        rule_name=f"llm:{entity_type}",
        category=entity_type,
        pattern_type="llm",
        regex_pattern=None,
        regex_flags=None,
        placeholder_prefix=placeholder_prefix_for_type(entity_type),
        priority=90,
        validator_name=None,
        description="LLM tarafindan dinamik olarak uretilen kurum/PII hassas veri tipi.",
    )
