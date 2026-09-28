"""Yanlis kimlikle 'Geri Al' calistirma durumunu tespit eden tani katmani.

Kok neden: placeholder formati (<ENTITY_TIPI>_<SIRA_NO>) TASARIM GEREGI
sadece KENDI context'i icinde benzersizdir (bkz. ValueMapping.__table_args__
- uq_mapping_context_placeholder). Ayni metin (orn. "mask_secret_1"), FARKLI
iki context'te FARKLI iki orijinal degeri temsil edebilir - her ikisi de
kendi bagimsiz sayaciyla 1'den baslar. Bu, maskeleme sirasinda bir sorun
DEGILDIR (bir placeholder her zaman KENDI context'i icinde cozulur).

Ama kullanici "Geri Al" adiminda yanlis proje/sicil/branch girerse,
sistem GUVENLIK GEREGI dogru sekilde BASKA (yanlis) context'i bulur ve
SADECE o context'in eslemeleriyle dener (bkz. load_context_mappings -
"asla bos context'e sessizce dusme" degismezi). Kucuk sayili sayaclar
(orn. mask_*_1, mask_*_2) birden fazla context'te sik rastlandigindan,
YANLIS context bile bazi placeholder'lari TESADUFEN cozebilir - bu da
"az sayida cozuldu, buyuk cogunlugu cozulemedi" seklinde kafa karistirici,
sanki-kismi-basarili-gibi-goruken bir sonuc dogurur. Kullanici bunun bir
"sistem hatasi" mi yoksa "yanlis kimlik" mi oldugunu ham sayilardan ayirt
edemez.

IdentityMismatchAdvisor, cozulemeyen placeholder kumesini TUM DIGER
context'lerle karsilastirip en yuksek ortusmeye sahip olani bir ONERI
olarak sunar - hicbir DEGERI asla cozmez/tahmin etmez (guvenlik
degismezi boylece korunur), sadece "belki bu kimlikle tekrar deneyin"
diyebilecek bir tespit uretir.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

# MaskingContext/ValueMapping: onerilen (muhtemelen kastedilen) context'in
# bilgilerini okumak ve baska context'lerdeki eslemelerle karsilastirmak icin.
from app.db.models import MaskingContext, ValueMapping

# Tani calistirmak icin gereken asgari ornek boyutu - kucuk dosyalarda
# birkac tesadufi mask_prefix_N bicimli false-positive zaten beklenen/kabul
# edilen bir durumdur (bkz. unmasker.py modul dokstring'i); bu esigin
# altinda oneri uretmek gurultu yaratir, fayda saglamaz.
_MIN_SAMPLE_SIZE = 20

# Cozulemeyen kumenin en az bu oranini BASKA TEK BIR context aciklayabiliyorsa,
# bu artik tesaduften cok "yanlis kimlik kullanildi" belirtisidir.
_MIN_OVERLAP_RATIO = 0.5


@dataclass(frozen=True)
class IdentityMismatchSuggestion:
    """Cozulemeyen placeholder'larin BUYUK COGUNLUGUNU aciklayan, mevcut
    olandan FARKLI bir context'e isaret eden tespit. Deger ICERMEZ - sadece
    hangi kimlikle tekrar denenmesi gerekebilecegini gosterir."""

    project_name: str
    sicil_no: str
    branch_name: str
    matched_count: int
    unresolved_count: int

    # Cozulemeyen placeholder'larin ne kadarinin onerilen context ile aciklandigini (0-1 arasi) doner.
    @property
    def match_ratio(self) -> float:
        return self.matched_count / self.unresolved_count if self.unresolved_count else 0.0


class IdentityMismatchAdvisor:
    """Tek sorumluluk: cozulemeyen placeholder kumesine bakip, muhtemelen
    kastedilen (ama yanlislikla girilmeyen) context'i - varsa - oneri
    olarak dondurur. Hicbir yan etkisi yoktur (salt okunur sorgu)."""

    # DB oturumunu saklar - tum sorgular salt okunur.
    def __init__(self, db: Session) -> None:
        self._db = db

    # Cozulemeyen placeholder'lara bakip muhtemel dogru kimligi oneri olarak dondurur (yoksa None).
    def suggest(
        self, *, current_context_id: int, unresolved_tokens: list[str], total_found: int
    ) -> IdentityMismatchSuggestion | None:
        if total_found < _MIN_SAMPLE_SIZE or not unresolved_tokens:
            return None

        best_context_id, matched_count = self._best_matching_other_context(
            current_context_id, unresolved_tokens
        )
        if best_context_id is None:
            return None

        unresolved_count = len(unresolved_tokens)
        if matched_count / unresolved_count < _MIN_OVERLAP_RATIO:
            return None

        context = self._db.get(MaskingContext, best_context_id)
        if context is None:
            return None

        return IdentityMismatchSuggestion(
            project_name=context.project_name,
            sicil_no=context.sicil_no,
            branch_name=context.branch_name,
            matched_count=matched_count,
            unresolved_count=unresolved_count,
        )

    # Cozulemeyen token'lardan mevcut context DISINDA en cok esleseni bulan
    # context_id'yi ve kac tanesinin eslestigini (tek bir gruplu sorguyla) doner.
    def _best_matching_other_context(
        self, current_context_id: int, unresolved_tokens: list[str]
    ) -> tuple[int | None, int]:
        row = self._db.execute(
            select(ValueMapping.context_id, func.count().label("matched"))
            .where(
                ValueMapping.placeholder_value.in_(unresolved_tokens),
                ValueMapping.context_id != current_context_id,
                ValueMapping.run_id.is_(None),
            )
            .group_by(ValueMapping.context_id)
            .order_by(func.count().desc())
            .limit(1)
        ).first()
        if row is None:
            return None, 0
        return row[0], row[1]
