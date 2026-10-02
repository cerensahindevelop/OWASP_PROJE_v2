"""Short review summaries with locations verified against quarantined content."""

from __future__ import annotations

import ast
import re

from sqlalchemy.orm import Session

from app.db.models import AuditWarning
from app.services.audit_reviewer import resolve_audit_values
from app.services.term_upload import find_leaked_terms


def _line_excerpt(content: str, line_number: int, value: str = "") -> str:
    lines = content.splitlines()
    if line_number < 1 or line_number > len(lines):
        return ""
    line = lines[line_number - 1].strip()
    if value and value in line:
        line = line.replace(value, f"⟦{value}⟧", 1)
    if len(line) > 240:
        line = line[:237] + "…"
    return line


_RECORDED_TERM_RE = re.compile(
    r"Satır (\d+), sütun (\d+): [^\n]*?; açık değer=('(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\")"
)


def _recorded_term_values(reason: str) -> dict[tuple[int, int], str]:
    values: dict[tuple[int, int], str] = {}
    for line, column, literal in _RECORDED_TERM_RE.findall(reason):
        try:
            value = ast.literal_eval(literal)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str):
            values[(int(line), int(column))] = value
    return values


def describe_audit_warning(warning: AuditWarning, db: Session, *, evidence_limit: int | None = 6) -> dict[str, object]:
    reason = warning.reasoning or ""
    if warning.audit_failed:
        lowered = reason.casefold()
        location = "Dosya geneli — belirli bir satır tespit edilmedi."
        if "round-trip" in lowered or "geri dönüş" in lowered:
            summary = "Geri dönüş (round-trip) doğrulaması tamamlanamadı."
            if "karsilik bulunamadi" in lowered or "çözülemiyor" in lowered:
                summary = "Geri dönüş başarısız: yer tutucunun eşleme kaydı bulunamadı."
            elif "harf fark" in lowered:
                summary = "Geri dönüş başarısız: özgün büyük/küçük harf biçimi korunmadı."
            elif "uyusmuyor" in lowered:
                summary = "Geri dönüş başarısız: geri çözülen içerik kaynakla birebir eşleşmiyor."
            difference = re.search(r"(?:satir|satır) (\d+), (?:sutun|sütun) (\d+)", reason, re.IGNORECASE)
            if difference:
                location = (
                    f"İlk fark: orijinal (maskelenmemiş) kaynak dosyada satır {difference.group(1)}, "
                    f"sütun {difference.group(2)}."
                )
        elif "sözdizimi" in lowered or "sozdizimi" in lowered:
            summary = "Maskelenmiş dosyanın sözdizimi doğrulanamadı."
            # Only allow-listed metadata is shown. Parser messages can carry
            # sensitive source fragments, even when no excerpt was requested.
            parser = re.search(r"\b(Python|JSON|YAML|TOML) sozdizimi", reason)
            if parser:
                summary = f"Maskelenmiş dosya {parser.group(1)} sözdizimi kontrolünden geçemedi."
            position = re.search(r"(?:satir|satır) (\d+)(?:, (?:sutun|sütun) (\d+))?", reason, re.IGNORECASE)
            if position:
                location = f"Maskelenmiş dosyada satır {position.group(1)}"
                if position.group(2):
                    location += f", sütun {position.group(2)}"
                location += "."
        elif "boyut" in lowered:
            summary = "Dosya boyut sınırını aştığı için güvenlik taraması tamamlanamadı."
        elif "binary" in lowered or "metin dışı" in lowered:
            summary = "Binary/metin dışı dosyanın güvenlik taraması tamamlanamadı."
        elif "kodlama" in lowered or "encoding" in lowered:
            summary = "Dosya kodlaması çözülemediği için güvenlik taraması tamamlanamadı."
        elif "tutarlılık" in lowered or "consistency" in lowered:
            summary = "Final tutarlılık kontrolü tamamlanamadı."
        else:
            summary = "Yapay zekâ kontrolü tamamlanamadı; dosyanın güvenliği doğrulanamadı."
        return {
            "summary": summary,
            "location": location,
            "next_step": "Teknik nedeni giderip projeyi yeniden dışa aktarın.",
            "evidence": [],
        }

    if reason.startswith(("Kurumsal terim sozlugu son kontrolu", "Kurumsal terim kontrolü:")):
        locations = re.findall(r"Satır (\d+), sütun (\d+): ([^\n;]+)", reason)
        live_terms = find_leaked_terms(db, warning.masked_content) if db is not None else []
        basis = ""
        if not locations:
            # Legacy records contain only rule names. Resolve their locations
            # read-only, and label the use of today's active dictionary.
            locations = [
                (str(t.line_number), str(t.column_number), t.category)
                for t in live_terms
            ]
            basis = " (güncel sözlük kontrolü)"
        if not locations:
            return {
                "summary": "Önceki terim kontrolü dosyayı durdurmuş; güncel kontrolde açık terim bulunmadı.",
                "location": "Eski kayıtta satır bilgisi yok.",
                "next_step": "Düzeltmelerin tüm denetimlerden geçmesi için projeyi yeniden dışa aktarın.",
                "evidence": [],
            }
        unique = list(dict.fromkeys((line, column) for line, column, _ in locations))
        location = "; ".join(f"Satır {line}, sütun {column}" for line, column in unique[:6])
        if len(unique) > 6:
            location += f"; +{len(unique) - 6} konum"
        categories = list(dict.fromkeys(category.split(" (")[0].replace("_", " ") for _, _, category in locations))
        live_by_location = {(t.line_number, t.column_number): t for t in live_terms}
        recorded_values = _recorded_term_values(reason)
        evidence = []
        for line, column in unique[:evidence_limit]:
            line_no, column_no = int(line), int(column)
            term = live_by_location.get((line_no, column_no))
            # Canli sozluk degeri bulamazsa (terim sonradan silinmis/degismis)
            # export anindaki gerekcede kayitli deger gosterilir.
            value = term.matched_value if term is not None else recorded_values.get((line_no, column_no), "")
            evidence.append({
                "line": line_no,
                "column": column_no,
                "found_value": value,
                "excerpt": _line_excerpt(warning.masked_content, line_no, value),
            })
        return {
            "summary": "Maskelenmeden kalan kurumsal terim: " + ", ".join(categories[:4]) + ".",
            "location": location + basis,
            "next_step": "Maskeleme kurallarını kontrol edip projeyi yeniden dışa aktarın.",
            "evidence": evidence,
        }

    # Only report line numbers when the model's cited excerpt actually exists.
    # A cited variable name is shown as the clear value assigned to it.
    content = warning.masked_content
    evidence: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    names_without_value: list[str] = []
    for excerpt in re.findall(r"\(ilgili bolum: '(.*?)'\)", reason, re.DOTALL):
        if not excerpt:
            continue
        values = resolve_audit_values(content, excerpt)
        if not values and excerpt in content:
            names_without_value.append(excerpt)
        for value in values:
            offset = 0
            while (offset := content.find(value, offset)) >= 0:
                line_number = content.count("\n", 0, offset) + 1
                key = (line_number, value)
                if key not in seen:
                    evidence.append({
                        "line": line_number,
                        "column": offset - content.rfind("\n", 0, offset),
                        "found_value": value,
                        "excerpt": _line_excerpt(content, line_number, value),
                    })
                    seen.add(key)
                offset += len(value)
    if not evidence and names_without_value:
        names = ", ".join(dict.fromkeys(names_without_value))
        return {
            "summary": (
                f"Denetim yalnızca ad gösterdi ({names}); dosyada bu ada atanmış açık bir değer yok."
            ),
            "location": "Açık hassas değer bulunamadı.",
            "next_step": "Büyük olasılıkla yanlış alarm. 'Yanlış alarm — yeniden doğrula' ile dosyayı yeniden denetleyin.",
            "evidence": [],
        }
    summary = " ".join(reason.split(" (ilgili bolum:", 1)[0].split())
    return {
        "summary": summary[:180] + ("…" if len(summary) > 180 else "") if summary else "Denetim olası hassas bilgi bildirdi.",
        "location": ("; ".join(
            f"Satır {item['line']}, sütun {item['column']}" for item in evidence[:6]
        )) if evidence else "Konum belirlenemedi — denetim ayrıntısını inceleyin.",
        "next_step": "Bulguyu inceleyin. Risk gerçekse kuralları düzeltip yeniden dışa aktarın.",
        "evidence": evidence[:evidence_limit],
    }
