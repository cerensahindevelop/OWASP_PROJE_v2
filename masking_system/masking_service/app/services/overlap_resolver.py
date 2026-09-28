"""Katman 1/2/3'ten gelen DetectionResult'lari, metne uygulanmadan (ve
eslesme_kayitlari'na yazilmadan) ONCE cakismalarini cozen saf (pure)
mantik. rule_engine.py'nin felsefesiyle ayni: DB/metin erisimi yok,
sadece (start, end, guven_seviyesi, kaynak_motor) bilgisine bakar - bu
yuzden trivially unit test edilebilir.

Oncelik sirasi:
  1) Otorite: DB-onayli kurumsal terim/alias > runtime kimligi
     (proje/sicil/branch) > ozel deterministik regex > genel yakalama
     regex'i > sezgisel detector.
     Presidio ve LLM ayni sezgisel gruptedir; aksi halde kisa/dusuk-guvenli
     bir Presidio bulgusu uzun/yuksek-guvenli LLM bulgusunu kesip parca
     sizintisi yaratabilir.
  2) Ayni otoritede yuksek confidence kazanir (yuksek > orta > dusuk).
  3) Esitse, DAHA UZUN araligi kapsayan bulgu kazanir.
  4) Esitse, daha yuksek kural onceligi (daha KUCUK priority sayisi) kazanir.
  5) Esitse, kaynak onceligi (Presidio > LLM) ve son olarak stabil giris
     sirasi kullanilir.

Kaybeden bulgular METNE UYGULANMAZ ve sessizce silinmez - her biri bir
OverlapConflict olarak dondurulur; cagiran taraf (mapping_service.py) bunu
denetim_kaydi'na yazar. ONEMLI: OverlapConflict.reason ASLA ham/gercek
degeri (DetectionResult.deger) icermemelidir - denetim_kaydi.detay
sifrelenmemis bir kolon, gercek secret'i oraya duz metin yazmak yeni bir
sizinti olur.
"""

from __future__ import annotations

from dataclasses import dataclass

# DetectionResult: cozulecek/siralanacak tespit kayitlarinin ortak tipi.
from app.services.detectors import DetectionResult

_CONFIDENCE_RANK: dict[str, int] = {"yuksek": 2, "orta": 1, "dusuk": 0}

# "dictionary" = Katman 1, "katman2_presidio" = Katman 2, "llm" = Katman 3
# (bkz. RuleBasedDetector.name / PresidioDetector.name / LLMDetector.name)
_SOURCE_RANK: dict[str, int] = {"dictionary": 2, "katman2_presidio": 1, "llm": 0}

_CORPORATE_TERM_RULE_PREFIX = "kurumsal_terim_"
_CORPORATE_ALIAS_RULE_PREFIX = "kurumsal_alias_"
_GENERIC_RULE_PREFIX = "generic_"

_AUTHORITY_RANK = {
    "corporate": 5,
    "corporate_alias": 4,
    "runtime": 3,
    "deterministic_regex": 3,
    "generic_regex": 2,
    "heuristic": 1,
}


# Cakismada kaybeden bir bulguyu, onu yenen (winner) bulguyla birlikte
# tutan kayit - denetim_kaydi'na yazilmak uzere mapping_service'e dondurulur.
@dataclass(frozen=True)
class OverlapConflict:
    winner: DetectionResult
    loser: DetectionResult
    reason: str


# Bir bulgunun kapladigi karakter araligi uzunlugunu hesaplar (oncelik siralamasinin 2. kriteri).
def _length(result: DetectionResult) -> int:
    if result.start is None or result.end is None:
        return 0
    return result.end - result.start


