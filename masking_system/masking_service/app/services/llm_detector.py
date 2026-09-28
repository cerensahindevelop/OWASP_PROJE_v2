"""LLM detector implementation for the shared detector interface."""

from __future__ import annotations

from pathlib import Path

from app.services.file_classifier import should_ignore_path, is_archive_filename


# DetectorOutput: DetectionOrchestrator'in beklerdigi ortak sonuc zarfi.
# find_llm_detections/LLMRecognitionError: asil vLLM cagrisini ve
# dogrulamayi yapan katman - bu dosya sadece detector arayuzune uydurur.
from app.services.detectors import DetectorOutput
from app.services.llm_recognizer import LLMRecognitionError, find_llm_detections


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
        try:
            return DetectorOutput(
                results=await find_llm_detections(
                    content, consumed, self.vllm_settings, metadata, self.extra_instructions
                )
            )
        except LLMRecognitionError as exc:
            detail = "LLM taramasi tamamlanamadi; dosya VALIDATION_FAILED"
            if file_path:
                detail += f" (dosya={file_path})"
            return DetectorOutput(errors=[f"{detail}: {exc}"])
