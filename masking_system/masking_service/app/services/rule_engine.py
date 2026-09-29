"""Pure matching logic for the rule engine.

No DB access happens here on purpose: this module only knows how to turn a
list of rule definitions + a runtime parameter dict into a list of
non-overlapping matches in a piece of text. That keeps it trivially unit
testable and reusable both by the DB-backed mapping service and by CLI/dry
run tooling.

Three rule natures are handled (see FilterRule model docstring for the
rationale):
  - 'regex'      -> regex_pattern is compiled as-is against the text. May
                    optionally wrap the sensitive part in a `(?P<deger>...)`
                    named group when it needs to match a fixed WRAPPER
                    around the value (e.g. an XML `<password>...</password>`
                    element) without replacing the wrapper itself - see
                    find_matches_compiled(), which then narrows the match
                    span to just that inner group so only the value (never
                    the enclosing tag) is cut out and placeholder-replaced.
                    Rules without this group keep replacing the WHOLE match,
                    as before.
  - 'parametric' -> the concrete value is pulled from `runtime_params` by
                    `category` (e.g. category='project_name' ->
                    runtime_params['project_name']) and matched as an
                    escaped literal with word-boundary-like guards. If the
                    caller didn't supply a value for that category, the
                    rule is silently skipped for this run (nothing to
                    search for).
  - 'llm'        -> has no fixed format for a regex to express (a person's
                    name, a home address). build_pattern() returns None for
                    it here - same as an unsupplied parametric rule - so
                    this module's own loop silently skips it. Matching
                    happens in a wholly separate pass
                    (app.services.llm_recognizer.find_llm_detections /
                    app.services.llm_detector.LLMDetector), which produces
                    DetectionResult objects the caller merges in afterwards;
                    this module never talks to the LLM.

A 'regex' rule may optionally carry a `validator_name`: after the regex
finds a candidate, the named function from app.services.validators is run
against the matched text, and the match is discarded if it fails. This is
for formats with a real checksum (TC kimlik no, IBAN, ...) where a bare
regex would false-positive on any digit string of the right length/shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# VALIDATORS: regex ile yakalanan bir degerin gercekten formata uydugunu
# (checksum vb.) dogrulamak icin kullanilan adlandirilmis fonksiyon sozlugu.
from app.services.validators import VALIDATORS
# chunk_text: buyuk metinleri overlap'li parcalara bolen ortak yardimci -
# presidio_detector.py (Katman 2) ile PAYLASILIR. Katman 1 (bu modul)
# onceden dosyanin TAMAMINI tek parca olarak her kurala karsi tarardi;
# Katman 2/3 zaten kendi boyut tavanlariyla (200k/20k karakter) chunk'liyordu
# - bu tutarsizlik hem cok buyuk dosyalarda tek bir regex cagrisinin sinirsiz
# buyuklukte girdi almasina (kotu/pathological bir regex'in tek cagrida
# katastrofik geri izlemeye - ReDoS - girme riskini dosya boyutuyla sinirsiz
# birakir) hem de katmanlar arasi davranis farkina yol aciyordu. chunk_text()
# kucuk metinlerde (varsayilan 200k karakter altinda, yani pratikte COGU
# dosyada) tek parca dondurup davranisi DEGISTIRMEZ.
from app.services.text_chunking import DEFAULT_CHUNK_OVERLAP_CHARS, DEFAULT_MAX_CHUNK_CHARS, chunk_text

# Recognizes our own placeholder format. Two generations, both supported so
# a file masked before the format below changed stays reversible forever:
#   - current:  mask_<prefix>_<N>, e.g. mask_ip_1, mask_proje_adi_12. The
#     literal "mask_" marker is what lets this regex tell a placeholder
#     apart from an ordinary lowercase identifier that happens to end in
#     "_<digits>" (batch_1, item_2, ...).
#   - legacy:   <PREFIX>_TEST_<N>, e.g. IP_TEST_1 - the format used before
#     placeholders were switched to the lowercase "mask_" scheme.
# Used so a rerun over an already-masked file never double-masks (or
# corrupts) an existing placeholder, regardless of which generation it is.
PLACEHOLDER_RE = re.compile(
    r"\bmask_[a-z][a-z0-9]*(?:_[a-z0-9]+)*_\d+\b"
    r"|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_TEST_\d+\b"
)
# Broader grammar used while scanning for reversible candidates. Rule
# administration also permits a bare PREFIX_{sayac} (legacy, uppercase, no
# _TEST) or mask_prefix_{sayac} (current) without going through
# PLACEHOLDER_RE's stricter shape. Only DB-known candidates in this
# broader grammar are reversible; ordinary identifiers (HTTP_200,
# VERSION_3) must remain unchanged.
_REVERSIBLE_TOKEN_RE = re.compile(
    r"\bmask_[a-z][a-z0-9_]*_\d+\b|\b[A-Z][A-Z0-9_]*_\d+\b"
)
# Recognize damaged marker casing/suffixes for diagnostics, never for
# guessing a replacement. Exact DB keys remain the sole authority.
_SUSPECT_TOKEN_RE = re.compile(
    r"\bmask_[a-z][a-z0-9_]*_\d+\w*\b|\b[a-z][a-z0-9_]*_test_\d+\w*\b", re.IGNORECASE
)

# JSON'da tirnaksiz bir alan SADECE bir sayi (ya da true/false/null) olabilir -
# yukaridaki harf-tabanli placeholder semasi (mask_ip_1 gibi) tirnaksiz bir
# JSON sayi konumuna YAZILAMAZ, gecersiz JSON uretir (bkz. syntax_validator.py
# compare_json_types - bu tam olarak boyle bir bozulmayi yakalayip dosyayi
# reddetmek icin var). Bu yuzden JSON'da tirnaksiz (bare) bir sayi konumunda
# maskelenen degerler, harfle degil SABIT bir rakam dizisiyle (811199...)
# baslayan, sabit genislikte bir tamsayi olarak temsil edilir:
#   - Gecerli bir JSON number'dir (sozdizimini bozmaz, compare_json_types
#     tip degisikligi gormez).
#   - Onek harfle BASLAMADIGI icin PLACEHOLDER_RE/_REVERSIBLE_TOKEN_RE ile
#     YAPISAL olarak cakismaz (o desenler harfle baslamayi sart kosar).
#   - Kaynakta bu sekle benzeyen gercek sayilar bulunabilir. Round-trip
#     ve imzali kaynak butunluk kaydi olmadan sekil, kimlik kaniti degildir.
# Version 2 uses 15 decimal digits, exactly representable by IEEE-754
# binary64 (including JavaScript Number). Keep the old 16-digit grammar
# readable; never allocate new tokens in that unsafe namespace.
JSON_NUMERIC_PLACEHOLDER_PREFIX = "811199"
JSON_NUMERIC_COUNTER_NAMESPACE = "__json_numeric_v2__"
_JSON_NUMERIC_PLACEHOLDER_COUNTER_DIGITS = 9
JSON_NUMERIC_PLACEHOLDER_RE = re.compile(
    rf"\b(?:{JSON_NUMERIC_PLACEHOLDER_PREFIX}\d{{{_JSON_NUMERIC_PLACEHOLDER_COUNTER_DIGITS}}}|911199\d{{10}})\b"
)
# JSON gramerinde gecerli, tirnaksiz bir tamsayi (bastaki sifir yasak, isaretli olabilir).
JSON_BARE_INTEGER_RE = re.compile(r"-?(0|[1-9][0-9]*)")


def make_json_numeric_placeholder(counter: int) -> str:
    if not 1 <= counter < 10 ** _JSON_NUMERIC_PLACEHOLDER_COUNTER_DIGITS:
        raise ValueError("Sayisal placeholder sayac kapasitesi asildi; yeni format surumu gerekli.")
    return f"{JSON_NUMERIC_PLACEHOLDER_PREFIX}{counter:0{_JSON_NUMERIC_PLACEHOLDER_COUNTER_DIGITS}d}"

_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
_PARAMETRIC_BOUNDARY_CHARS = "A-Za-z0-9_"

# Turkce kucuk/buyuk harfler - Python'un \w'si Unicode-farkli oldugundan
# (PLACEHOLDER_RE'nin yukaridaki \b'si dahil - Turkce bir harf de \w sayilir)
# ama ASCII karakter siniflari (orn. "[A-Za-z0-9_]") bunu ICERMEZ, iki
# temsili de acikca tanimliyoruz: TR_ALNUM_CHARS tekil karakter kontrolu
# icin (bkz. token_boundary_validator.py _WORD_CHARS - PLACEHOLDER_RE'nin
# Unicode \b'siyle AYNI kumeyi kullanmasi gerekir, aksi halde bir katman
# "sinir var" derken digeri "yok" der ve placeholder metnin ortasinda
# bitmis gibi GORUNUP unmask'ta cozulemez), TR_LOWER_CLASS/TR_UPPER_CLASS
# ise regex karakter sinifi (orn. "[a-z" + TR_LOWER_CLASS + "]") parcasi olarak.
TR_LOWER_CLASS = "a-zçğıöşü"
TR_UPPER_CLASS = "A-ZÇĞİÖŞÜ"
TR_ALNUM_CHARS = frozenset(
    "0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZÇĞİÖŞÜabcdefghijklmnopqrstuvwxyzçğıöşü"
)

# İki bitisik karakter arasinda, klasik "\b" (iki harf arasinda ASLA sinir
# saymaz) YAKALAYAMAYACAGI ama gercekte bir kelime/kod-token'i sinirini
# temsil eden gecis turleri - iki ayri grupta:
#
# _CASE_TRANSITION: kucuk->buyuk harf (camelCase, orn. "girisYapanSicil"
# icindeki "Sicil") ve kisaltma->Kelime (orn. "APISicil" icindeki "Sicil").
# IGNORECASE tum derlenen desende gecerli oldugundan, harf-BUYUKLUGUNE
# bakan bu testler (?-i:...) ile YEREL olarak case-sensitive'e cevrilmeli -
# aksi halde IGNORECASE altinda [a-z] de [A-Z] de AYNI harflerle eslesir
# ve gecis testi anlamsizlasip HER iki harf arasini sinir sayardi.
#
# _DIGIT_LETTER_TRANSITION: rakam<->harf (orn. "sicil01", "01sicil").
# AYRI tutuluyor cunku sadece kurumsal terim sozlugu (belirli, elle
# secilmis kod adlari) icin guvenli - parametrik kurallara (proje adi/
# sicil no/branch) uygulanirsa, SAYISAL sicil_no degerleri "PKCS12",
# "AES256" gibi rastgele teknik token'larin SONUNDAKI rakamlarla
# cakisir (mevcut _is_numeric_technical_context/_looks_like_technical_
# sicil_token filtreleri SADECE bilinen belirli onekleri/kaliplari
# tanir, genel "harf+rakam" token'lari degil - bkz. build_pattern'in
# parametrik kolu, orada BILEREK kullanilmiyor).
_CASE_TRANSITION = (
    rf"(?:(?-i:(?<=[{TR_LOWER_CLASS}])(?=[{TR_UPPER_CLASS}]))"
    rf"|(?-i:(?<=[{TR_UPPER_CLASS}])(?=[{TR_UPPER_CLASS}][{TR_LOWER_CLASS}])))"
)
_DIGIT_LETTER_TRANSITION = (
    rf"(?:(?<=[0-9])(?=[{TR_LOWER_CLASS}{TR_UPPER_CLASS}])"
    rf"|(?<=[{TR_LOWER_CLASS}{TR_UPPER_CLASS}])(?=[0-9]))"
)

# Turkce'de eklerin ozel isme dogrudan (kesme isareti olmadan) yapismasi
# COK yaygin bir yazim ("Poseidonda", "Poseidonun" - dogru yazim
# "Poseidon'da"/"Poseidon'un" olsa da). Bu, bir terimin/proje adinin
# metinde gectigi COK sayida gercek durumun sessizce kacirilmasina yol
# aciyordu (kor testte gozlemlendi). SADECE uzun/belirgin (2+ harfli)
# durum eklerini taniyoruz VE ekin TAM OLARAK kelimenin geri kalani
# olmasini (ardindan baska bir kelime karakteri GELMEMESINI) sart
# kosuyoruz - aksi halde "main" + "tenance" = "maintenance" gibi
# TAMAMEN ALAKASIZ bir kelimenin devami bir Turkce ekiyle baslamasi
# tesaduf eslesme yaratirdi (gercek testte yakalandi: "main"
# "maintenance" icinde "-ten" ekiyle basladigi icin yanlislikla
# eslesiyordu). SADECE SAG sinirda kullanilir (bir ek daima kelimenin
# SONUNA eklenir, basina degil).
_TR_SUFFIX_STARTS = (
    "dan", "den", "tan", "ten",  # ayrilma hali: -dan/-den/-tan/-ten
    "dır", "dir", "dur", "dür", "tır", "tir", "tur", "tür",  # bildirme eki: -dır/-dir/-dur/-dür
    "nın", "nin", "nun", "nün", "ın", "in", "un", "ün",  # tamlayan eki: -(n)ın/-(n)in/-(n)un/-(n)ün
)
_TR_WORD_CHAR_CLASS = f"0-9_{TR_LOWER_CLASS}{TR_UPPER_CLASS}"
_TR_SUFFIX_TRANSITION = rf"(?=(?:{'|'.join(_TR_SUFFIX_STARTS)})(?:[^{_TR_WORD_CHAR_CLASS}]|$))"
# Kurumsal terim sozlugu icin tam kume (bkz. term_upload.py) - terim_
# classifier.py zaten salt sayisal/cok kisa/genel terimleri "supheli"
# (pasif) isaretledigi icin buradaki ekstra agresiflik o katmanla dengelenir.
_COMPOUND_WORD_TRANSITION = rf"(?:{_CASE_TRANSITION}|{_DIGIT_LETTER_TRANSITION})"


# Metindeki tum placeholder token'larini gercek degerle degistirir; sozlukte
# olmayanlari oldugu gibi birakip "unresolved" listesine ekler. Hem gercek
# geri-donusumde (unmasker.py) hem export-anindaki kendini-sinamada
# (roundtrip_validator.py) AYNI fonksiyon kullanilir.
_PLACEHOLDER_CORE_PREFIX_RE = re.compile(
    r"^(?:mask_[a-z][a-z0-9]*(?:_[a-z0-9]+)*_\d+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_TEST_\d+)"
)
# Cekirdek onegi bulunduktan SONRA kalan artigin gercekten "kesme isaretsiz
# yapismis bir Turkce eki" mi yoksa "bozuk/kurcalanmis bir marker" mi
# oldugunu ayirt eder - SADECE salt kucuk harfli Turkce harflerden olusan
# (alt cizgisiz, rakamsiz, BUYUK harfle BASLAMAYAN) bir artik kabul edilir.
# "_suffix" (alt cizgiyle basliyor) ya da "Suffix" (buyuk harfle basliyor)
# gibi TESADUFEN ayni sekle uyan ama gercekte damaged-marker senaryosunu
# temsil eden artiklar BUNUNLA reddedilir (bkz. test_damaged_marker_is_
# reported_without_manifest - bu testin "hicbir zaman sessizce cozme"
# ilkesi Turkce ek DISINDAKI her artik icin hala gecerli).
_TR_SUFFIX_REMAINDER_RE = re.compile(rf"^[{TR_LOWER_CLASS}]+$")


def reverse_text(text: str, placeholder_map: dict[str, str]) -> tuple[str, int, list[str]]:
    # Harf-tabanli token'lar (_REVERSIBLE_TOKEN_RE) ve JSON sayisal
    # placeholder'lar (JSON_NUMERIC_PLACEHOLDER_RE) YAPISAL olarak ayrisir
    # (biri harfle, digeri rakamla baslamak ZORUNDA) - bu yuzden iki ayri
    # regex'in eslesmelerini konuma gore BIRLESTIRIP TEK sirali gecisle
    # cozmek guvenlidir, cakisma riski yoktur.
    candidates = sorted(
        (*_REVERSIBLE_TOKEN_RE.finditer(text), *JSON_NUMERIC_PLACEHOLDER_RE.finditer(text),
         *_SUSPECT_TOKEN_RE.finditer(text)),
        key=lambda m: (m.start(), -m.end()),
    )
    parts: list[str] = []
    cursor = 0
    resolved = 0
    unresolved: list[str] = []

    for m in candidates:
        if m.start() < cursor:
            continue
        token = m.group(0)
        parts.append(text[cursor : m.start()])
        if token in placeholder_map:
            parts.append(placeholder_map[token])
            resolved += 1
            cursor = m.end()
            continue
        # YORUM SATIRINDA (kod DEGIL) bir Turkce ek, placeholder'a kesme
        # isareti OLMADAN dogrudan yapisabilir (orn. "mask_terim_5liği") -
        # boyle bir aday HICBIR ZAMAN placeholder_map'te TAM olarak yer
        # almaz (ek, placeholder uretiminin bir parcasi degil - bkz.
        # token_boundary_validator.py'nin yorumlarda KASITLI olarak
        # identifier sinirina GENISLETMEME kurali). Adayin BASINDAKI
        # gercek placeholder cekirdegini (sayaca kadar, PLACEHOLDER_RE ile
        # AYNI kati sekil) ayirip ONU sozlukte aramak, kalan eki (ne
        # olursa olsun) OLDUGU GIBI birakarak dogru geri-donusu saglar -
        # gercek intranet calismasinda "...icin az once olusturulan
        # mapping'de karsilik bulunamadi: mask_kurumsal_ifade_5'lik gibi"
        # seklinde gozlemlenen round-trip hatasi buradan geliyordu.
        prefix_match = _PLACEHOLDER_CORE_PREFIX_RE.match(token)
        if prefix_match:
            core = prefix_match.group(0)
            remainder = token[len(core):]
            if core in placeholder_map and _TR_SUFFIX_REMAINDER_RE.fullmatch(remainder):
                parts.append(placeholder_map[core])
                parts.append(remainder)
                resolved += 1
                cursor = m.end()
                continue
        parts.append(token)
        if PLACEHOLDER_RE.fullmatch(token) or JSON_NUMERIC_PLACEHOLDER_RE.fullmatch(token) or _SUSPECT_TOKEN_RE.fullmatch(token):
            unresolved.append(token)
        cursor = m.end()

    parts.append(text[cursor:])
    return "".join(parts), resolved, unresolved


# DB'deki bir filter_rules satirinin DB'den bagimsiz, saf (pure) temsili.
@dataclass(frozen=True)
class RuleSpec:
    id: int
    rule_name: str
    category: str
    pattern_type: str  # 'regex' | 'parametric' | 'llm'
    regex_pattern: str | None
    regex_flags: str | None
    placeholder_prefix: str
    priority: int
    validator_name: str | None = None
    description: str | None = None


# Bir metinde bulunan tek bir eslesmeyi (hangi kural, hangi deger, nerede) temsil eder.
@dataclass(frozen=True)
class Match:
    rule: RuleSpec
    original_value: str
    start: int
    end: int
    # Registry/denetim provenance. Defaults keep direct RuleEngine callers
    # backward compatible; detector orchestration fills both fields.
    entity_type: str | None = None
    source_detector: str | None = None
    # Olasiliksal kaynaklarin (LLM/Presidio) guven seviyesi; tutarlilik
    # registry'sine kabul karari icin kullanilir.
    confidence: str | None = None


# Bir kuralin tanimi gecersiz/tutarsiz oldugunda (orn. pattern_type='regex'
# ama regex_pattern eksik) firlatilan hata.
class InvalidRuleError(ValueError):
    pass


# "i", "m", "s" gibi kisa regex flag harflerini Python'un re modul bayraklarina cevirir.
def _compile_flags(flags: str | None) -> int:
    result = 0
    for ch in flags or "":
        result |= _FLAG_MAP.get(ch, 0)
    return result


# Turkce harflerin "Turkce karakter" / "ASCII (karaktersiz) yazim" karsiliklarini
# tek bir karakter sinifinda birlestirir - "s"<->"ş", "c"<->"ç", "g"<->"ğ",
# "i"<->"ı", "o"<->"ö", "u"<->"ü". Kurumsal terim sozlugune SADECE "sube" ya da
# SADECE "şube" eklense bile diger yazimin metinde otomatik yakalanmasi icin
# (bkz. term_upload.py._compute_rule_fields - regex_flags="i" zaten BUYUK/kucuk
# harf farkini cozuyor, ama "ş" ile "s" birbirinin buyuk/kucuk harf karsiligi
# DEGIL, tamamen ayri Unicode karakter - IGNORECASE bunu asla birlestirmez).
_TR_ASCII_FOLD_CLASSES = {
    "s": "sş", "ş": "sş",
    "c": "cç", "ç": "cç",
    "g": "gğ", "ğ": "gğ",
    "i": "iı", "ı": "iı",
    "o": "oö", "ö": "oö",
    "u": "uü", "ü": "uü",
}


# re.escape'in Turkce-karakter-toleransli hali: normal kacislama yapar, ama
# yukaridaki listedeki her harfi (buyuk/kucuk farketmeksizin, regex_flags="i"
# zaten o kismi cozuyor) hem Turkce hem ASCII yazimini kabul eden bir karakter
# sinifina cevirir. Sadece kurumsal terim sozlugu icin kullanilir (bkz.
# term_upload.py) - parametrik/seed kurallar bunu KULLANMAZ, cunku oralarda
# beklenen deger formati (IP, UUID, TC kimlik no vb.) zaten Turkce harf icermez.
def diacritic_tolerant_escape(term: str) -> str:
    parts = []
    for ch in term:
        variants = _TR_ASCII_FOLD_CLASSES.get(ch.casefold())
        parts.append(f"[{variants}]" if variants else re.escape(ch))
    return "".join(parts)


# Kacisli bir degeri, klasik "\b" yerine gecen daha akilli bir sinir kontroluyle
# sarar - camelCase/rakam-harf gecislerini de sinir sayar (orn. "sicil01" icindeki "sicil").
def compound_aware_boundary_pattern(
    escaped_value: str,
    *,
    connector_class: str,
    include_digit_transitions: bool = True,
    free_right_continuation: bool = False,
) -> str:
    transition = _COMPOUND_WORD_TRANSITION if include_digit_transitions else _CASE_TRANSITION
    left = rf"(?:(?<![{connector_class}])|{transition})"
    if free_right_continuation:
        # Kurumsal terim sozlugu icin (bkz. term_upload.py cagri yeri): sagda
        # KUCUK harfli bir Turkce ekle devam etse bile ("subeler", "subesi",
        # "subemuduru") sinir sayilir - _TR_SUFFIX_TRANSITION'in kapsadigi
        # SINIRLI ek listesiyle degil, hicbir sag-sinir kisitlamasi olmadan.
        # "main" + "tenance" = "maintenance" tarzi rastgele cakisma riski BU
        # YUZDEN parametrik kurallarda (varsayilan False) kabul edilmiyor,
        # ama kurumsal terimlerde kabul edilebilir - terimler elle onaylanir
        # VE classify_term zaten "main" gibi kisa/yaygin kelimeleri pasif
        # (is_active=False) ekler (bkz. term_classifier.py _COMMON_WORDS_EN).
        right = ""
    else:
        # _TR_SUFFIX_TRANSITION SADECE sag sinirda: bir Turkce eki daima
        # kelimenin SONUNA eklenir, basina degil.
        right = rf"(?:(?![{connector_class}])|{transition}|{_TR_SUFFIX_TRANSITION})"
    return f"{left}{escaped_value}{right}"


# 'sicil_no' kategorisi icin, gercek sicil numarasi degil de teknik
# bir kisaltmayla (p12/pkcs12/sha256/rsaNNNN gibi) cakisan degerleri ayirt eder.
def _looks_like_technical_sicil_token(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"p12", "pkcs12", "sha256"}:
        return True
    if re.fullmatch(r"rsa\d+", lowered):
        return True
    return False


# Salt sayisal bir 'sicil_no' eslesmesinin, aslinda bir surum numarasi/
# port/degisken-icinde-sayi gibi teknik bir baglamda gectigini kontrol eder.
def _is_numeric_technical_context(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before == "{" and after == "}":
        return True
    if before == "." or after == ".":
        return True

    left_start = start
    while left_start > 0 and re.match(r"[A-Za-z0-9_.-]", text[left_start - 1]):
        left_start -= 1
    right_end = end
    while right_end < len(text) and re.match(r"[A-Za-z0-9_.-]", text[right_end]):
        right_end += 1
    token = text[left_start:right_end]
    if re.fullmatch(r"v?\d+(?:\.\d+){1,}(?:[-+][A-Za-z0-9_.-]+)?", token, re.IGNORECASE):
        return True
    if re.fullmatch(r"(?:rsa|sha)\d+", token, re.IGNORECASE):
        return True
    if re.fullmatch(r"pkcs\d+", token, re.IGNORECASE):
        return True
    return False


# Bir parametrik eslesmenin gecerli sayilip sayilmayacagina karar verir -
# sadece 'sicil_no' kategorisi icin yukaridaki teknik-baglam filtrelerini
# uygular. start/end HER ZAMAN `text` (chunk DEGIL, tam/orijinal metin)
# uzerindeki GLOBAL konumlardir - find_matches_compiled metni parcalara
# bolse bile (bkz. asagisi), teknik-baglam kontrolu chunk sinirindan
# etkilenmeden dogru komsu karakterlere bakabilsin diye boyle tasarlandi.
def _parametric_match_allowed(rule: RuleSpec, text: str, value: str, start: int, end: int) -> bool:
    if rule.category != "sicil_no":
        return True
    if _looks_like_technical_sicil_token(value):
        return False
    if value.isdigit() and _is_numeric_technical_context(text, start, end):
        return False
    return True


# Bir RuleSpec'i, metinde arama yapmaya hazir derlenmis bir regex'e cevirir (uygulanamazsa None).
def build_pattern(rule: RuleSpec, runtime_params: dict[str, str]) -> re.Pattern | None:
    if rule.pattern_type == "regex":
        if not rule.regex_pattern:
            raise InvalidRuleError(f"rule '{rule.rule_name}' is type 'regex' but has no pattern")
        return re.compile(rule.regex_pattern, _compile_flags(rule.regex_flags))

    if rule.pattern_type == "parametric":
        value = runtime_params.get(rule.category)
        if not value:
            return None
        escaped = re.escape(value)
        # No plain word-internal matching: underscores stay CONNECTORS (not
        # boundaries) - identifiers, module names, template variables and
        # test fixtures like "main_handler" must not match a project/branch
        # named "main". A case TRANSITION (camelCase, ACRONYMWord - see
        # _CASE_TRANSITION) still counts as a real boundary, so e.g.
        # project_name="Poseidon" is still caught inside
        # "projePoseidonKurulumu". Digit<->letter transitions are
        # DELIBERATELY excluded here (unlike the term dictionary) - see
        # _DIGIT_LETTER_TRANSITION docstring for why that's unsafe for a
        # numeric sicil_no value.
        pattern = compound_aware_boundary_pattern(
            escaped, connector_class=_PARAMETRIC_BOUNDARY_CHARS, include_digit_transitions=False
        )
        return re.compile(pattern, re.IGNORECASE)

    if rule.pattern_type == "llm":
        # No regex to build - handled entirely by llm_recognizer.find_llm_detections,
        # outside this module. Returning None makes find_matches's loop skip it,
        # identical to an unsupplied parametric rule.
        return None

    raise InvalidRuleError(f"unknown pattern_type '{rule.pattern_type}' on rule '{rule.rule_name}'")


# Verilen span (baslangic, bitis) daha once "kullanilmis" bir aralikla
# cakisiyor mu diye kontrol eder - ayni metin parcasinin iki kez eslenmesini onler.
def _overlaps(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    s, e = span
    return any(not (e <= cs or s >= ce) for cs, ce in consumed)


# Bir RuleSpec listesini, priority sirasina gore derlenmis (pattern,
# validator) uclulerine cevirir - build_pattern() (regex derleme) ve
# VALIDATORS.get() lookup'i sadece BURADA, bir kere yapilir. find_matches()
# bunu HER cagrida (yani find_matches'i dosya basina cagiran RuleBasedDetector
# icin HER DOSYADA) yeniden yapiyordu; CompiledRuleSet bunu bir kere
# hesaplayip cagiranin (bkz. RuleBasedDetector.__init__) run boyunca
# saklamasini saglar.
@dataclass(frozen=True)
class _CompiledRule:
    rule: RuleSpec
    pattern: re.Pattern | None
    validator: object | None


# RuleSpec listesini priority sirasina gore derler; pattern_type='llm' ya da
# degeri verilmemis parametrik kurallar (build_pattern None dondurur) atlanir.
def compile_rules(rules: list[RuleSpec], runtime_params: dict[str, str] | None = None) -> list[_CompiledRule]:
    runtime_params = runtime_params or {}
    compiled: list[_CompiledRule] = []
    for rule in sorted(rules, key=lambda r: r.priority):
        pattern = build_pattern(rule, runtime_params)
        if pattern is None:
            continue
        validator = None
        if rule.validator_name:
            validator = VALIDATORS.get(rule.validator_name)
            if validator is None:
                raise InvalidRuleError(
                    f"rule '{rule.rule_name}' references unknown validator_name '{rule.validator_name}'"
                )
        compiled.append(_CompiledRule(rule=rule, pattern=pattern, validator=validator))
    return compiled


# Onceden derlenmis kurallari metne uygular (find_matches ile ayni mantik, ama
# regex tekrar derlenmez). Buyuk metinler parca parca (chunk_text ile) taranir.
# Farkli kurallarin cakisan adaylari burada erken elenmez: bu katman sadece
# mevcut placeholder span'lerini korur ve chunk tekrarlarini tekillestirir.
# Gercek cakismalar tum detector adaylari toplandiktan sonra merkezi
# OverlapResolver tarafindan cozulur.
def find_matches_compiled(
    compiled_rules: list[_CompiledRule],
    text: str,
    *,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> tuple[list[Match], list[tuple[int, int]]]:
    protected_spans: list[tuple[int, int]] = []
    already_masked: list[tuple[int, int]] = []

    for m in PLACEHOLDER_RE.finditer(text):
        span = (m.start(), m.end())
        protected_spans.append(span)
        already_masked.append(span)

    # PLACEHOLDER_RE tek basina round-trip ile TUTARSIZ: buyuk/kucuk harfe
    # DUYARLI (sadece mask_x_N ya da PREFIX_TEST_N'i tanir), ama
    # reverse_text() (round-trip dogrulamasinin "bu token cozulemedi,
    # basarisiz say" testi) _SUSPECT_TOKEN_RE ile AYNI seklin buyuk/kucuk
    # harf FARKI GOZETMEKSIZIN her varyantini "supheli" sayiyor. Bu, kaynakta
    # placeholder'la HICBIR ilgisi olmayan ama tesaduf sekilce benzeyen
    # siradan bir tanimlayiciyi (orn. "unit_test_3") round-trip asamasinda
    # HAKSIZ YERE reddediyordu (bkz. gercek intranet calismasinda gozlemlenen
    # round-trip hatalari - cogu yorum satirindaki "..._test_N" bicimli
    # ifadelerden geliyordu).
    #
    # BUNU protected_spans'a KATMADAN sadece already_masked'e eklemek KRITIK:
    # _SUSPECT_TOKEN_RE kasitli olarak genis bir sekil (bkz. dokstringi) -
    # gercek bir sifre/anahtar deger de tesaduf bu sekle uyabilir (orn.
    # "sk_test_51H8xJ2..." bir Stripe test-modu API anahtaridir,
    # generic_secret_assignment kuralinin hedefi). protected_spans'a
    # eklenseydi (ilk denemede boyle yapilmisti) bu gercek sifre "zaten
    # placeholder" sanilip generic_secret_assignment kuralinin onunu
    # KESIYOR ve deger HIC MASKELENMEDEN disari sizabiliyordu - gercek bir
    # regresyon, testle dogrulandi (find_matches_compiled bu durumda 0
    # eslesme donduruyordu). already_masked'e (SADECE round-trip'in
    # identity-map kismina) eklemek, "MAX_LOGIN_TEST_3" icin zaten var olan
    # korumanin (bkz. exporter.py) kucuk/karisik harfli esdegerini verirken
    # bu riski tasimiyor - bir kural zaten bu span'i (ya da onu kapsayan
    # daha genis bir span'i) yakalarsa placeholder_map'teki fazladan
    # identity girdisi kullanilmiyor (o metin masked_text'te artik yok).
    for m in _SUSPECT_TOKEN_RE.finditer(text):
        already_masked.append((m.start(), m.end()))

    matches: list[Match] = []
    seen_spans: set[tuple[int, int, str]] = set()
    for chunk_start, chunk in chunk_text(text, max_chunk_chars, chunk_overlap_chars):
        for compiled in compiled_rules:
            rule, pattern, validator = compiled.rule, compiled.pattern, compiled.validator
            for m in pattern.finditer(chunk):
                # Kuralda "deger" adli bir grup varsa (orn. XML <tag>deger</tag>),
                # SADECE o grup degistirilir - sarmalayici (etiketler) korunur.
                if "deger" in m.groupdict() and m.group("deger") is not None:
                    span = (chunk_start + m.start("deger"), chunk_start + m.end("deger"))
                else:
                    span = (chunk_start + m.start(), chunk_start + m.end())
                dedup_key = (span[0], span[1], rule.rule_name)
                if dedup_key in seen_spans:
                    continue  # overlap bolgesinde onceki chunk'ta zaten bulunmus
                if _overlaps(span, protected_spans):
                    continue
                value = text[span[0] : span[1]]
                if validator is not None and not validator(value):
                    continue
                if rule.pattern_type == "parametric" and not _parametric_match_allowed(
                    rule, text, value, span[0], span[1]
                ):
                    continue
                matches.append(Match(rule=rule, original_value=value, start=span[0], end=span[1]))
                seen_spans.add(dedup_key)

    matches.sort(key=lambda mm: mm.start)
    return matches, already_masked


# Kural motorunun ana fonksiyonu: bir metni tum aktif kurallara karsi tarar,
# tum adaylari ve zaten placeholder olan (dokunulmayan) yerleri dondurur.
# Adaylar birbiriyle cakisma icerebilir; uygulamadan once merkezi resolver'dan
# gecirilmelidir.
def find_matches(
    rules: list[RuleSpec],
    text: str,
    runtime_params: dict[str, str] | None = None,
    *,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> tuple[list[Match], list[tuple[int, int]]]:
    return find_matches_compiled(
        compile_rules(rules, runtime_params),
        text,
        max_chunk_chars=max_chunk_chars,
        chunk_overlap_chars=chunk_overlap_chars,
    )