# Bir bulgunun kullanici-onayli/deterministik/sezgisel otorite seviyesini
# hesaplar. Mevcut kurumsal terim yukleme akisi ana terim ve onayli alias'i
# ayni `kurumsal_terim_` ailesinde saklar; bunlar esit otoritede olup daha
# uzun span ile ayrisir. Gelecekte ayri alias kaydi kullanilabilmesi icin
# `kurumsal_alias_` ad ailesi de desteklenir.
def _authority(result: DetectionResult) -> int:
    rule = result.rule
    if result.kaynak_motor == "dictionary":
        if rule is not None and rule.rule_name.startswith(_CORPORATE_ALIAS_RULE_PREFIX):
            return _AUTHORITY_RANK["corporate_alias"]
        if rule is not None and rule.rule_name.startswith(_CORPORATE_TERM_RULE_PREFIX):
            return _AUTHORITY_RANK["corporate"]
        if rule is not None and rule.pattern_type == "parametric":
            return _AUTHORITY_RANK["runtime"]
        # `generic_secret_assignment` gibi catch-all kurallar, belirli bir
        # Google/GitHub/JWT formatiyla ayni degeri yakaladiginda tum atama
        # ifadesini kapsadigi icin daha uzundur. Salt "en uzun" kriteri bu
        # genel kurali kazandirip ozel placeholder turunu kaybettirir. Genel
        # kurallar bu nedenle deterministik kalir, ancak ozel format
        # kurallarinin bir alt otorite basamaginda cozulur.
        if rule is not None and rule.rule_name.startswith(_GENERIC_RULE_PREFIX):
            return _AUTHORITY_RANK["generic_regex"]
        return _AUTHORITY_RANK["deterministic_regex"]
    if result.kaynak_motor == "katman2_presidio":
        return _AUTHORITY_RANK["heuristic"]
    if result.kaynak_motor == "llm":
        return _AUTHORITY_RANK["heuristic"]
    return 0


def _rule_priority(result: DetectionResult) -> int:
    return result.rule.priority if result.rule is not None else 100


# Reverse sort kullanildigi icin daha kucuk DB priority degeri `-priority`
# ile daha buyuk siralama anahtarina donusturulur.
def _priority_key(result: DetectionResult) -> tuple[int, int, int, int, int]:
    return (
        _authority(result),
        _CONFIDENCE_RANK.get(result.guven_seviyesi, -1),
        _length(result),
        -_rule_priority(result),
        _SOURCE_RANK.get(result.kaynak_motor, -1),
    )


# Iki (start, end) araliginin karakter bazinda kesisip kesismedigini kontrol eder.
def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    a_start, a_end = a
    b_start, b_end = b
    return not (a_end <= b_start or a_start >= b_end)


# Bir bulguyu, gercek degerini SIZDIRMADAN denetim_kaydi mesajlarinda kullanilabilecek kisa metne cevirir.
def _describe(result: DetectionResult) -> str:
    return f"tip={result.tip} kaynak={result.kaynak_motor} guven={result.guven_seviyesi} offset=({result.start},{result.end})"


# Katmanlar arasi cakisma cozumunun tek genel giris noktasi (bkz. modul dokstring'i).
class OverlapResolver:
    """Bir dosyanin TUM DetectionResult'larini alir, cakisan araliklardan
    SADECE en yuksek oncelikli olani kabul eder, digerlerini kaybeden
    (conflict) olarak raporlar."""

    # En yuksek oncelikli bulgulari kabul eder, cakisan dusuk oncelikli
    # bulgulari OverlapConflict olarak toplayip (kabul_edilenler, cakismalar) dondurur.
    def resolve(self, results: list[DetectionResult]) -> tuple[list[DetectionResult], list[OverlapConflict]]:
        positioned = [r for r in results if r.start is not None and r.end is not None]
        unpositioned = [r for r in results if r.start is None or r.end is None]

        # En yuksek oncelikliden en dusuge dogru isle; Python'un sort'u
        # STABLE oldugu icin tam esitliklerde orijinal sira korunur.
        ordered = sorted(positioned, key=_priority_key, reverse=True)

        accepted: list[DetectionResult] = []
        accepted_spans: list[tuple[int, int]] = []
        conflicts: list[OverlapConflict] = []

        for candidate in ordered:
            span = (candidate.start, candidate.end)  # type: ignore[arg-type]
            winner_idx = next(
                (i for i, existing_span in enumerate(accepted_spans) if _spans_overlap(span, existing_span)),
                None,
            )
            if winner_idx is None:
                accepted.append(candidate)
                accepted_spans.append(span)
                continue

            winner = accepted[winner_idx]
            conflicts.append(
                OverlapConflict(
                    winner=winner,
                    loser=candidate,
                    reason=f"cakisma: [{_describe(candidate)}] bulgusu, [{_describe(winner)}] ile ortustugu icin uygulanmadi",
                )
            )

        accepted.sort(key=lambda r: r.start)  # type: ignore[arg-type,return-value]
        return unpositioned + accepted, conflicts
