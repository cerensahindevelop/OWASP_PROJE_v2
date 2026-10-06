"""OverlapResolver'dan SONRA, placeholder metne uygulanmadan ONCE calisan
son kontrol katmani: bir DetectionResult'in (start, end) araliginin,
bir identifier'in (fonksiyon adi, metod cagrisi, degisken adi) ya da bir
string literal'in ORTASINDAN baslayip bitmedigini dogrular.

Iki ayri durum ele alinir:

1) Eslesme bir string literal (tirnak) ile HERHANGI bir sekilde
   kesisiyorsa (start illa tirnak icinde olmak zorunda degil - orn.
   Presidio'nun gercekte gozlemlenen `dbname="prod` gibi anahtar-
   kelime+tirnak+degeri TEK PARCA esleyip kapanis tirnagini disarida
   birakmasi), sonuc HER ZAMAN kendi hesapladigimiz tam ic icerige
   normallestirilir - tirnaklar ve varsa disarida kalan "anahtar-kelime="
   oneki, metinde OLDUGU GIBI (degistirilmeden) kalir; kismi degistirme
   YASAK. Eslesme, tirnak sinirindan MAKUL BIR MESAFENIN (varsayilan 200
   karakter) OTESINDE baslamis/bitmisse - tespitin bu string ile gercekten
   ilgili oldugu guvenilir sayilmaz - TAMAMEN REDDEDILIR.

   Istisna: kurumsal terim sozlugu eslesmesi (is_corporate_term_result)
   literal'in tamamini degil, icindeki tam token'i maskeler - SQL tablo
   adi, dosya yolu ve API yolu, kodda ve dosya adlarinda ayni token'in
   aldigi yer tutucuyu alir. Ayni literal'i baska bir bulgu tamamen
   maskeliyorsa ya da token bir kacis dizisine yapisiksa literal'in
   tamami maskelenir (bkz. _narrow_in_literal).

2)Eslesme tirnaksiz (bare) koddaysa: once en yakin identifier sinirina
   (\\b) genisletilir - bu, ayirici icermeyen camelCase/PascalCase/snake_case/
   UPPER_SNAKE_CASE identifier'larda (orn. `subeAdi`, `HedefSubeAdi`,
   `SUBE_ADI`) eslesmenin sadece bir PARCASI bulunmus olsa bile identifier'in
   TAMAMININ maskelenmesini saglar (harf buyuklugu/alt cizgi farki `_is_word_char`
   icin onemsizdir). Genisletilmis span'in ardindan bir "tirnaksiz kod ifadesi"
   (attribute erisimi orn. `redis.StrictRedis`, ya da fonksiyon/metod cagrisi
   orn. `requests.get(`) olup olmadigi kontrol edilir. Oyle ise sezgisel
   (Presidio/LLM) bulgular icin TAMAMEN REDDEDILIR - boyle bir span'i
   "genisletip maskelemek" hala gecersiz olurdu (orn. `app.route` bir URL
   degil, bir Flask decorator'idir; genisletilmis haliyle bile
   maskelenmemelidir). Istisna: Katman 1 (regex/sozluk - bkz.
   is_authoritative_result) gibi belirlenimli/kesin bir kaynagin eslesmesi
   TAM token'a genisletilip nokta/parantez ve komsu tokeni tuketmeden
   degistirilir - degisken/method/class/field/package/annotation/SQL
   identifier'lari (`schema.table`, `namespace.member`, `getSubeAdi()`)
   qualified-name/method-call baglaminda dahi guvenle maskeler.

   Kritik tasarim notu: "nokta + identifier" kontrolu SADECE eslesmenin
   HEMEN DISINDAKI karaktere bakar, eslesmenin ICINDEKI noktalara
   BAKMAZ - aksi halde `ayse.yilmaz@example.com` gibi gercek bir e-posta
   (icinde nokta var) ya da `192.168.10.55` gibi bir IP (rakamla baslayan
   segmentler) yanlislikla reddedilirdi. "Identifier" tanimi da ozellikle
   HARF/ALT-CIZGIYLE baslamayi sart kosar (rakamla degil) - bu, IP
   adreslerinin (192, 168 gibi rakamla baslayan segmentler) bare-kod
   kontrolunu hic tetiklememesini saglar.

3) Eslesme bir YORUM (comment - satir ya da blok) icindeyse: yorum kod
   DEGILDIR, bu yuzden (2)'deki bare-kod/parantez kontrolleri uygulanmaz.
   Eslesme bir kelimenin tamamiysa ya da arkasinda yalnizca kucuk harfli
   bir Turkce ek varsa ("VEGAdan") span aynen maskelenir; aksi halde
   ("VEGAService", "VEGA2", "sube_adi") geri cozulebilmesi icin kelimenin
   tamami maskelenir (bkz. StringLiteralIndex.in_comment).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

# DetectionResult: OverlapResolver'dan gelen, sinir dogrulamasi yapilacak/
# gerekirse genisletilecek/reddedilecek tek bir tespiti temsil eden veri tipi.
from app.services.detectors import DetectionResult
# Reused for every finding in this document, including authoritative fallback.
from app.services.string_literal_index import StringLiteralIndex
from app.services.rule_engine import TR_LOWER_CLASS
from app.services.placeholder_policy import is_corporate_rule

_DEFAULT_MAX_EXPANSION = 200
_BRACKET_CLOSERS = {")": "(", "}": "{", "]": "["}
_BRACKET_OPENERS = set(_BRACKET_CLOSERS.values())
# rule_engine.reverse_text'in placeholder arkasinda kabul ettigi ek bicimi.
_TR_SUFFIX_REMAINDER_RE = re.compile(rf"[{TR_LOWER_CLASS}]+")


# Sinir dogrulamasindan gecemeyip TAMAMEN reddedilen bir tespiti, ret
# gerekcesiyle birlikte tasir (maskeleme uygulanmaz).
@dataclass(frozen=True)
class BoundaryRejection:
    result: DetectionResult
    reason: str


_ESCAPE_SEQUENCE_RE = re.compile(r"\\.")

# Verilen karakterin bir identifier/kelime karakteri (harf/rakam/alt cizgi) olup olmadigini bildirir.
def _is_word_char(ch: str | None) -> bool:
    return ch is not None and (ch == "_" or ch.isalnum())


# Token gercek bir identifier gibi mi gorunuyor (harf/alt-cizgiyle basliyor, rakamla degil)?
def _looks_like_identifier(token: str) -> bool:
    return bool(token) and (token[0].isalpha() or token[0] == "_")


def is_corporate_term_result(result: DetectionResult) -> bool:
    """Kurumsal terim sozlugu eslesmesi mi (proje/sicil/branch ya da regex degil)?

    Bir string literal icinde yalnizca bu eslesmeler kendi token'ina
    daraltilir (bkz. TokenBoundaryValidator._narrow_in_literal): kurum adi
    hassas olan KELIMEDIR, cevresindeki SQL/yol/cumle degil. Parametrik ve
    regex eslesmeleri tum literal'i maskelemeye devam eder - "test-2024-gizli"
    gibi bir parolada proje adi "test" yalnizca bir parcadir.
    """
    return (
        result.kaynak_motor == "dictionary"
        and result.rule is not None
        and is_corporate_rule(result.rule.rule_name)
    )


def is_authoritative_result(result: DetectionResult) -> bool:
    """Return whether a deterministic match may mask a whole code identifier.

    Katman 1 (regex/sozluk - RuleBasedDetector/LearnedSensitiveDetector,
    kaynak_motor="dictionary") tespitleri kesin/belirlenimlidir: kural DB'de
    acikca tanimli, deger bir tahmin degil. Boyle bir eslesme bir identifier'in
    (degisken/method/class/field/package/annotation/SQL identifier) SADECE bir
    PARCASIYSA, kismi degil TAM identifier'i maskelemek gerekir - aksi halde
    `subeAdi`, `HedefSubeAdi`, `SUBE_ADI` gibi camelCase/PascalCase/UPPER_SNAKE
    yapilarda kismi maskeleme syntax/semantic bozulmasi yaratir. Bunun icin
    identifier'i TAM token sinirina genisletip nokta/parantez komsulugunu
    (attribute erisimi/method cagrisi/qualified name) TUKETMEDEN degistirmek
    guvenlidir - `schema.table`, `namespace.member`, `getSubeAdi()` gibi
    yapilarin cevresindeki sozdizimini bozmaz.

    Presidio/LLM (istatistiksel/olasiliksal tahmin) matches bu yetkiye sahip
    DEGIL - onlar icin daha siki bare-kod reddetme politikasi gecerli kalir
    (bkz. modul docstring'i, madde 2).
    """
    return result.kaynak_motor == "dictionary"


# pos konumundan geriye dogru, kelime karakteri oldugu surece genisleyip onceki tam token'i doner.
def _preceding_token(text: str, pos: int) -> str:
    end = pos
    start = end
    while start > 0 and _is_word_char(text[start - 1]):
        start -= 1
    return text[start:end]


# pos konumundan ileriye dogru, kelime karakteri oldugu surece genisleyip sonraki tam token'i doner.
def _following_token(text: str, pos: int) -> str:
    start = pos
    end = start
    while end < len(text) and _is_word_char(text[end]):
        end += 1
    return text[start:end]


# Span, bir fonksiyon cagrisinin/attribute erisiminin (orn. "requests.get(") parcasi mi?
def _is_bare_code_expression(text: str, start: int, end: int) -> bool:
    if end < len(text) and text[end] == "(":
        return True
    if start > 0 and text[start - 1] == ".":
        if _looks_like_identifier(_preceding_token(text, start - 1)):
            return True
    if end < len(text) and text[end] == ".":
        if _looks_like_identifier(_following_token(text, end + 1)):
            return True
    return False


# Span'in icinde kapanmamis/eslesmemis parantez var mi? (varsa placeholder sozdizimini bozar)
def _has_unbalanced_brackets(text: str, start: int, end: int) -> bool:
    stack: list[str] = []
    for ch in text[start:end]:
        if ch in _BRACKET_OPENERS:
            stack.append(ch)
        elif ch in _BRACKET_CLOSERS:
            expected = _BRACKET_CLOSERS[ch]
            if not stack or stack[-1] != expected:
                return True
            stack.pop()
    return bool(stack)


# Verilen span'i, her iki yonde de en yakin identifier/token sinirina kadar
# genisletir; genisleme max_expansion'i asarsa (None, None) doner (reddedilir).
def _expand_to_token_boundary(
    text: str, start: int, end: int, max_expansion: int
) -> tuple[int, int] | tuple[None, None]:
    new_start = start
    while new_start > 0 and _is_word_char(text[new_start - 1]):
        if start - new_start >= max_expansion:
            return None, None
        new_start -= 1
    new_end = end
    while new_end < len(text) and _is_word_char(text[new_end]):
        if new_end - end >= max_expansion:
            return None, None
        new_end += 1
    return new_start, new_end


# Span'i saran tirnak ciftini bulur (ic_bas, ic_son, tirnak_bas, tirnak_son).
# Birden fazla tirnak cifti kesisiyorsa (orn. JSON'da tirnakli anahtar +
# tirnakli deger) EN SAGDAKI (degerin) cift tercih edilir. Bulunamazsa None.
def _find_enclosing_string_literal(text: str, start: int, end: int) -> tuple[int, int, int, int] | None:
    return StringLiteralIndex(text).enclosing(start, end)


class TokenBoundaryValidator:
    """OverlapResolver'in kazananlarini alir, her birinin span'inin
    identifier/string-literal sinirlarina uygun oldugunu dogrular; gerekirse
    genisletir/daraltir, mantiksiz durumlarda tamamen reddeder."""

    # max_expansion: bir tespitin sinira genisletilebilecegi azami karakter sayisi.
    def __init__(self, max_expansion: int = _DEFAULT_MAX_EXPANSION) -> None:
        self._max_expansion = max_expansion

    # Her tespiti tek tek dogrular; kabul edilenleri ve reddedilenleri ayri listeler halinde doner.
    def validate(
        self, text: str, results: list[DetectionResult], *, file_path: str = ""
    ) -> tuple[list[DetectionResult], list[BoundaryRejection]]:
        accepted: list[DetectionResult] = []
        rejections: list[BoundaryRejection] = []

        if not results:
            return accepted, rejections
        string_index = StringLiteralIndex(text, file_path)
        # accepted icindeki sira -> kendi token'ina daraltilan kurumsal
        # terimin literal ic sinirlari (bkz. asagidaki son gecis).
        narrowed: dict[int, tuple[int, int]] = {}
        for result in results:
            if result.start is None or result.end is None:
                rejections.append(BoundaryRejection(result=result, reason="eksik offset araligi"))
                continue

            span, reason = self._validate_one(text, result.start, result.end, string_index)
            if span is None:
                # ``foo.bar`` is not universally an object attribute: SQL,
                # config/query languages and many unknown text formats use
                # the same separator for qualified identifiers.  A trusted,
                # full-token replacement can be applied in place without
                # consuming either neighbour or changing the punctuation.
                if is_authoritative_result(result):
                    # A dictionary term can be part of T_TERM or TermService.
                    # Replace the complete identifier, preserving separators
                    # and calls, so the placeholder remains reversible.
                    start, end = _expand_to_token_boundary(
                        text, result.start, result.end, self._max_expansion
                    ) if 0 <= result.start < result.end <= len(text) else (None, None)
                    if start is not None and end is not None and self.is_exact_span_allowed(
                        text, start, end, allow_bare_code_expression=True, string_index=string_index,
                    ):
                        accepted.append(replace(result, start=start, end=end, deger=text[start:end]))
                        continue
                rejections.append(BoundaryRejection(result=result, reason=reason or "bilinmeyen sinir ihlali"))
                continue

            new_start, new_end = span
            if (
                not is_authoritative_result(result)
                and "\n" in text[new_start:new_end]
                and "\n" not in text[result.start:result.end]
            ):
                # Tahmine dayali tek satirlik bir bulgu (orn. SQL icindeki bir
                # kolon adi) cok satirli bir string'in tamamina genisletilmez:
                # butun sorgu/blok tek yer tutucu olur, dosya okunamaz hale gelir.
                rejections.append(BoundaryRejection(
                    result=result, reason="tek satirlik bulgu cok satirli string'in tamamina genisletilmez",
                ))
                continue
            if is_corporate_term_result(result):
                bounds = string_index.enclosing(result.start, result.end)
                if bounds is not None and (new_start, new_end) == bounds[:2]:
                    token = self._narrow_in_literal(text, result.start, result.end, bounds[0], bounds[1])
                    if token is not None:
                        narrowed[len(accepted)] = bounds[:2]
                        new_start, new_end = token
            if (new_start, new_end) == (result.start, result.end) and result.deger == text[new_start:new_end]:
                accepted.append(result)
            else:
                # Span degistiyse `deger` de MUTLAKA yeni span'e gore
                # guncellenmeli - aksi halde DB'ye kaydedilecek orijinal
                # deger, metinde gercekte maskelenen span ile uyusmaz ve
                # unmask sirasinda yanlis deger geri donerdi.
                accepted.append(replace(result, start=new_start, end=new_end, deger=text[new_start:new_end]))

        # Ayni literal'i baska bir bulgu (parola regex'i, proje adi, LLM)
        # TAMAMEN maskeliyorsa daraltma geri alinir: aksi halde ikinci
        # cakisma cozumunde kurumsal terim (yuksek otorite) kazanir, tum
        # literal'i isteyen bulgu kaybeder ve degerin geri kalani acikta kalir.
        if narrowed:
            whole_spans = [
                (r.start, r.end) for i, r in enumerate(accepted) if i not in narrowed
            ]
            for i, (content_start, content_end) in narrowed.items():
                if any(s <= content_start and content_end <= e for s, e in whole_spans):
                    accepted[i] = replace(
                        accepted[i], start=content_start, end=content_end,
                        deger=text[content_start:content_end],
                    )

        # Son guvence: sinira genisletme/daraltma sonucu yalnizca noktalama
        # kalan bir span (orn. ayristirilamayan bir string'den `]}\n`) hicbir
        # hassas degeri temsil etmez; maskelenirse kodu bozar. Kacis dizileri
        # (\n, \t) harf sayilmaz.
        kept: list[DetectionResult] = []
        for result in accepted:
            if any(char.isalnum() for char in _ESCAPE_SEQUENCE_RE.sub("", text[result.start:result.end])):
                kept.append(result)
            else:
                rejections.append(BoundaryRejection(result=result, reason="harf/rakam icermeyen deger maskelenmez"))
        return kept, rejections

    # Kurumsal terimi literal'in tamami yerine icindeki tam token'a (ayni
    # kelime karakteri kurali: harf/rakam/alt cizgi) daraltir:
    # "SELECT * FROM tsk_bakim" -> "SELECT * FROM mask_x_1". Token, kodda ayni
    # identifier'in aldigi yer tutucuyu alir; yol/SQL/API referanslari
    # dosya adlari ve tablo tanimlariyla tutarli kalir. Token bir kacis
    # dizisine yapisiksa ("\nTSK" -> "nTSK") yer tutucu "\mask..." gibi
    # gecersiz bir kacis uretebilir ve geri cozumde \b bulunmaz; o durumda
    # None doner, literal'in tamami maskelenir.
    def _narrow_in_literal(
        self, text: str, start: int, end: int, content_start: int, content_end: int,
    ) -> tuple[int, int] | None:
        start, end = max(start, content_start), min(end, content_end)
        if start >= end:
            return None
        while start > content_start and _is_word_char(text[start - 1]):
            start -= 1
        while end < content_end and _is_word_char(text[end]):
            end += 1
        if start > content_start and text[start - 1] == "\\":
            return None
        return start, end

    def is_exact_span_allowed(
        self,
        text: str,
        start: int,
        end: int,
        *,
        allow_bare_code_expression: bool = False,
        string_index: StringLiteralIndex | None = None,
    ) -> bool:
        """Validate a known-sensitive exact occurrence without expanding it.

        Consistency masking must never turn a confirmed short value into a
        larger identifier/string replacement. Quoted occurrences are allowed
        in-place; word-internal spans are always rejected. Callers may allow a
        complete token component in a bare/qualified expression when the
        value already has an authoritative sensitive-value provenance.
        """
        if start < 0 or end > len(text) or start >= end:
            return False
        index = string_index or StringLiteralIndex(text)
        string_bounds = index.enclosing(start, end)
        if string_bounds is not None:
            content_start, content_end, _quote_start, _quote_end = string_bounds
            return content_start <= start < end <= content_end

        new_start, new_end = _expand_to_token_boundary(text, start, end, self._max_expansion)
        if (new_start, new_end) != (start, end):
            return False
        # Yorum icindeki bir tam-token span'i, kod-sozdizimi kontrollerine
        # (bare-kod ifadesi/dengesiz parantez - ikisi de gercek kodu bozmama
        # amacli) tabi degildir; yorumlarda "bozulacak sozdizimi" yoktur.
        if index.in_comment(start, end):
            return True
        if not allow_bare_code_expression and _is_bare_code_expression(text, start, end):
            return False
        if _has_unbalanced_brackets(text, start, end):
            return False
        return True

    # Tek bir span'i dogrular/duzeltir; gecersizse (None, gerekce) doner.
    def _validate_one(
        self,
        text: str,
        start: int,
        end: int,
        string_index: StringLiteralIndex | None = None,
    ) -> tuple[tuple[int, int] | None, str | None]:
        if start < 0 or end > len(text) or start >= end:
            return None, "gecersiz offset araligi"

        string_bounds = (string_index or StringLiteralIndex(text)).enclosing(start, end)
        if string_bounds is not None:
            content_start, content_end, quote_start, quote_end = string_bounds
            if content_start >= content_end:
                return None, "string icerigi bos/gecersiz sinirlar"
            # Eslesme tirnak sinirlarinin COK disinda (makul bir mesafenin
            # otesinde) baslamis/bitmisse, tespitin bu string ile gercekten
            # ilgili oldugu guvenilir degildir - reddet. Ancak MAKUL bir
            # mesafede disariya tasan (orn. Presidio'nun `dbname="prod`
            # gibi anahtar-kelime+tirnak+degeri TEK bir span olarak
            # esleyip kapanis tirnagini disarida biraktigi GERCEK,
            # gozlemlenen durumlar icin) taskin, KENDI HESAPLADIGIMIZ
            # (content_start, content_end) sinirlarina guvenle
            # normallestirilir - "keyword=" oneki/tirnaklar orijinal
            # metinde OLDUGU GIBI kalir, sadece ic deger degistirilir.
            if (quote_start - start) > self._max_expansion or (end - quote_end) > self._max_expansion:
                return None, "string literal sinirindan makul mesafenin otesinde bir span - guvenilmez"
            # Kismi degistirme YASAK: eslesme tirnaklari da kapsasa
            # (daraltilir), ic icerigin sadece bir ALT-KUMESI olsa
            # (genisletilir) ya da tirnak disina tasan bir anahtar-kelime
            # onekini icerse (daraltilir) fark etmez - sonuc HER ZAMAN
            # tirnak ici DEGERIN TAMAMI olur, asla kismi bir parca degil.
            return (content_start, content_end), None

        # Yorumlar (comment) kod DEGILDIR - bare-kod/dengesiz-parantez
        # kontrolleri ONLARA uygulanmaz. Span yorumun TAMAMEN icindeyse
        # (kismi kesisim asagidaki normal bare-kod yoluna dusmeye devam
        # eder - gercekci bir detector span'i boyle bolmez).
        if string_index is not None and string_index.in_comment(start, end):
            token_start, token_end = _expand_to_token_boundary(text, start, end, self._max_expansion)
            if token_start is None or token_end is None:
                return None, "yorumdaki bulgu, kelime sinirina makul mesafede genisletilemedi"
            # Token'a yapisik kalan bir kelime parcasi geri cozulemez:
            # reverse_text \b ister ve token arkasinda SADECE kucuk harfli bir
            # Turkce ek kabul eder ("mask_x_1dan"). "eskiVEGA", "VEGAService",
            # "VEGA2", "VEGAOMEGA", "sube_adi" gibi durumlarda yalnizca terim
            # maskelenirse round-trip dosyayi engeller (ve VEGAOMEGA'da OMEGA
            # acikta kalir) - tum kelime tek deger olarak maskelenir.
            suffix = text[end:token_end]
            if token_start == start and (not suffix or _TR_SUFFIX_REMAINDER_RE.fullmatch(suffix)):
                return (start, end), None
            return (token_start, token_end), None

        new_start, new_end = _expand_to_token_boundary(text, start, end, self._max_expansion)
        if new_start is None or new_end is None:
            return None, "identifier/token sinirina makul mesafede genisletilemedi"

        if _is_bare_code_expression(text, new_start, new_end):
            return None, "bulgu, tirnaksiz bir kod ifadesinin (attribute erisimi/fonksiyon cagrisi) parcasi"

        if _has_unbalanced_brackets(text, new_start, new_end):
            return None, "bulgu, ICINDE dengesiz parantez/suslu/kose parantez iceriyor - bir kod ifadesinin ortasindan kesilmis olabilir"

        return (new_start, new_end), None
