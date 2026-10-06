"""Katman 2 detector: Presidio + DB-driven regex PatternRecognizer rules."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

# DetectionResult/DetectorOutput: ortak tespit sonuc tipleri. entropy:
# dogal-dil kategorileri icin rastgelelik/entropi filtresi. rule_engine:
# ozel kurallarin RuleSpec'e cevrimi ve cakisma yardimcisi (_overlaps).
from app.services.detectors import DetectionResult, DetectorOutput, placeholder_prefix_for_type, synthetic_llm_rule
from app.services.encoded_blobs import blank_spans
from app.services.entropy import NATURAL_LANGUAGE_ENTITY_TYPES, is_high_entropy
from app.services.rule_engine import RuleSpec, _compile_flags, _overlaps
# chunk_text: buyuk metinleri overlap'li parcalara bolen ortak yardimci -
# rule_engine.py (Katman 1) ile PAYLASILIR, bkz. text_chunking.py modul docstring'i.
from app.services.text_chunking import DEFAULT_CHUNK_OVERLAP_CHARS, DEFAULT_MAX_CHUNK_CHARS, chunk_text

_logger = logging.getLogger(__name__)

_DEFAULT_MAX_ANALYZER_CHARS = DEFAULT_MAX_CHUNK_CHARS
_DEFAULT_CHUNK_OVERLAP_CHARS = DEFAULT_CHUNK_OVERLAP_CHARS

# Presidio'nun yerlesik UrlRecognizer'i "sema gerektirmeyen" (Non schema URL /
# Quoted Non-schema URL, skor 0.5) desenler icerir; bunlarin TLD listesi cok
# sayida kisa ccTLD (pl, si, in, is, it, id, io, me, us, to, st ...) icerdigi
# icin noktali TANIMLAYICILARI (Java paket adlari - "java.util.Base64", Maven
# groupId - "org.apache.maven.plugins", SQL alias.kolon referanslari -
# "p.SICIL_NO", XML etiket adlari - "<maven.compiler.source>") yanlislikla
# URL saniyor (kod dosyalari icin URL kategorisi izinli - bkz.
# dosya_tipi_kategori_kisitlamasi seed verisi). Bu yuzden yerlesik
# recognizer'i CIKARIP, SADECE http(s):// semasi gerektiren desenlerini
# (skor 0.6) iceren daha siki bir surumunu ekliyoruz - gercek gomulu URL'ler
# hala yakalanir, dotted identifier'lar artik yakalanmaz.
_SCHEMA_REQUIRED_URL_PATTERN_NAMES = {"Standard Url", "Quoted URL"}


# Metadata'daki dosya yolundan uzantiyi (noktasiz, kucuk harf) cikarir -
# kategori kisitlamalarini uygulamak icin kullanilir.
def _extension_from_metadata(metadata: dict | None) -> str:
    file_path = (metadata or {}).get("file_path") or ""
    return Path(file_path).suffix.lower().lstrip(".")


# Microsoft Presidio (Katman 2 tespit motoru): AnalyzerEngine metni tarar,
# Pattern/PatternRecognizer ozel (DB-tanimli) kurallari analyzer'a ekler,
# RecognizerRegistry yerlesik+ozel recognizer'lari yonetir, NlpArtifacts
# spaCy on-isleme sonucunu tasir. Presidio kurulu degilse (opsiyonel
# bagimlilik) hepsi None kalir ve _fallback_analyze() saf regex'e duser.
try:
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpArtifacts
except ImportError:  # pragma: no cover - exercised implicitly when dependency is absent
    AnalyzerEngine = None
    Pattern = None
    PatternRecognizer = None
    RecognizerRegistry = None
    NlpArtifacts = None


# DB'deki bir Presidio (Katman 2) kuralinin saf veri temsili - RuleSpec'e
# cevrilerek diger katmanlarla ayni Match/audit akisina katilmasini saglar.
@dataclass(frozen=True)
class PresidioRuleSpec:
    rule_name: str
    regex_pattern: str
    entity_type: str
    confidence_score: float
    is_allow_list: bool
    regex_flags: str | None = None
    placeholder_prefix: str | None = None
    rule_id: int | None = None
    priority: int = 100

    # Bu Presidio kuralini, mask_text()'in beklerdigi ortak RuleSpec bicimine cevirir.
    def as_rule_spec(self) -> RuleSpec:
        return RuleSpec(
            id=self.rule_id,  # type: ignore[arg-type]
            rule_name=self.rule_name,
            category=self.entity_type,
            pattern_type="presidio",
            regex_pattern=self.regex_pattern,
            regex_flags=self.regex_flags,
            placeholder_prefix=self.placeholder_prefix or placeholder_prefix_for_type(self.entity_type),
            priority=self.priority,
            validator_name=None,
            description="Katman 2 Presidio pattern kuralı.",
        )


# Presidio analyzer'in (veya fallback regex'in) ham cikisini temsil eden
# ic (private) yardimci tip - henuz DetectionResult'a cevrilmemis.
@dataclass(frozen=True)
class _AnalyzerResult:
    entity_type: str
    start: int
    end: int
    score: float
    rule: RuleSpec | None = None
    metadata: dict | None = None


# Presidio'nun 0-1 araligindaki skorunu, sistemin genel
# guven_seviyesi (yuksek/orta/dusuk) sozlugune cevirir.
def score_to_confidence(score: float) -> str:
    if score >= 0.85:
        return "yuksek"
    if score >= 0.6:
        return "orta"
    return "dusuk"


# Katman 2 detector: Presidio'nun yerlesik recognizer'lari + DB-tanimli ozel
# PatternRecognizer kurallarini calistirir, dosya-turu/entropi filtrelerini uygular.
class PresidioDetector:
    name = "katman2_presidio"

    # Kural/dil/model ayarlarini saklar; Presidio analyzer'ini bir kez kurar.
    def __init__(
        self,
        rules: list[PresidioRuleSpec],
        language: str = "en",
        spacy_model: str | None = "en_core_web_lg",
        use_builtin_recognizers: bool = True,
        category_restrictions: dict[str, list[str]] | None = None,
        entropy_threshold: float = 3.5,
        max_analyzer_chars: int = _DEFAULT_MAX_ANALYZER_CHARS,
        chunk_overlap_chars: int = _DEFAULT_CHUNK_OVERLAP_CHARS,
        disabled_entities: frozenset[str] = frozenset(),
    ) -> None:
        self.rules = rules
        self.disabled_entities = disabled_entities
        self.language = language
        self.spacy_model = spacy_model
        self.use_builtin_recognizers = use_builtin_recognizers
        self.category_restrictions = category_restrictions or {}
        self.entropy_threshold = entropy_threshold
        self.max_analyzer_chars = max_analyzer_chars
        self.chunk_overlap_chars = chunk_overlap_chars
        self._allow_list_rules = [rule for rule in rules if rule.is_allow_list]
        self._custom_rules = [rule for rule in rules if not rule.is_allow_list]
        # Ayni detector ornegi bir calismadaki TUM dosyalar arasinda paylasilir
        # ve analiz ayri thread'lerde yurur (bkz. detect). spaCy Language
        # nesnesi (vocab/StringStore) ve tldextract'in tembel onbellegi
        # eszamanli cagri icin guvenli degil; analyzer'a erisim tek tek yapilir.
        self._analyze_lock = threading.Lock()
        self._analyzer = self._build_analyzer()

    # True ise bu detector, kurulumu basarisiz oldugu icin bu calisma
    # boyunca SADECE _fallback_analyze()'in dar regex kumesiyle
    # (EMAIL/IP/CREDIT_CARD/PHONE + ozel kurallar) calisiyor demektir -
    # Presidio'nun yerlesik NLP tabanli (PERSON/ORGANIZATION/LOCATION/
    # DATE_TIME/NRP) tespiti bu calisma icin TAMAMEN devre disi. Cagiran
    # taraf (exporter.py) bunu SESSIZCE gecmemeli - bkz. "detector
    # basarisizligi sessizce basari sayilmamali" ilkesi.
    @property
    def is_degraded(self) -> bool:
        return self._analyzer is None

    # Bir dosya uzantisi icin gecerli entities listesini hesaplar:
    #   - Tabloda hic satir yoksa (kisitlama yok) None doner - Presidio'nun
    #     TUM (yerlesik + ozel) kategorileri calisir.
    #   - Satir varsa, izinli yerlesik kategoriler + ozel kurallarimizin
    #     entity_type'lari BIRLESTIRILEREK donderilir (ozel kurallar dosya
    #     turunden HER ZAMAN bagimsizdir).
    def _entities_for_file(self, metadata: dict | None) -> list[str] | None:
        extension = _extension_from_metadata(metadata)
        allowed_builtin = self.category_restrictions.get(extension)
        if allowed_builtin is None:
            return None
        custom_entity_types = {rule.entity_type for rule in self._custom_rules}
        return list(set(allowed_builtin) | custom_entity_types)

    # Metni Presidio ile tarar; allow-list, dosya-turu ve entropi filtrelerini uygular.
    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        if metadata is not None and metadata.get("enable_presidio") is False:
            return DetectorOutput()
        # Yalnizca mevcut placeholder gibi dokunulmaz span'leri koru.
        # Presidio adaylarini birbirine karsi burada erken elemek merkezi
        # otorite/uzunluk cozumunu devre disi birakir.
        protected_spans = list((metadata or {}).get("consumed_spans", []))
        allow_spans = self._find_allow_list_spans(content)
        entities = self._entities_for_file(metadata)
        results: list[DetectionResult] = []
        # Gomulu ikili veri (base64 resim/ikon) analize bosluk olarak girer:
        # spaCy orada anlamsiz PERSON/ORGANIZATION bulgulari uretip resim
        # verisini maskeletmesin ve zaman harcamasin. Ofsetler degismez.
        analysis_text = blank_spans(content, (metadata or {}).get("encoded_blob_spans", []))

        # spaCy/Presidio CPU-bound: olay dongusunde calisirsa eszamanli LLM
        # isteklerinin zamanlayicilari ilerlemez ve sahte zaman asimi olusur.
        analyzed = await asyncio.to_thread(self._analyze_serialized, analysis_text, entities)
        for item in analyzed:
            span = (item.start, item.end)
            if _overlaps(span, protected_spans) or _overlaps(span, allow_spans):
                continue
            # Kapatilan yerlesik kategoriler atlanir; DB'deki ozel kurallar (rule dolu) her zaman calisir.
            if item.rule is None and item.entity_type in self.disabled_entities:
                continue
            value = content[item.start:item.end]
            # Entropi kontrolu: dogal-dil kategorileri (PERSON vb.) rastgele
            # gorunen (yuksek entropili) bir degere UYGULANMAZ - dosya-tipi
            # kisitlamasindan BAGIMSIZ, ek bir savunma katmani.
            if item.entity_type in NATURAL_LANGUAGE_ENTITY_TYPES and is_high_entropy(value, self.entropy_threshold):
                continue
            rule = item.rule
            if rule is None:
                rule = synthetic_llm_rule(item.entity_type)
            results.append(
                DetectionResult(
                    deger=value,
                    tip=item.entity_type,
                    guven_seviyesi=score_to_confidence(item.score),
                    kaynak_motor=self.name,
                    gerekce=f"Presidio recognizer score={item.score:.2f}",
                    start=item.start,
                    end=item.end,
                    rule=rule,
                    raw_result={
                        "entity_type": item.entity_type,
                        "score": item.score,
                        "metadata": item.metadata or {},
                    },
                )
            )

        return DetectorOutput(results=sorted(results, key=lambda result: result.start or 0))

    # Yerlesik URL taniticisini, sadece http(s):// ile baslayan (daha az yanlis-pozitifli) bir surumle degistirir.
    def _harden_url_recognizer(self, registry) -> None:
        from presidio_analyzer.predefined_recognizers import UrlRecognizer

        schema_required_patterns = [
            pattern for pattern in UrlRecognizer.PATTERNS if pattern.name in _SCHEMA_REQUIRED_URL_PATTERN_NAMES
        ]
        if not schema_required_patterns:
            return
        registry.remove_recognizer("UrlRecognizer")
        registry.add_recognizer(
            UrlRecognizer(patterns=schema_required_patterns, supported_language=self.language)
        )

    # Presidio AnalyzerEngine'i (yerlesik + ozel recognizer'lar, opsiyonel
    # spaCy NLP motoru) kurar; kurulum basarisiz olursa None doner (fallback'e dusulur).
    def _build_analyzer(self):
        if AnalyzerEngine is None:
            return None

        try:
            registry = RecognizerRegistry()
            if self.use_builtin_recognizers:
                registry.load_predefined_recognizers(languages=[self.language])
                self._harden_url_recognizer(registry)
                # Presidio's default email validation fetches/caches public
                # suffix lists on first use. Use its bundled snapshot so an
                # offline or read-only deployment cannot abort masking.
                from presidio_analyzer.predefined_recognizers import EmailRecognizer
                from tldextract import TLDExtract
                extractor = TLDExtract(cache_dir=None, suffix_list_urls=())
                class OfflineEmailRecognizer(EmailRecognizer):
                    def validate_result(self, pattern_text):
                        return extractor(pattern_text).fqdn != ""
                registry.remove_recognizer("EmailRecognizer")
                registry.add_recognizer(OfflineEmailRecognizer(supported_language=self.language))
            for rule in self._custom_rules:
                recognizer = PatternRecognizer(
                    supported_entity=rule.entity_type,
                    patterns=[
                        Pattern(
                            name=rule.rule_name,
                            regex=self._pattern_for_presidio(rule),
                            score=rule.confidence_score,
                        )
                    ],
                    supported_language=self.language,
                )
                registry.add_recognizer(recognizer)
            nlp_engine = None
            if self.spacy_model:
                try:
                    from presidio_analyzer.nlp_engine import NlpEngineProvider

                    configuration = {
                        "nlp_engine_name": "spacy",
                        "models": [{"lang_code": self.language, "model_name": self.spacy_model}],
                    }
                    nlp_engine = NlpEngineProvider(nlp_configuration=configuration).create_engine()
                except Exception as exc:
                    # Sadece kurulum hatasinin tipini/mesajini logluyoruz (model
                    # adi, dil) - taranan dosya icerigi/hassas veri bu asamada
                    # hic devrede degil, sizdirilacak bir sey yok. Davranis
                    # AYNEN korunuyor: nlp_engine None kalir, analyzer bu
                    # kategoriler icin dogal-dil (spaCy) destegi olmadan kurulur.
                    _logger.warning(
                        "spaCy NLP modeli yuklenemedi (model=%r, dil=%r) - Presidio dogal-dil "
                        "kategorileri (PERSON/ORGANIZATION/DATE_TIME/LOCATION/NRP) bu calisma "
                        "boyunca calismayacak: %s: %s",
                        self.spacy_model, self.language, type(exc).__name__, exc,
                    )
                    nlp_engine = None
            return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine)
        except Exception as exc:
            # None doner, cagiran _analyze() _fallback_analyze()'e (saf
            # regex) duser VE is_degraded=True olur - exporter.py bu
            # calismayi "completed_with_warnings" olarak isaretleyip
            # denetim_kaydi'na yazar (bkz. is_degraded property'si), bu
            # yuzden ERROR seviyesinde logluyoruz: bu artik rutin bir
            # dusus degil, calisma-genelinde bir tespit kapasitesi kaybi.
            _logger.error(
                "Presidio analyzer kurulumu basarisiz oldu - bu calisma boyunca yalnizca "
                "regex tabanli fallback tespit kullanilacak (bkz. _fallback_analyze): %s: %s",
                type(exc).__name__, exc,
            )
            return None

    # Bu dosya icin dogal-dil (PERSON/ORGANIZATION vb.) kategorisi gerekiyor mu?
    # Gerekmiyorsa pahali spaCy analizini atlayip hizli yola gecilebilir.
    def _needs_full_nlp(self, entities: list[str] | None) -> bool:
        if entities is None:
            return True
        return bool(set(entities) & NATURAL_LANGUAGE_ENTITY_TYPES)

    # _needs_full_nlp() False dondugunde kullanilan hizli yol: sadece
    # tokenizasyon yapar, pahali tagger/parser/ner/lemmatizer asamalarini atlar.
    def _fast_nlp_artifacts(self, text: str):
        nlp_engine = self._analyzer.nlp_engine
        nlp = nlp_engine.nlp[self.language]
        doc = nlp.tokenizer(text)
        return NlpArtifacts(
            entities=[],
            tokens=doc,
            tokens_indices=[token.idx for token in doc],
            lemmas=[token.text for token in doc],
            nlp_engine=nlp_engine,
            language=self.language,
            scores=None,
        )

    # _analyze'i detector-genelindeki kilit altinda calistirir (bkz. __init__).
    def _analyze_serialized(self, content: str, entities: list[str] | None) -> list[_AnalyzerResult]:
        with self._analyze_lock:
            return self._analyze(content, entities=entities)

    # Analyzer varsa metni parcalayip her parcayi Presidio ile tarar
    # (ortusen parcalardaki tekrar bulgulari `seen` ile eler); analyzer
    # yoksa _fallback_analyze()'e duser.
    def _analyze(self, content: str, entities: list[str] | None = None) -> list[_AnalyzerResult]:
        if self._analyzer is not None:
            custom_by_entity = {rule.entity_type: rule for rule in self._custom_rules}
            full_nlp = self._needs_full_nlp(entities)
            results: list[_AnalyzerResult] = []
            seen: set[tuple[str, int, int, float]] = set()
            for offset, chunk in chunk_text(content, self.max_analyzer_chars, self.chunk_overlap_chars):
                nlp_artifacts = None if full_nlp else self._fast_nlp_artifacts(chunk)
                analyzed = self._analyzer.analyze(
                    text=chunk, language=self.language, entities=entities, nlp_artifacts=nlp_artifacts
                )
                for result in analyzed:
                    start = offset + result.start
                    end = offset + result.end
                    key = (result.entity_type, start, end, result.score)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(
                        _AnalyzerResult(
                            entity_type=result.entity_type,
                            start=start,
                            end=end,
                            score=result.score,
                            rule=custom_by_entity.get(result.entity_type, None).as_rule_spec()
                            if result.entity_type in custom_by_entity
                            else None,
                            metadata=getattr(result, "recognition_metadata", None),
                        )
                    )
            return sorted(results, key=lambda result: (result.start, -result.score))

        return self._fallback_analyze(content, entities=entities)

    # Presidio kurulu degilse (AnalyzerEngine None) kullanilan saf regex
    # yedegi: birkac yaygin yerlesik kategoriyi (EMAIL/IP/CREDIT_CARD/PHONE)
    # ve tum ozel kurallari duz regex ile tarar.
    def _fallback_analyze(self, content: str, entities: list[str] | None = None) -> list[_AnalyzerResult]:
        results: list[_AnalyzerResult] = []
        builtin_patterns = [
            ("EMAIL_ADDRESS", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", 0.85),
            (
                "IP_ADDRESS",
                r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\b",
                0.85,
            ),
            ("CREDIT_CARD", r"\b(?:\d[ -]*?){13,19}\b", 0.75),
            ("PHONE_NUMBER", r"\b(?:\+?\d[\d\s().-]{7,}\d)\b", 0.65),
        ]
        for entity_type, pattern, score in builtin_patterns:
            if entities is not None and entity_type not in entities:
                continue
            for match in re.finditer(pattern, content):
                results.append(
                    _AnalyzerResult(entity_type=entity_type, start=match.start(), end=match.end(), score=score)
                )
        for rule in self._custom_rules:
            compiled = re.compile(rule.regex_pattern, _compile_flags(rule.regex_flags))
            for match in compiled.finditer(content):
                results.append(
                    _AnalyzerResult(
                        entity_type=rule.entity_type,
                        start=match.start(),
                        end=match.end(),
                        score=rule.confidence_score,
                        rule=rule.as_rule_spec(),
                        metadata={"rule_name": rule.rule_name, "fallback": True},
                    )
                )
        return sorted(results, key=lambda result: (result.start, -result.score))

    # is_allow_list=True olan kurallarin eslestigi araliklari toplar - bu
    # araliklar detect() icinde diger bulgularin uygulanmasini engeller (bilinerek maskelenmeyen degerler).
    def _find_allow_list_spans(self, content: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for rule in self._allow_list_rules:
            compiled = re.compile(rule.regex_pattern, _compile_flags(rule.regex_flags))
            for match in compiled.finditer(content):
                spans.append((match.start(), match.end()))
        return spans

    # rule_engine tarzi regex_flags'i ("i", "m", "s") Presidio'nun beklerdigi
    # satir-basi inline bayrak sozdizimine ((?im)...) cevirir.
    @staticmethod
    def _pattern_for_presidio(rule: PresidioRuleSpec) -> str:
        prefixes: list[str] = []
        for flag in rule.regex_flags or "":
            if flag == "i":
                prefixes.append("i")
            elif flag == "m":
                prefixes.append("m")
            elif flag == "s":
                prefixes.append("s")
        if not prefixes:
            return rule.regex_pattern
        return f"(?{''.join(prefixes)}){rule.regex_pattern}"
