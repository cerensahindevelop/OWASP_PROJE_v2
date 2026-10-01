"""Allocate stable placeholders within one masking job.

New jobs use independent per-prefix counters and (job, value) mappings.
Legacy calls without a job retain their old context-scoped mappings so
historical files remain reversible.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

# sqlite_insert: SQLite'in INSERT ... ON CONFLICT ... DO UPDATE ...
# RETURNING ifadesi icin - _next_counter()'in atomik/yarissiz calismasinin temeli.
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
# encrypt_value/hash_value: orijinal (hassas) degerleri DB'ye yazmadan once
# sifreler ve aranabilir hash'lerini uretir - duz metin secret asla saklanmaz.
from app.core.crypto import encrypt_value, hash_value
from app.db.models import (
    AuditLog,
    FilterRule,
    MaskingContext,
    MaskingRun,
    JobPlaceholderCounter,
    PlaceholderCounter,
    ReviewQueue,
    ValueMapping,
)
# Repository katmani: kural/kisitlama tablolarini okuyan DB erisim siniflari.
from app.repository.file_category_restriction_repository import SqlAlchemyFileCategoryRestrictionRepository
from app.repository.filter_rule_repository import SqlAlchemyFiltreKuraliRepository
# Uc katmanli tespit hattinin ortak sozlesmesi: kayit tipleri, detector
# kayit defteri (registry), orkestratOr ve Match donusum yardimcilari.
from app.services.detectors import (
    DetectionOrchestrator,
    DetectionResult,
    DetectorRegistry,
    RuleBasedDetector,
    detection_to_match,
    synthetic_llm_rule,
)
# Katman 3 (LLM) detector'i, cakisma cozucu, Katman 2 (Presidio) detector'i,
# Katman 1 (regex/sozluk) kural motoru ve span sinir dogrulayicisi -
# mask_text()'in cagirdigi butun tespit/dogrulama zincirinin parcalari.
from app.services.encoded_text_detector import EncodedTextDetector
from app.services.llm_detector import LLMDetector
from app.services.learned_decisions import LearnedDecisionPolicy, LearnedSensitiveDetector
from app.services.overlap_resolver import OverlapConflict, OverlapResolver
from app.services.presidio_detector import PresidioDetector, PresidioRuleSpec
from app.services.rule_engine import JSON_BARE_INTEGER_RE, JSON_NUMERIC_COUNTER_NAMESPACE, Match, RuleSpec, find_matches, make_json_numeric_placeholder
from app.services.string_literal_index import StringLiteralIndex
from app.services.consistency_masking import PRESIDIO_STRUCTURED_TYPES
from app.services.term_classifier import classify_term, is_generic_heuristic_value
from app.services.placeholder_policy import (
    CORPORATE_PLACEHOLDER_PREFIX,
    is_corporate_rule,
    public_placeholder_prefix,
)
from app.services.token_boundary_validator import BoundaryRejection, TokenBoundaryValidator


# get_or_create_mapping'in run-scoped bellek onbellegi - bkz. o fonksiyonun
# dokstring'i. exporter.py bir export calismasi (run) boyunca TEK bir
# MappingCache olusturup her dosya icin ayni nesneyi mask_text()'e gecirir;
# tek DB session/tek thread'de sirali doldurulup okundugu icin race
# condition riski yoktur.
@dataclass
class MappingCache:
    mappings: dict[tuple[int, int | None, str], ValueMapping] = field(default_factory=dict)


# Veritabanindaki aktif (is_active=True) tum kurallari okuyup, kural
# motorunun kullanacagi RuleSpec listesine cevirir.
def load_active_rules(db: Session) -> list[RuleSpec]:
    return SqlAlchemyFiltreKuraliRepository(db).list_active_detection_rules()


# Veritabanindaki aktif Presidio (Katman 2) pattern kurallarini okuyup
# PresidioDetector'un kullanacagi PresidioRuleSpec listesine cevirir.
def load_active_presidio_rules(db: Session) -> list[PresidioRuleSpec]:
    return SqlAlchemyFiltreKuraliRepository(db).list_active_presidio_rules()


# Dosya uzantisina gore Presidio yerlesik kategori kisitlamalarini okur -
# bkz. FileCategoryRestriction model dokumantasyonu.
def load_file_category_restrictions(db: Session) -> dict[str, list[str]]:
    return SqlAlchemyFileCategoryRestrictionRepository(db).load_active_restrictions()


# Verilen proje/sicil/branch uclusune ait context'i bulur; yoksa yeni olusturur.
def get_or_create_context(
    db: Session, project_name: str, sicil_no: str, branch_name: str
) -> MaskingContext:
    stmt = select(MaskingContext).where(
        MaskingContext.project_name == project_name,
        MaskingContext.sicil_no == sicil_no,
        MaskingContext.branch_name == branch_name,
    )
    ctx = db.scalars(stmt).one_or_none()
    if ctx is not None:
        return ctx

    ctx = MaskingContext(
        project_name=project_name, sicil_no=sicil_no, branch_name=branch_name
    )
    db.add(ctx)
    try:
        db.flush()
        return ctx
    except (IntegrityError, OperationalError) as exc:
        # Iki eszamanli istek ayni context'i olusturmaya calisirsa kaybeden
        # buraya duser - rollback edip kazananin satirini okuyup doner.
        db.rollback()
        existing = db.scalars(stmt).one_or_none()
        if existing is None:
            raise exc
        return existing


# Context'i kilitlemeye calisir (SQLite'ta gercek etkisi yok, asil koruma baska yerde - bkz. exporter.py).
def _lock_context(db: Session, context_id: int) -> None:
    db.execute(select(MaskingContext.id).where(MaskingContext.id == context_id).with_for_update())


# Eski calismalar baglam eslemelerini, yeni calismalar kendi JOB kapsamlarini kullanir.
def mapping_scope_for_run(db: Session, run_id: int | None) -> int | None:
    if run_id is None:
        return None
    run = db.get(MaskingRun, run_id)
    if run is None or run.operation_type != "mask":
        raise ValueError("Gecerli bir maskeleme islem kimligi gerekli.")
    return run.id if run.mapping_version == 2 else None


def _next_counter(db: Session, placeholder_prefix: str, run_id: int | None = None) -> int:
    if run_id is not None:
        table = JobPlaceholderCounter.__table__
        stmt = (
            sqlite_insert(JobPlaceholderCounter)
            .values(calisma_id=run_id, on_ek=placeholder_prefix, sayac=1)
            .on_conflict_do_update(
                index_elements=[table.c.calisma_id, table.c.on_ek],
                set_={"sayac": table.c.sayac + 1},
            )
            .returning(table.c.sayac)
        )
        return db.execute(stmt).scalar_one()
    table = PlaceholderCounter.__table__
    stmt = (
        sqlite_insert(PlaceholderCounter)
        .values(on_ek=placeholder_prefix, sayac=1)
        .on_conflict_do_update(
            index_elements=[table.c.on_ek],
            set_={"sayac": table.c.sayac + 1},
        )
        .returning(table.c.sayac)
    )
    return db.execute(stmt).scalar_one()


# Bir orijinal deger icin var olan eslemeyi dondurur; yoksa yeni bir
# placeholder atayip kalici bir ValueMapping kaydi olusturur. `cache`
# verilirse ayni run icinde tekrar eden degerler icin DB'ye tekrar SELECT atilmaz.
def get_or_create_mapping(
    db: Session,
    context_id: int,
    rule: RuleSpec,
    original_value: str,
    cache: dict[tuple[int, int | None, str], ValueMapping] | None = None,
    *,
    numeric: bool = False,
    run_id: int | None = None,
) -> tuple[ValueMapping, bool]:
    """`numeric=True`: bu deger tirnaksiz bir JSON sayi konumunda bulundu -
    placeholder harf-tabanli semayla degil (gecersiz JSON uretir),
    make_json_numeric_placeholder() ile (gecerli bir JSON number olarak)
    uretilir - bkz. rule_engine.py JSON_NUMERIC_PLACEHOLDER_RE dokstringi.

    AYNI orijinal deger projede hem duz metin/tirnakli baglamda (orn. bir
    .env/README/SQL dosyasinda) HEM DE tirnaksiz bir JSON sayi baglaminda
    gecebilir (gercek raporda gozlemlendi: bir sicil numarasi digerinde
    tirnakli/metinsel, digerinde JSON number). Bu iki gorunum FARKLI
    placeholder BICIMLERI gerektirdigi icin (biri harfle, digeri sadece
    rakamla baslar) ayni deger icin ayri, kendi baslarina tutarli iki
    mapping satiri tutulur - arama/tekillik hash'i bu yuzden `numeric`
    bayragini da (namespace olarak) icerir. Placeholder metni asla
    tekrar kullanilmadigi (yer_tutucu_degeri UNIQUE) ve DB'de hem
    sifreli hem duz orijinal deger AYNI kaldigi icin bu, guvenligi
    ZAYIFLATMAZ - sadece "ayni deger = HER ZAMAN ayni TEK placeholder"
    varsayimini "ayni deger + ayni temsil sekli = ayni placeholder"
    olarak inceltir."""
    # A new namespace prevents reusing legacy numeric tokens in new exports.
    # Historical rows remain available for restoring historical files.
    value_hash = hash_value(context_id, f"\x00json-numeric-v2\x00{original_value}" if numeric else original_value)
    if run_id is not None:
        run = db.get(MaskingRun, run_id)
        if run is None or run.context_id != context_id:
            raise ValueError("Maskeleme islem kimligi secilen proje kimligiyle eslesmiyor.")
    scope_id = mapping_scope_for_run(db, run_id)
    cache_key = (context_id, scope_id, value_hash)
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached, False

    existing = db.scalars(
        select(ValueMapping).where(
            ValueMapping.context_id == context_id, ValueMapping.run_id == scope_id,
            ValueMapping.original_value_hash == value_hash
        )
    ).one_or_none()
    placeholder_prefix = public_placeholder_prefix(rule.rule_name, rule.placeholder_prefix)
    if existing is not None and not numeric:
        stored_rule = db.get(FilterRule, existing.rule_id) if existing.rule_id is not None else None
        if (
            stored_rule is not None
            and is_corporate_rule(stored_rule.rule_name)
            and re.fullmatch(rf"{CORPORATE_PLACEHOLDER_PREFIX}_[0-9]+", existing.placeholder_value) is None
        ):
            # Keep the old token and its value intact for historical restores,
            # but retire its lookup key so new exports cannot reuse the title.
            existing.original_value_hash = hash_value(
                context_id, f"\x00legacy-corporate-placeholder\x00{existing.placeholder_value}"
            )
            db.flush()
            existing = None
            placeholder_prefix = CORPORATE_PLACEHOLDER_PREFIX
    if existing is not None:
        if cache is not None:
            cache[cache_key] = existing
        return existing, False

    counter = _next_counter(db, JSON_NUMERIC_COUNTER_NAMESPACE if numeric else placeholder_prefix, scope_id)
    placeholder_value = (
        make_json_numeric_placeholder(counter) if numeric else f"{placeholder_prefix}_{counter}"
    )
    mapping = ValueMapping(
        context_id=context_id,
        run_id=scope_id,
        rule_id=rule.id,
        original_value_encrypted=encrypt_value(original_value),
        # BILEREK DUZ METIN - bkz. ValueMapping.original_value_plain
        # kolon yorumundaki guvenlik uyarisi (app/db/models.py).
        original_value_plain=original_value,
        original_value_hash=value_hash,
        placeholder_value=placeholder_value,
    )
    db.add(mapping)
    db.flush()
    if cache is not None:
        cache[cache_key] = mapping
    return mapping, True


# Klasor/dosya yolundaki aktif kurumsal terim/alias ve runtime kimliklerini,
# icerik maskelemesiyle AYNI matcher/oncelik/mapping kayitlarini kullanarak
# maskeler. Genel secret/IP vb. regex'ler yollarda calistirilmaz: yol kapsami
# kurumsal kimlik ile proje/sicil/branch degerleriyle sinirlidir.
def mask_relative_path(
    db: Session,
    context: MaskingContext,
    relative_path: Path,
    runtime_params: dict[str, str],
    rules: list[RuleSpec] | None = None,
    mapping_cache: MappingCache | None = None,
    *,
    run_id: int | None = None,
) -> tuple[Path, list[ValueMapping]]:
    rules = rules if rules is not None else load_active_rules(db)

    def _is_path_rule(rule: RuleSpec) -> bool:
        if is_corporate_rule(rule.rule_name):
            return True
        if rule.pattern_type != "parametric":
            return False
        value = runtime_params.get(rule.category)
        if not value:
            return False
        # `main`, `test`, `app`, `src` gibi yaygin proje/branch adlari yol
        # baglaminda kimlik kaniti degildir. Ornegin branch=main degeri
        # Maven/Gradle'in standart `src/main` klasorunu maskeleyip derlemeyi
        # bozmamalidir. Sicil degeri ise salt sayisal olabilen gercek bir
        # kimliktir; onun mevcut teknik-baglam filtresi rule_engine'de kalir.
        if rule.category != "sicil_no" and classify_term(value).status != "ok":
            return False
        return True

    path_rules = [
        rule
        for rule in rules
        if _is_path_rule(rule)
    ]
    if not path_rules:
        return relative_path, []

    mappings_used: list[ValueMapping] = []
    masked_parts: list[str] = []

    # Tek bir tarama turu: `text` icinde, `token_spans` (bu bilesene daha
    # once yazilmis token'lar) ile CAKISMAYAN eslesmeleri maskeler.
    def _replacements(text: str, token_spans: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
        candidates, _already_masked = find_matches(path_rules, text, runtime_params)
        detection_candidates = []
        for match in candidates:
            # Bilesik token'lar (orn. mask_x_7OMEGA) \b tasimadigi icin
            # PLACEHOLDER_RE onlari korumaz; "kurumsal"/"ifade" gibi bir terim
            # token'in ICINDE eslesebilir. Kendi yazdigimiz span'lere dokunan
            # her eslesme atlanir - token'i tasan bir kalinti varsa son
            # kontrol (find_leaked_terms) fail-closed yakalar.
            if match.start >= match.end or any(
                match.start < right and left < match.end for left, right in token_spans
            ):
                continue
            # A token ends with a numeric counter. Leaving source digits
            # adjacent would turn token _1 + "123" into an ambiguous _1123.
            # Store the exact term + digit suffix as one reversible value.
            # Extend BEFORE overlap resolution so a separate numeric match
            # cannot cause overlapping replacements. isdecimal agrees with
            # the path decoder's Unicode-aware (?!\d) counter boundary.
            end = match.end
            while end < len(text) and text[end].isdecimal():
                end += 1
            detection_candidates.append(DetectionResult(
                deger=text[match.start:end],
                tip=match.rule.category,
                guven_seviyesi="yuksek",
                kaynak_motor="dictionary",
                gerekce=f"rule={match.rule.rule_name}",
                start=match.start,
                end=end,
                rule=match.rule,
            ))
        resolved, _conflicts = OverlapResolver().resolve(detection_candidates)

        replacements: list[tuple[int, int, str]] = []
        for result in resolved:
            if result.rule is None or result.start is None or result.end is None:
                continue
            # Case-insensitive matching decides whether the occurrence is
            # sensitive; it must never decide which spelling is persisted.
            # Distinct source spellings need distinct exact mappings so path
            # unmask can reproduce every component character-for-character.
            value_for_storage = result.deger
            mapping, _created = get_or_create_mapping(
                db,
                context.id,
                result.rule,
                value_for_storage,
                cache=mapping_cache.mappings if mapping_cache is not None else None,
                run_id=run_id,
            )
            mappings_used.append(mapping)
            replacements.append((result.start, result.end, mapping.placeholder_value))
        return replacements

    for part in relative_path.parts:
        # Token'in sonundaki sayac rakami, kaynakta OLMAYAN bir rakam->harf
        # siniri yaratir: "VEGAOMEGA" icinde OMEGA'nin solunda sinir yoktur
        # (buyuk->buyuk harf), ama VEGA maskelenince "mask_x_7OMEGA" olur ve
        # OMEGA artik eslesir. Son kontrol maskeli yolu taradigi icin bilesen
        # de yeni eslesme kalmayana kadar yeniden taranir. Her tur korunmasiz
        # metni token'a cevirdigi (ve bos eslesmeler atlandigi) icin
        # korunmasiz karakter sayisi kesin azalir - dongu sonludur.
        masked_part = part
        token_spans: list[tuple[int, int]] = []
        while replacements := _replacements(masked_part, token_spans):
            pieces: list[str] = []
            new_spans: list[tuple[int, int]] = []
            length = cursor = 0
            edits = [(start, end, None) for start, end in token_spans] + replacements
            for start, end, placeholder in sorted(edits, key=lambda item: item[0]):
                gap = masked_part[cursor:start]
                segment = masked_part[start:end] if placeholder is None else placeholder
                pieces += (gap, segment)
                length += len(gap)
                new_spans.append((length, length + len(segment)))
                length += len(segment)
                cursor = end
            pieces.append(masked_part[cursor:])
            masked_part, token_spans = "".join(pieces), new_spans
        masked_parts.append(masked_part)

    return Path(*masked_parts), mappings_used


# Bir bulgunun cevresinden (radius kadar) baglam metni keser - review
# kuyrugunda bulguyu insan gozuyle degerlendirmeyi kolaylastirmak icin.
def _context_excerpt(text: str, start: int | None, end: int | None, radius: int = 80) -> str | None:
    if start is None or end is None:
        return None
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


# Karakter ofsetinden 1-tabanli satir numarasina cevirir - review kuyrugu kaydinda gosterilir.
def _line_number(text: str, start: int | None) -> int | None:
    if start is None:
        return None
    return text.count("\n", 0, start) + 1


# Dusuk/orta guvenli LLM bulgusunu (metne uygulamadan) review_queue'ya
# ekler - insan onayi/reddi bekleyecek.
def _enqueue_review(
    db: Session,
    *,
    run_id: int | None,
    file_path: str | None,
    result: DetectionResult,
    text: str,
) -> None:
    db.add(
        ReviewQueue(
            run_id=run_id,
            file_path=file_path or "",
            line_number=_line_number(text, result.start),
            found_value=result.deger,
            entity_type=result.tip,
            confidence_level=result.guven_seviyesi,
            reason=result.gerekce,
            surrounding_context=_context_excerpt(text, result.start, result.end),
        )
    )
    if run_id is not None:
        db.add(AuditLog(
            run_id=run_id, file_path=file_path or "", action="matched",
            detail=(f"finding={result.tip} ai_result=uncertain "
                    f"ai_confidence={result.guven_seviyesi} user_decision=pending"),
        ))


# Kural setinden bir DetectionOrchestrator kurar (pahali kurulum - run basina bir kez cagrilmali).
def build_orchestrator(
    rules: list[RuleSpec],
    runtime_params: dict[str, str] | None,
    presidio_rules: list[PresidioRuleSpec] | None = None,
    category_restrictions: dict[str, list[str]] | None = None,
    decision_policy: LearnedDecisionPolicy | None = None,
) -> DetectionOrchestrator:
    # Yerel katmanlar (sozluk + ogrenilmis karar + Presidio): hem ana taramada
    # hem de kodlanmis metnin cozulmus hali icin (EncodedTextDetector) ayni
    # ornekler kullanilir - Presidio/spaCy kurulumu pahalidir.
    local_detectors = [RuleBasedDetector(rules, runtime_params)]
    if decision_policy is not None and decision_policy.sensitive:
        local_detectors.append(LearnedSensitiveDetector(decision_policy.sensitive))
    local_detectors.append(
        PresidioDetector(
            presidio_rules or [],
            language=settings.presidio.language,
            spacy_model=settings.presidio.spacy_model,
            use_builtin_recognizers=settings.presidio.use_builtin_recognizers,
            category_restrictions=category_restrictions,
            entropy_threshold=settings.presidio.entropy_threshold,
            max_analyzer_chars=settings.presidio.max_analyzer_chars,
            chunk_overlap_chars=settings.presidio.chunk_overlap_chars,
        )
    )
    local_registry = DetectorRegistry()
    registry = DetectorRegistry()
    for detector in local_detectors:
        local_registry.register(detector)
        registry.register(detector)
    registry.register(EncodedTextDetector(DetectionOrchestrator(local_registry)))
    # pattern_type='llm' kurallarinin aciklamalarini (kural-ekle --aciklama ile
    # eklenen tarama talimatlari) vLLM promptuna gercekten dahil et - bkz.
    # llm_recognizer._augment_prompt. Eskiden bu kurallar sadece DB'de
    # dururdu, LLMDetector'a hic iletilmiyordu.
    llm_instructions = [r.description for r in rules if r.pattern_type == "llm" and r.description]
    registry.register(LLMDetector(settings.vllm, extra_instructions=llm_instructions))
    orchestrator = DetectionOrchestrator(registry, encoded_blob_min_chars=settings.scan.encoded_blob_min_chars)
    orchestrator.decision_policy = decision_policy
    return orchestrator


# Bir export/mask calismasi boyunca degismeyen, dosya basina tekrar tekrar
# kullanilan alanlari bir arada tutar (context/orchestrator/mapping_cache vb.).
@dataclass(frozen=True)
class MaskingRunContext:
    context: MaskingContext
    run_id: int | None = None
    runtime_params: dict[str, str] | None = None
    orchestrator: DetectionOrchestrator | None = None
    mapping_cache: MappingCache | None = None


# Bir dosyanin tespit asamasinin (DB'siz, saf) sonucu - detect_matches() uretir, apply_detections() kullanir.
@dataclass(frozen=True)
class DetectionOutcome:
    matches: list[Match]
    review_results: list[DetectionResult]
    llm_errors: list[str]
    already_masked_spans: list[tuple[int, int]]
    overlap_conflicts: list[OverlapConflict]
    boundary_rejections: list[BoundaryRejection]
    suppressed_results: list[tuple[DetectionResult, int]] = field(default_factory=list)
    # bkz. detectors.DetectorOutput.crashes dokstringi.
    detector_crashes: list[str] = field(default_factory=list)
    # auto_mask_min_confidence altinda kalan ve low_confidence_action=ignore
    # ile onaya GONDERILMEYEN LLM adaylari - sadece denetim kaydina yazilir.
    ignored_llm_results: list[DetectionResult] = field(default_factory=list)
    # bkz. detectors.DetectorOutput.notices dokstringi.
    detector_notices: list[str] = field(default_factory=list)
    # Kodlanmis metnin icinde bulunan hassas veri (bkz. encoded_text_detector) -
    # dosya karantinaya alinir, deger kodlanmis blok icinde maskelenmez.
    encoded_leaks: list[str] = field(default_factory=list)


_CONFIDENCE_RANK = {"dusuk": 0, "orta": 1, "yuksek": 2}


def _llm_confidence_route(confidence: str) -> str:
    """Return 'mask', 'review' or 'ignore' for an LLM finding.

    Maskeleme geri donusumlu ve round-trip ile dogrulandigi icin fazladan
    maskelemenin maliyeti dusuktur; her belirsiz bulguyu insan onayina
    gondermek ise dosyalarin buyuk kismini bekletir. Esik ve dusuk-guven
    davranisi VLLM_AUTO_MASK_MIN_CONFIDENCE / VLLM_LOW_CONFIDENCE_ACTION
    ile ayarlanir. Post-mask LLM denetimi her dosyada yine calisir.
    """
    vllm = settings.vllm
    threshold = _CONFIDENCE_RANK.get(getattr(vllm, "auto_mask_min_confidence", "orta"), 1)
    if _CONFIDENCE_RANK.get(confidence, 0) >= threshold:
        return "mask"
    return "review" if getattr(vllm, "low_confidence_action", "ignore") == "review" else "ignore"


# Presidio'nun NER (dogal dil) bulgusu mu ve bayrak acikken generic mi?
# Yapisal tipler (e-posta, IP, IBAN, kart, kripto) ve DB'deki ozel Presidio
# kurallari (pattern_type=presidio) deterministiktir; bu filtreden gecmez.
def _is_generic_presidio_ner(result: DetectionResult) -> bool:
    if not settings.scan.generic_compound_filter or result.kaynak_motor != "katman2_presidio":
        return False
    if (result.tip or "").upper() in PRESIDIO_STRUCTURED_TYPES:
        return False
    if result.rule is not None and result.rule.pattern_type == "presidio":
        return False
    return is_generic_heuristic_value(result.deger, compound=True)


# Bir metni tum katmanlara (Rule/Presidio/LLM) karsi tarar, cakismalari cozer,
# span sinirlarini dogrular - ama HICBIR DB yazmasi yapmaz (saf/DB'siz yari).
async def detect_matches(
    orchestrator: DetectionOrchestrator,
    text: str,
    metadata: dict | None = None,
) -> DetectionOutcome:
    metadata = metadata or {}
    detector_output = await orchestrator.scan(text, metadata=metadata)
    already_masked_spans = detector_output.already_masked_spans

    # 1) Katmanlar arasi cakismalari coz (yuksek confidence > uzun aralik >
    #    kaynak onceligi kazanir) - kaybedenler METNE UYGULANMAZ, sessizce
    #    de silinmez (bkz. asagida denetim_kaydi'na yazilmalari).
    resolved_results, overlap_conflicts = OverlapResolver().resolve(detector_output.results)
    # 2) Kazananlarin span'lerinin bir identifier/string-literal'in
    #    ortasindan baslayip bitmedigini dogrula; gerekirse genislet/
    #    daralt, mantiksiz durumlarda tamamen reddet.
    validated_results, boundary_rejections = TokenBoundaryValidator().validate(
        text, resolved_results, file_path=str(metadata.get("file_path", ""))
    )
    # 3) Genisletme adimi (2) NADIREN iki span'i yeniden cakistirabilir
    #    (orn. bir span identifier sinirina genisledi ve simdi komsu baska
    #    bir span'le cakisiyor) - bunu yakalamak icin ayni oncelik
    #    kurallariyla KISA bir ikinci cakisma cozumu gecisi daha yapiyoruz.
    final_results, post_expansion_conflicts = OverlapResolver().resolve(validated_results)
    overlap_conflicts = overlap_conflicts + post_expansion_conflicts

    matches: list[Match] = []
    review_results: list[DetectionResult] = []
    ignored_llm_results: list[DetectionResult] = []
    suppressed_results: list[tuple[DetectionResult, int]] = []
    policy = getattr(orchestrator, "decision_policy", None)
    for result in final_results:
        if result.kaynak_motor in ("llm", "katman2_presidio") and not any(
            ch.isalnum() for ch in (result.deger or "")
        ):
            # `&&`, `@`, `=` gibi harf/rakam icermeyen olasiliksal bulgular
            # hassas deger olamaz; maskelenirse kodu bozar.
            ignored_llm_results.append(result)
            continue
        suppression = policy.is_suppressed(result, str(metadata.get("file_path", ""))) if (
            policy is not None and result.kaynak_motor == "llm"
        ) else None
        if suppression is not None:
            suppressed_results.append((result, suppression.id))
            continue
        if _is_generic_presidio_ner(result):
            # SCAN_GENERIC_COMPOUND_FILTER: Presidio NER'in UserService gibi
            # tamamen genel parcalardan olusan bir adi ORGANIZATION sanmasi.
            ignored_llm_results.append(result)
            continue
        if result.kaynak_motor == "llm":
            route = _llm_confidence_route(result.guven_seviyesi)
            if is_generic_heuristic_value(result.deger, compound=settings.scan.generic_compound_filter):
                # `default`, `export`, `client` gibi genel/anahtar kelime
                # degerler kodu bozar ve hassas degildir.
                route = "ignore"
            if route == "review":
                review_results.append(result)
                continue
            if route == "ignore":
                ignored_llm_results.append(result)
                continue
            result = DetectionResult(
                deger=result.deger,
                tip=result.tip,
                guven_seviyesi=result.guven_seviyesi,
                kaynak_motor=result.kaynak_motor,
                gerekce=result.gerekce,
                start=result.start,
                end=result.end,
                rule=synthetic_llm_rule(result.tip),
                raw_result=result.raw_result,
            )
        matches.append(detection_to_match(result))

    return DetectionOutcome(
        matches=matches,
        review_results=review_results,
        llm_errors=list(detector_output.errors),
        already_masked_spans=already_masked_spans,
        overlap_conflicts=overlap_conflicts,
        boundary_rejections=boundary_rejections,
        suppressed_results=suppressed_results,
        detector_crashes=list(detector_output.crashes),
        ignored_llm_results=ignored_llm_results,
        detector_notices=list(detector_output.notices),
        encoded_leaks=list(detector_output.encoded_leaks),
    )


# detect_matches()'in sonucunu DB'ye yazar (mapping/audit_log) ve metni placeholder'larla degistirir.
def apply_detections(
    db: Session,
    run_ctx: MaskingRunContext,
    text: str,
    outcome: DetectionOutcome,
    file_path: str | None = None,
) -> tuple[str, list[ValueMapping]]:
    context = run_ctx.context
    run_id = run_ctx.run_id
    runtime_params = run_ctx.runtime_params
    mapping_cache = run_ctx.mapping_cache

    # Refuse an invalid edit plan before any mapping/audit mutation. Applying
    # overlapping slices with a moving cursor can duplicate or drop source.
    cursor = 0
    for match in sorted(outcome.matches, key=lambda item: item.start):
        if not 0 <= match.start < match.end <= len(text) or match.start < cursor:
            raise ValueError("gecersiz veya cakisan maskeleme araligi")
        cursor = match.end

    _lock_context(db, context.id)

    for llm_error in outcome.llm_errors:
        if run_id is not None:
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="error",
                    detail=llm_error,
                )
            )
    for result in outcome.review_results:
        _enqueue_review(db, run_id=run_id, file_path=file_path, result=result, text=text)

    if run_id is not None:
        for notice in outcome.detector_notices:
            db.add(AuditLog(run_id=run_id, file_path=file_path or "", action="skipped", detail=notice))
        for result in outcome.ignored_llm_results:
            # Acik deger yazilmaz; sadece tur/guven/konum.
            db.add(AuditLog(
                run_id=run_id, file_path=file_path or "", action="skipped",
                detail=(f"finding={result.tip} ai_confidence={result.guven_seviyesi} "
                        f"source={result.kaynak_motor} line={_line_number(text, result.start)} final=not_masked"),
            ))
        for result, decision_id in outcome.suppressed_results:
            db.add(AuditLog(
                run_id=run_id, file_path=file_path or "", action="skipped",
                detail=(f"finding={result.tip} ai_confidence={result.guven_seviyesi} "
                        f"suppression_rule_id={decision_id} final=not_sensitive"),
            ))

    if run_id is not None:
        for start, end in outcome.already_masked_spans:
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="skipped",
                    detail=f"already placeholder-formatted: '{text[start:end]}'",
                )
            )
        for conflict in outcome.overlap_conflicts:
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="cakisma",
                    detail=conflict.reason,
                )
            )
        for rejection in outcome.boundary_rejections:
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="sinir_ihlali",
                    detail=rejection.reason,
                )
            )

    # JSON dosyalarinda tirnaksiz bir sayi konumuna harf-tabanli bir
    # placeholder yazmak gecersiz JSON uretir (bkz. rule_engine.py
    # JSON_NUMERIC_PLACEHOLDER_RE dokstringi) - bu yuzden hangi eslesmelerin
    # boyle bir konumda oldugunu (tirnak DISINDA + JSON dosyasi + tirnaksiz
    # gecerli bir tamsayi) bir kere hesapliyoruz.
    is_json_file = Path(file_path or "").suffix.lower().lstrip(".") == "json"
    string_index = StringLiteralIndex(text, file_path or "") if is_json_file else None

    mappings_used: list[ValueMapping] = []
    replacements: list[tuple[int, int, str]] = []
    for match in outcome.matches:
        # Case-insensitive matching only answers "is this sensitive?".
        # Reversible storage always uses the exact source slice. The mapping
        # hash is already case-sensitive, so each distinct spelling naturally
        # receives its own placeholder and can be restored losslessly.
        value_for_storage = text[match.start:match.end]
        numeric = bool(
            string_index is not None
            and string_index.enclosing(match.start, match.end) is None
            and JSON_BARE_INTEGER_RE.fullmatch(value_for_storage)
        )

        mapping, _created = get_or_create_mapping(
            db, context.id, match.rule, value_for_storage,
            cache=mapping_cache.mappings if mapping_cache is not None else None,
            numeric=numeric,
            run_id=run_ctx.run_id,
        )
        mappings_used.append(mapping)
        replacements.append((match.start, match.end, mapping.placeholder_value))

        if run_id is not None:
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="matched",
                    detail=f"rule={match.rule.rule_name}",
                )
            )
            db.add(
                AuditLog(
                    run_id=run_id,
                    file_path=file_path or "",
                    action="replaced",
                    detail=f"placeholder={mapping.placeholder_value}",
                )
            )

    # Metni tek pass'te (parca parca) yeniden olusturur - dongu icinde
    # text[:start]+...+text[end:] tekrarlamak buyuk dosyalarda cok yavas olurdu.
    segments: list[str] = []
    cursor = 0
    for start, end, placeholder_value in sorted(replacements, key=lambda item: item[0]):
        segments.append(text[cursor:start])
        segments.append(placeholder_value)
        cursor = end
    segments.append(text[cursor:])
    masked_text = "".join(segments)

    return masked_text, mappings_used


# Tek bir metni tarayip maskeler: eslesen her degeri placeholder ile degistirir,
# maskelenmis metni ve kullanilan mapping'leri dondurur. exporter.py'nin toplu
# hattı bunun yerine detect_matches/apply_detections'i dogrudan kullanir (gercek
# concurrency icin); bu fonksiyon review_service.py ve tekil-dosya testleri icindir.
def mask_text(
    db: Session,
    run_ctx: MaskingRunContext,
    text: str,
    file_path: str | None = None,
    detection_metadata: dict | None = None,
) -> tuple[str, list[ValueMapping]]:
    orchestrator = run_ctx.orchestrator
    if orchestrator is None:
        rules = load_active_rules(db)
        presidio_rules = load_active_presidio_rules(db)
        category_restrictions = load_file_category_restrictions(db)
        policy = LearnedDecisionPolicy.load(db, run_ctx.context.id)
        orchestrator = build_orchestrator(
            rules, run_ctx.runtime_params, presidio_rules, category_restrictions,
            decision_policy=policy,
        )
    metadata = dict(detection_metadata or {})
    metadata["file_path"] = file_path or ""

    outcome = asyncio.run(detect_matches(orchestrator, text, metadata))
    return apply_detections(db, run_ctx, text, outcome, file_path=file_path)
