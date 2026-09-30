"""LLM detector implementation for the shared detector interface."""

from __future__ import annotations

from pathlib import Path

from app.services.file_classifier import should_ignore_path, is_archive_filename


# DetectorOutput: DetectionOrchestrator'in beklerdigi ortak sonuc zarfi.
# find_llm_detections/LLMRecognitionError: asil vLLM cagrisini ve
# dogrulamayi yapan katman - bu dosya sadece detector arayuzune uydurur.
from app.services.detectors import DetectorOutput
from app.services.llm_recognizer import FindingRepairStats, LLMRecognitionError, find_llm_detections
from app.services.token_boundary_validator import is_authoritative_result


# Katman 1'in (sozluk/regex/ogrenilmis karar) kesin bulgulari: LLM'e bu
# araliklar gecici yer tutucuyla gonderilir (bkz. llm_input_view). Presidio
# gibi olasiliksal bulgular DAHIL EDILMEZ - sonradan reddedilebilirler ve
# o durumda LLM'in degeri gormus olmasi gerekir.
def _known_spans(metadata: dict) -> list[tuple[int, int, str]]:
    return [
        (result.start, result.end, result.tip)
        for result in metadata.get("prior_results", ())
        if is_authoritative_result(result) and result.start is not None and result.end is not None
    ]


# Katman 3 (LLM tabanli) tespiti, DetectorRegistry'nin bekledigi ortak
# detector arayuzune (name + detect()) uyarlayan ince sarmalayici.
class LLMDetector:
    name = "llm"

    # vLLM baglanti ayarlarini (host/model/timeout) saklar. extra_instructions:
    # DB'deki pattern_type='llm' FilterRule satirlarinin (kural-ekle --aciklama
    # ile eklenen) tarama talimatlari - build_orchestrator() tarafindan doldurulur,
    # find_llm_detections() araciligiyla gercek vLLM promptuna eklenir.
    def __init__(self, vllm_settings, extra_instructions: list[str] | None = None) -> None:
        self.vllm_settings = vllm_settings
        self.extra_instructions = extra_instructions

    # Metni LLM ile tarar; LLM devre disiysa (enable_llm=False) veya
    # vLLM'e ulasilamazsa programi cokertmeden bos/hata sonucu doner.
    # async: tek gercek `await` noktasi burasi - find_llm_detections agdan
    # LLM sunucusuna gider (bkz. app/services/llm_recognizer.py modul
    # dokstring'i, vLLM coklu-istek gerekcesi).
    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        metadata = metadata or {}
        if metadata.get("enable_llm") is False:
            return DetectorOutput()
        file_path = metadata.get("file_path", "")
        if file_path:
            path = Path(str(file_path).replace("\\", "/"))
            if should_ignore_path(path)[0] or is_archive_filename(path.name):
                return DetectorOutput()
        if not content.strip():
            return DetectorOutput()
        consumed = list(metadata.get("consumed_spans", []))
        repair_stats = FindingRepairStats()
        try:
            results = await find_llm_detections(
                content, consumed, self.vllm_settings, metadata, self.extra_instructions,
                repair_stats=repair_stats, known_spans=_known_spans(metadata),
                blob_spans=metadata.get("encoded_blob_spans"),
            )
        except LLMRecognitionError as exc:
            detail = "LLM taramasi tamamlanamadi; dosya VALIDATION_FAILED"
            if file_path:
                detail += f" (dosya={file_path})"
            return DetectorOutput(errors=[f"{detail}: {exc}"])
        notices = []
        if repair_stats.repaired or repair_stats.dropped:
            notices.append(
                f"llm_bulgu_semasi_bozuk onarilan={repair_stats.repaired} atilan={repair_stats.dropped}"
            )
        return DetectorOutput(results=results, notices=notices)
