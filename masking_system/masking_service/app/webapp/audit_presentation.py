"""Ham audit kayitlarini kullanici ozeti ve okunabilir teknik ize cevirir."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.webapp.sensitive_breakdown import display_sensitive_rule_name
from app.webapp.time_display import format_turkey_time


_SCAN_POLICY_RE = re.compile(
    r"^scan_policy mode=(?P<mode>mask|scan_only) "
    r"presidio=(?P<presidio>True|False) llm=(?P<llm>True|False)$"
)
_COLLISION_RE = re.compile(
    r"\[tip=(?P<loser_type>[^ ]+) kaynak=(?P<loser_source>[^ ]+).*?\].*?"
    r"\[tip=(?P<winner_type>[^ ]+) kaynak=(?P<winner_source>[^ ]+).*?\]"
)

_ACTION_LABELS = {
    "matched": "Hassas bulgu tespit edildi",
    "replaced": "Değer maskelendi",
    "skipped": "Kontrol / atlama",
    "cakisma": "Çakışma güvenli biçimde çözüldü",
    "sinir_ihlali": "Token sınırı güvenlik kontrolü",
    "error": "İşlem hatası",
    "sozdizimi_hatasi": "Sözdizimi doğrulama hatası",
    "round_trip_hatasi": "Geri dönüş doğrulama hatası",
}
_SOURCE_LABELS = {
    "dictionary": "kural/kurumsal sözlük",
    "katman1": "regex ve sözlük katmanı",
    "katman2_presidio": "Presidio",
    "llm": "yapay zekâ",
    "consistency": "tutarlılık taraması",
}
_ENTITY_LABELS = {
    "PERSON": "Kişi",
    "LOCATION": "Konum",
    "ORGANIZATION": "Kurum / kuruluş",
    "DATE_TIME": "Tarih / saat",
    "URL": "URL",
    "IC_DOMAIN_ADI": "Kurum içi alan adı",
}


def _entity_label(value: str) -> str:
    return _ENTITY_LABELS.get(value, value.replace("_", " ").capitalize())


def _source_label(value: str) -> str:
    return _SOURCE_LABELS.get(value, value.replace("_", " "))


def _scan_policy_description(detail: str) -> tuple[str, str] | None:
    match = _SCAN_POLICY_RE.fullmatch(detail)
    if match is None:
        return None
    mode = "Salt okunur tarama" if match.group("mode") == "scan_only" else "Metin taraması"
    layers = ["regex/sözlük"]
    if match.group("presidio") == "True":
        layers.append("Presidio")
    if match.group("llm") == "True":
        layers.append("yapay zekâ")
    return mode, f"{mode}. Etkin katmanlar: {', '.join(layers)}."


def _technical_description(action: str, detail: str) -> tuple[str, str, str]:
    """Return ``(step, severity, description)`` for one raw audit event."""

    scan_policy = _scan_policy_description(detail)
    if scan_policy is not None:
        _mode, description = scan_policy
        return "Tarama planı oluşturuldu", "Bilgi", description

    if action == "matched" and detail.startswith("rule="):
        rule_name = detail.removeprefix("rule=").strip()
        return _ACTION_LABELS[action], "Başarılı", f"Tür: {display_sensitive_rule_name(rule_name)}."

    if action == "replaced" and detail.startswith("placeholder="):
        placeholder = detail.removeprefix("placeholder=").strip()
        placeholder_type = re.sub(r"_TEST_\d+$", "", placeholder).replace("_", " ").title()
        return _ACTION_LABELS[action], "Başarılı", f"Değer, {placeholder_type} türünde geri alınabilir bir yer tutucuyla değiştirildi."

    if action == "cakisma":
        match = _COLLISION_RE.search(detail)
        if match is not None:
            loser = _entity_label(match.group("loser_type"))
            winner = _entity_label(match.group("winner_type"))
            loser_source = _source_label(match.group("loser_source"))
            winner_source = _source_label(match.group("winner_source"))
            description = (
                f"{loser_source} tarafından bulunan “{loser}” adayı uygulanmadı; aynı metindeki "
                f"daha öncelikli “{winner}” bulgusu ({winner_source}) kullanıldı. Metin iki kez maskelenmedi."
            )
        else:
            description = "Aynı metne ait birden fazla bulgudan yalnızca daha güvenilir olanı uygulandı."
        return _ACTION_LABELS[action], "Bilgi", description

    if action == "sinir_ihlali":
        return _ACTION_LABELS[action], "Uyarı", f"Aday bulgu uygulanmadı: {detail.rstrip('.')}.”"

    if action == "skipped" and detail.startswith("validation_warning;"):
        return "Sözdizimi doğrulama uyarısı", "Uyarı", detail.split(";", 1)[1].strip()
    if action == "skipped" and "; skipped_unsupported; " in detail:
        return "Desteklenmeyen içerik", "Uyarı", detail.split("; skipped_unsupported; ", 1)[1]
    if action == "skipped":
        if detail.startswith("already placeholder-formatted"):
            description = "İçerik zaten sistem yer tutucusu biçimindeydi; yeniden maskelenmedi."
        elif "karantinaya" in detail.casefold():
            description = "Dosya son güvenlik kontrolünü geçemedi ve çıktı paketine alınmadı."
        else:
            description = detail or "Bu adım güvenli biçimde atlandı."
        return _ACTION_LABELS[action], "Uyarı", description

    severity = "Hata" if action in {"error", "sozdizimi_hatasi", "round_trip_hatasi"} else "Bilgi"
    return _ACTION_LABELS.get(action, action.replace("_", " ").capitalize()), severity, detail or "Ek açıklama yok."


@dataclass
class _MutableFileSummary:
    file_path: str
    scan_method: str = "—"
    findings: int = 0
    replacements: int = 0
    warnings: int = 0
    errors: int = 0
    unsupported: bool = False

    def as_table_row(self) -> dict[str, str | int]:
        if self.errors:
            result = "İnceleme gerekli"
        elif self.unsupported:
            result = "Desteklenmeyen içerik — çıktıya alınmadı"
        elif self.replacements:
            result = "Maskelendi"
        elif self.findings:
            result = "Bulgu bulundu"
        else:
            result = "Tarandı"
        return {
            "Dosya": self.file_path,
            "Tarama": self.scan_method,
            "Bulgu": self.findings,
            "Maskelenen": self.replacements,
            "Güvenlik notu": self.warnings,
            "Hata": self.errors,
            "Sonuç": result,
        }


def summarize_audit_entries(entries: Iterable[object]) -> list[dict[str, str | int]]:
    """Cok sayidaki event satirini dosya basina tek anlamli ozet satirina indirger."""

    summaries: dict[str, _MutableFileSummary] = {}
    for entry in entries:
        file_path = str(getattr(entry, "file_path", "") or "(genel işlem)")
        action = str(getattr(entry, "action", "") or "")
        detail = str(getattr(entry, "detail", "") or "")
        summary = summaries.setdefault(file_path, _MutableFileSummary(file_path=file_path))
        scan_policy = _scan_policy_description(detail)
        if scan_policy is not None:
            summary.scan_method = scan_policy[0]
        elif action == "matched":
            summary.findings += 1
        elif action == "replaced":
            summary.replacements += 1
        elif action == "skipped" and "; skipped_unsupported; " in detail:
            summary.unsupported = True
            summary.scan_method = "Taranmadı"
            summary.warnings += 1
        elif action in {"cakisma", "sinir_ihlali"} or detail.startswith("validation_warning;"):
            summary.warnings += 1
        elif action == "skipped" and "karantinaya" in detail.casefold():
            summary.errors += 1
        elif action in {"error", "sozdizimi_hatasi", "round_trip_hatasi"}:
            summary.errors += 1
    return [summary.as_table_row() for summary in summaries.values()]


def present_audit_entries(entries: Iterable[object]) -> list[dict[str, str]]:
    """Ham event listesini zaman sirali, Turkce teknik iz tablosuna cevirir."""

    rows: list[dict[str, str]] = []
    for entry in entries:
        action = str(getattr(entry, "action", "") or "")
        detail = str(getattr(entry, "detail", "") or "")
        step, severity, description = _technical_description(action, detail)
        created_at = getattr(entry, "created_at", None)
        rows.append(
            {
                "Zaman": format_turkey_time(created_at),
                "Dosya": str(getattr(entry, "file_path", "") or "(genel işlem)"),
                "Seviye": severity,
                "Adım": step,
                "Açıklama": description,
            }
        )
    return rows
