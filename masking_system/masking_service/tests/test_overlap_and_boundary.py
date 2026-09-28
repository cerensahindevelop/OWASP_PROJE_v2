"""Bolum 2 duzeltmelerinin regresyon testleri:
  - OverlapResolver: cakisan DetectionResult'lardan dogru olanin secildigini,
    kaybedenin METNE UYGULANMADIGINI (sadece kayit disi birakildigini) dogrular.
  - TokenBoundaryValidator: bug_report'taki syntax_corruption/
    string_literal_corruption ornekleriyle birebir esleserek, artik olusmadigini
    ve IP/e-posta gibi mesru degerlerin YANLISLIKLA reddedilmedigini dogrular.
  - Entegrasyon: gercek mask_text() akisiyla (canli DB'ye karsi) uretilen
    maskelenmis Python ciktisinin ast.parse() ile gecerli oldugunu dogrular.
"""

from __future__ import annotations

import asyncio
import ast
import re

import pytest

from app.services.detectors import DetectionOrchestrator, DetectionResult, DetectorRegistry, RuleBasedDetector
from app.services.mapping_service import detect_matches
from app.services.overlap_resolver import OverlapResolver
from app.services.rule_engine import RuleSpec
from app.services.token_boundary_validator import TokenBoundaryValidator


def _result(start: int, end: int, confidence: str, source: str, tip: str = "GENERIC") -> DetectionResult:
    return DetectionResult(deger=f"<{start}:{end}>", tip=tip, guven_seviyesi=confidence, kaynak_motor=source, start=start, end=end)


def _rule(
    rule_name: str,
    pattern_type: str,
    *,
    priority: int = 100,
    category: str = "test",
) -> RuleSpec:
    return RuleSpec(
        id=1,
        rule_name=rule_name,
        category=category,
        pattern_type=pattern_type,
        regex_pattern=r"test" if pattern_type == "regex" else None,
        regex_flags=None,
        placeholder_prefix="TEST",
        priority=priority,
    )


# --------------------------------------------------------------------------
# OverlapResolver
# --------------------------------------------------------------------------


class TestOverlapResolver:
    def test_good_example_from_spec_high_confidence_wins(self):
        # Spec'teki good_example: (10,25,yuksek,katman2) vs (15,20,orta,llm)
        wide_high = _result(10, 25, "yuksek", "katman2_presidio")
        narrow_medium = _result(15, 20, "orta", "llm")

        accepted, conflicts = OverlapResolver().resolve([wide_high, narrow_medium])

        assert accepted == [wide_high]
        assert len(conflicts) == 1
        assert conflicts[0].winner is wide_high
        assert conflicts[0].loser is narrow_medium
        # Kayip logu ham degeri (deger alanini) ICERMEMELI.
        assert "<15:20>" not in conflicts[0].reason
        assert "<10:25>" not in conflicts[0].reason

    def test_deterministic_authority_beats_llm_confidence(self):
        # Onayli/deterministik kaynak, sezgisel LLM bulgusundan daha yuksek
        # otoritedir; confidence yalnizca ayni otorite seviyesinde karsilastirilir.
        low_confidence_dictionary = _result(0, 10, "dusuk", "dictionary")
        high_confidence_llm = _result(2, 8, "yuksek", "llm")

        accepted, conflicts = OverlapResolver().resolve([low_confidence_dictionary, high_confidence_llm])

        assert accepted == [low_confidence_dictionary]
        assert conflicts[0].winner is low_confidence_dictionary

    def test_corporate_full_term_beats_short_runtime_project_value(self):
        text = "ALFA IC DENETIM VE GUVENLIK BASKANLIGI"
        corporate = DetectionResult(
            deger=text,
            tip="kurum_kimligi",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=len(text),
            rule=_rule("kurumsal_terim_kurum_123", "regex", priority=281),
        )
        runtime_start = text.index("IC DENETIM")
        runtime = DetectionResult(
            deger="IC DENETIM",
            tip="project_name",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=runtime_start,
            end=runtime_start + len("IC DENETIM"),
            rule=_rule("project_name", "parametric", priority=1, category="project_name"),
        )

        accepted, conflicts = OverlapResolver().resolve([runtime, corporate])

        assert accepted == [corporate]
        assert len(conflicts) == 1
        assert conflicts[0].winner is corporate
        assert conflicts[0].loser is runtime

    def test_runtime_identity_beats_overlapping_generic_regex(self):
        runtime = DetectionResult(
            deger="Poseidon",
            tip="project_name",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=8,
            rule=_rule("project_name", "parametric", priority=50, category="project_name"),
        )
        generic = DetectionResult(
            deger="Poseidon",
            tip="generic",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=8,
            rule=_rule("generic_regex", "regex", priority=1),
        )

        accepted, _conflicts = OverlapResolver().resolve([generic, runtime])

        assert accepted == [runtime]

    def test_specific_secret_rule_beats_longer_generic_catch_all(self):
        specific = DetectionResult(
            deger="ghp_" + "A" * 40,
            tip="secret",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=9,
            end=53,
            rule=_rule("github_token", "regex", priority=12),
        )
        generic = DetectionResult(
            deger="TOKEN = '" + "ghp_" + "A" * 40 + "'",
            tip="secret",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=54,
            rule=_rule("generic_secret_assignment", "regex", priority=20),
        )

        accepted, conflicts = OverlapResolver().resolve([generic, specific])

        assert accepted == [specific]
        assert conflicts[0].loser is generic

    def test_length_tiebreaks_equal_confidence(self):
        short = _result(5, 10, "orta", "llm")
        long_ = _result(0, 20, "orta", "llm")

        accepted, conflicts = OverlapResolver().resolve([short, long_])

        assert accepted == [long_]
        assert conflicts[0].winner is long_
        assert conflicts[0].loser is short

    def test_source_priority_tiebreaks_equal_confidence_and_length(self):
        dictionary_result = _result(0, 10, "yuksek", "dictionary")
        presidio_result = _result(0, 10, "yuksek", "katman2_presidio")
        llm_result = _result(0, 10, "yuksek", "llm")

        accepted, conflicts = OverlapResolver().resolve([presidio_result, llm_result, dictionary_result])

        assert accepted == [dictionary_result]
        assert len(conflicts) == 2

    def test_lower_rule_priority_number_breaks_equal_authority_tie(self):
        first = DetectionResult(
            deger="same",
            tip="A",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=4,
            rule=_rule("regex_late", "regex", priority=200),
        )
        preferred = DetectionResult(
            deger="same",
            tip="B",
            guven_seviyesi="yuksek",
            kaynak_motor="dictionary",
            start=0,
            end=4,
            rule=_rule("regex_early", "regex", priority=10),
        )

        accepted, _conflicts = OverlapResolver().resolve([first, preferred])

        assert accepted == [preferred]

    def test_non_overlapping_results_both_kept(self):
        a = _result(0, 5, "orta", "llm")
        b = _result(10, 15, "dusuk", "llm")

        accepted, conflicts = OverlapResolver().resolve([a, b])

        assert accepted == [a, b]
        assert conflicts == []

    def test_chain_overlap_resolves_deterministically(self):
        # A ve B ortusuyor, B ve C ortusuyor, A ve C ortusmuyor.
        # B en yuksek oncelikli (yuksek confidence) - kazanmali; A ve C
        # (ikisi de orta) kaybetmeli.
        a = _result(0, 12, "orta", "llm")
        b = _result(10, 20, "yuksek", "llm")
        c = _result(18, 30, "orta", "llm")

        accepted, conflicts = OverlapResolver().resolve([a, b, c])

        assert accepted == [b]
        assert len(conflicts) == 2
        assert {conflict.loser for conflict in conflicts} == {a, c}


@pytest.mark.parametrize(
    "text",
    [
        "ALFA IC DENETIM VE GUVENLIK BASKANLIGI",
        'kurum.isim="ALFA IC DENETIM VE GUVENLIK BASKANLIGI"',
    ],
)
def test_detection_pipeline_masks_full_corporate_term_before_alias_and_runtime(text):
    full_value = "ALFA IC DENETIM VE GUVENLIK BASKANLIGI"
    rules = [
        _rule("project_name", "parametric", priority=1, category="project_name"),
        RuleSpec(
            id=2,
            rule_name="kurumsal_terim_kurum_ana_123",
            category="kurum_kimligi",
            pattern_type="regex",
            regex_pattern=re.escape(full_value),
            regex_flags="i",
            placeholder_prefix="mask_kurum",
            priority=281,
        ),
        RuleSpec(
            id=3,
            rule_name="kurumsal_terim_kurum_alias_456",
            category="kurum_kimligi",
            pattern_type="regex",
            regex_pattern=re.escape("ALFA"),
            regex_flags="i",
            placeholder_prefix="mask_kurum",
            priority=291,
        ),
    ]
    registry = DetectorRegistry()
    registry.register(RuleBasedDetector(rules, {"project_name": "ic denetim"}))
    orchestrator = DetectionOrchestrator(registry)

    outcome = asyncio.run(detect_matches(orchestrator, text))

    assert len(outcome.matches) == 1
    assert outcome.matches[0].rule.rule_name == "kurumsal_terim_kurum_ana_123"
    assert outcome.matches[0].original_value == full_value
    assert len(outcome.overlap_conflicts) == 2


# --------------------------------------------------------------------------
# TokenBoundaryValidator - bug_report senaryolari
# --------------------------------------------------------------------------


class TestTokenBoundaryValidatorBareCode:
    def test_method_call_rejected_requests_get(self):
        text = "results = requests.get(url, headers=headers)"
        match_start = text.index("get")
        match_end = match_start + len("get")
        result = _result(match_start, match_end, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1
        assert "kod ifadesinin" in rejections[0].reason

    def test_partial_domain_match_expands_then_rejects_app_route(self):
        # Presidio'nun gercek hatasi: "app.ro" sadece kismi eslesiyor
        # (TLD='ro'). Genisletme "app.route" tam identifier'ina ulasmali,
        # SONRA "(" ile devam ettigi icin reddedilmeli.
        text = "@app.route('/req_1')"
        match_start = text.index("app.ro")
        match_end = match_start + len("app.ro")
        result = _result(match_start, match_end, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1

    def test_partial_domain_match_psycopg2_connect_rejected(self):
        text = 'conn = psycopg2.connect(host="db")'
        match_start = text.index("psycopg2.co")
        match_end = match_start + len("psycopg2.co")
        result = _result(match_start, match_end, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1

    def test_partial_domain_match_redis_strictredis_rejected(self):
        text = 'redis_db = redis.StrictRedis(host="localhost", port=6379, db=0)'
        # 'redis.St' (orijinal bug_report ornegindeki gibi, case-sensitive)
        match_start = text.index("redis.St")
        match_end = match_start + len("redis.St")
        result = _result(match_start, match_end, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1

    def test_full_token_app_run_rejected(self):
        text = "app.run(debug=True)"
        match_start = 0
        match_end = len("app.run")
        result = _result(match_start, match_end, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1

    def test_ip_address_not_falsely_rejected(self):
        text = "SERVER_IP = 192.168.10.55\n"
        match_start = text.index("192.168.10.55")
        match_end = match_start + len("192.168.10.55")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="ipv4_address")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        assert (accepted[0].start, accepted[0].end) == (match_start, match_end)

    def test_email_address_not_falsely_rejected(self):
        text = "# Iletisim: ayse.yilmaz@example.com\n"
        match_start = text.index("ayse.yilmaz@example.com")
        match_end = match_start + len("ayse.yilmaz@example.com")
        result = _result(match_start, match_end, "yuksek", "katman2_presidio", tip="EMAIL_ADDRESS")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1

    def test_mid_identifier_expands_when_not_code_expression(self):
        text = "config_value_98765 = load()"
        match_start = text.index("value_987")
        match_end = match_start + len("value_987")
        result = _result(match_start, match_end, "orta", "llm", tip="KURUM_JARGONU")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        full_start = text.index("config_value_98765")
        full_end = full_start + len("config_value_98765")
        assert (accepted[0].start, accepted[0].end) == (full_start, full_end)
        assert accepted[0].deger == "config_value_98765"

    def test_mid_identifier_expands_across_turkish_letters(self):
        """token_boundary_validator._WORD_CHARS, rule_engine.PLACEHOLDER_RE'nin
        ("\\b" - Python'da Unicode-farkli, Turkce bir harf de \\w sayilir)
        tanidigi kumeyle TUTARLI olmali. Degilse bir bulgu, Turkce harfle
        devam eden bir identifier'in ortasinda KUCUK kalir (span Turkce
        harfe gelince genisleme durur), placeholder o Turkce harfe bitisik
        yazilir - ve unmask'ta PLACEHOLDER_RE bunu artik bir placeholder
        olarak TANIMAZ (round-trip sessizce bozulur). Bkz.
        token_boundary_validator.py _WORD_CHARS dokstringi."""
        text = "sicilİşlemi = kaydet()"
        match_start = text.index("sicil")
        match_end = match_start + len("sicil")
        result = _result(match_start, match_end, "orta", "llm", tip="KURUM_JARGONU")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        assert accepted[0].deger == "sicilİşlemi"


class TestTokenBoundaryValidatorIdentifierAwareDictionary:
    """Katman 1 (regex/sozluk, kaynak_motor='dictionary') bir eslesme bir
    kod identifier'inin SADECE bir parcasiysa, identifier'in TAMAMI
    maskelenmeli - camelCase/PascalCase/snake_case/UPPER_SNAKE_CASE fark
    etmeksizin, ve method cagrisi/qualified-name (nokta/parantez komsulugu)
    baglaminda bile. Presidio/LLM ayni senaryoda REDDEDILMEYE devam eder
    (bkz. TestTokenBoundaryValidatorBareCode) - bu sadece Katman 1 icin."""

    def test_snake_case_partial_match_expands_to_full_identifier(self):
        text = "sube_adi = get_value()"
        match_start = text.index("sube")
        match_end = match_start + len("sube")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        full_start, full_end = 0, len("sube_adi")
        assert (accepted[0].start, accepted[0].end) == (full_start, full_end)
        assert accepted[0].deger == "sube_adi"

    def test_camel_case_partial_match_expands_to_full_identifier(self):
        text = "subeAdi = getValue()"
        match_start = text.index("Adi")
        match_end = match_start + len("Adi")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert accepted[0].deger == "subeAdi"

    def test_pascal_case_partial_match_expands_to_full_identifier(self):
        text = "class HedefSubeAdi:\n    pass\n"
        match_start = text.index("Sube")
        match_end = match_start + len("Sube")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert accepted[0].deger == "HedefSubeAdi"

    def test_upper_snake_case_partial_match_expands_to_full_identifier(self):
        text = "SUBE_ADI = 1"
        match_start = text.index("ADI")
        match_end = match_start + len("ADI")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert accepted[0].deger == "SUBE_ADI"

    def test_method_call_identifier_masked_whole_call_preserved(self):
        # Presidio/LLM ayni durumda (test_method_call_rejected_requests_get
        # gibi) REDDEDILIR; Katman 1 (dictionary) icin ise "()" korunarak
        # TAM method adi maskelenir.
        text = "sonuc = getSubeAdi()"
        match_start = text.index("Sube")
        match_end = match_start + len("Sube")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        full_start = text.index("getSubeAdi")
        full_end = full_start + len("getSubeAdi")
        assert (accepted[0].start, accepted[0].end) == (full_start, full_end)
        assert accepted[0].deger == "getSubeAdi"
        masked = text[:full_start] + "PLACEHOLDER" + text[full_end:]
        assert masked == "sonuc = PLACEHOLDER()"

    def test_decorator_identifier_masked_marker_and_call_preserved(self):
        text = '@GetSubeAdi("/x")\ndef handler():\n    pass\n'
        match_start = text.index("Sube")
        match_end = match_start + len("Sube")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        full_start = text.index("GetSubeAdi")
        full_end = full_start + len("GetSubeAdi")
        assert (accepted[0].start, accepted[0].end) == (full_start, full_end)
        assert text[full_start - 1] == "@"

    def test_sql_qualified_column_identifier_masked_schema_preserved(self):
        # `schema.SUBE_KODU` - nokta ONCESI qualified-name baglami; Katman 1
        # icin sadece "SUBE_KODU" bileseni TAM olarak maskelenir, "schema."
        # ve cevresindeki sozdizimi dokunulmadan kalir.
        text = "SELECT schema.SUBE_KODU FROM tablo"
        match_start = text.index("KODU")
        match_end = match_start + len("KODU")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        full_start = text.index("SUBE_KODU")
        full_end = full_start + len("SUBE_KODU")
        assert (accepted[0].start, accepted[0].end) == (full_start, full_end)
        assert accepted[0].deger == "SUBE_KODU"
        assert "schema." in text  # sarmalayici degismedi

    def test_sql_identifier_list_both_masked_independently(self):
        text = "SELECT T_SUBE, SUBE_KODU FROM tablo"
        first_start = text.index("T_SUBE")
        first_end = first_start + len("T_SUBE")
        second_start = text.index("KODU")
        second_end = second_start + len("KODU")
        results = [
            _result(first_start, first_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM"),
            _result(second_start, second_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM"),
        ]

        accepted, rejections = TokenBoundaryValidator().validate(text, results)

        assert rejections == []
        values = {a.deger for a in accepted}
        assert values == {"T_SUBE", "SUBE_KODU"}

    def test_comment_partial_match_expands_to_word_only(self):
        # Yorumlar kod DEGILDIR: bare-kod kontrolu (.get() komsulugu) ret
        # sebebi olmaz. Ama "mask_x_1_adi" geri cozulemedigi icin yapisik
        # kelime parcasi terimle birlikte maskelenir; nokta/parantez korunur.
        text = "# sube_adi.get() hakkinda not\nreal_code = 1\n"
        match_start = text.index("sube")
        result = _result(match_start, match_start + len("sube"), "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(
            text, [result], file_path="notes.py"
        )

        assert rejections == []
        assert accepted[0].deger == "sube_adi"

    def test_block_comment_partial_match_expands_to_word_only(self):
        text = "/* sube_adi.get() hakkinda not */\nint real_code = 1;\n"
        match_start = text.index("sube")
        result = _result(match_start, match_start + len("sube"), "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(
            text, [result], file_path="Notes.java"
        )

        assert rejections == []
        assert accepted[0].deger == "sube_adi"

    @pytest.mark.parametrize(("comment", "expected"), [
        ("// VEGA calisir", "VEGA"),
        ("// VEGAdan gelen", "VEGA"),  # kucuk harfli Turkce ek geri cozulebilir
        ("// VEGAService calisir", "VEGAService"),
        ("// VEGA2 calisir", "VEGA2"),
        ("// VEGAOMEGA sistemi", "VEGAOMEGA"),
        ("// eskiVEGA sistemi", "eskiVEGA"),
        ("// VEGAdanService", "VEGAdanService"),
        ("// VEGA-OMEGA", "VEGA"),
    ])
    def test_comment_match_glued_to_word_masks_whole_word(self, comment, expected):
        text = f"class X {{\n  {comment}\n}}\n"
        match_start = text.index("VEGA")
        result = _result(match_start, match_start + len("VEGA"), "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result], file_path="X.java")

        assert rejections == []
        assert accepted[0].deger == expected

    def test_code_outside_comment_on_same_file_still_expands(self):
        # Yorum-farkindaligi SADECE yorumun kendi araligini etkilemeli - ayni
        # dosyadaki gercek kod satirinda normal identifier genisletmesi
        # (ve method-cagrisi yetki istisnasi) calismaya devam etmeli.
        text = "# sube_adi hakkinda not\nresult = getSubeAdi()\n"
        match_start = text.index("Sube", text.index("getSubeAdi"))
        match_end = match_start + len("Sube")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="KURUMSAL_TERIM")

        accepted, rejections = TokenBoundaryValidator().validate(
            text, [result], file_path="notes.py"
        )

        assert rejections == []
        assert accepted[0].deger == "getSubeAdi"


class TestTokenBoundaryValidatorUnbalancedBrackets:
    """Gercek export'ta gozlemlenen bug: bir ust-katman (regex/Presidio/LLM),
    tirnaksiz bir span'i bir kod ifadesinin ORTASINDAN kesip (acilis "("
    span'in ICINE, kapanis ")" span'in DISINA kalacak sekilde) yakalarsa,
    `_is_bare_code_expression` (sadece span'in HEMEN DISINDAKI karakterlere
    bakar) bunu YAKALAYAMIYORDU - span tek bir placeholder'a donusup
    kapanmamis bir parantez birakiyor, sozdizimini bozuyordu. Bu savunma
    katmani (`_has_unbalanced_brackets`) span'in ICINDE dengesiz parantez/
    suslu/kose parantez olup olmadigina bakar."""

    def test_span_swallowing_open_paren_without_its_close_is_rejected(self):
        # Gercek export'ta gozlemlenen tam senaryo (bkz. generic_secret_
        # assignment regex duzeltmesi): "secret_key = conf.get(section,"
        # acilis "(" span'in icinde, kapanisi ")" disarida kalir.
        text = 'secret_key = conf.get(section, key, fallback=sentinel)\n'
        match_start = 0
        match_end = text.index("section,") + len("section,")
        result = _result(match_start, match_end, "yuksek", "dictionary", tip="secret")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1
        assert "dengesiz parantez" in rejections[0].reason

    def test_balanced_parenthetical_remark_is_not_rejected(self):
        # Gercek, cok-kelimeli bir bulgu (orn. bir LLM'in ORGANIZATION
        # tespiti) parantez icerebilir ama DENGELIDIR - bu YANLISLIKLA
        # reddedilmemeli.
        text = "Vendor: Standard Chartered (Hong Kong) Ltd - onaylandi\n"
        match_start = text.index("Standard")
        match_end = text.index(" - onaylandi")
        result = _result(match_start, match_end, "orta", "llm", tip="ORGANIZATION")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1


class TestTokenBoundaryValidatorStringLiterals:
    def test_quotes_included_in_match_are_stripped(self):
        # bug_report: "SLACK_WEBHOOK = mask_url_14" - iki tirnak da
        # kaybolmustu, cunku Presidio'nun "Quoted URL" deseni tirnaklari
        # da esleserdi. Duzeltme: tirnaklar HARIC tutulmali.
        text = "SLACK_WEBHOOK = 'https://hooks.slack.com/services/T00/B00/XXX'"
        quote_start = text.index("'")
        quote_end = text.rindex("'")
        result = _result(quote_start, quote_end + 1, "orta", "katman2_presidio", tip="URL")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        assert accepted[0].start == quote_start + 1
        assert accepted[0].end == quote_end
        assert text[accepted[0].start] != "'"
        assert text[accepted[0].end - 1] != "'"

    def test_match_extending_far_past_closing_quote_rejected(self):
        # Eslesme, string sinirinin COK OTESINE (max_expansion'in de
        # otesine) tasiyorsa - artik bu string ile ilgili oldugu guvenilir
        # degildir - REDDEDILMELI.
        text = "SLACK_OAUTH_ACCESS_TOKEN = 'xoxb-FAKE-TEST-abcdefghij'" + (" " * 250) + "trailing\n"
        inner_start = text.index("xoxb")
        trailing_end = text.index("trailing") + len("trailing")
        result = _result(inner_start + 5, trailing_end, "orta", "katman2_presidio", tip="DATE_TIME")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert accepted == []
        assert len(rejections) == 1

    def test_keyword_prefix_before_quote_is_normalized_not_lost(self):
        # GERCEKTE GOZLEMLENEN Presidio davranisi: `dbname="prod` - span
        # anahtar kelimeden (tirnaktan ONCE) baslayip kapanis tirnagini
        # DISARIDA birakiyor. Bu span REDDEDILMEMELI (aksi halde
        # `psycopg2.connect(host="...", , user="...")` gibi kismen
        # bozulmus/eksik-anahtar-kelimeli syntax olusurdu) - "dbname=" oneki
        # ve HER IKI tirnak da metinde OLDUGU GIBI kalmali, SADECE "prod"
        # degistirilmeli.
        text = 'conn = psycopg2.connect(host="x", dbname="prod", user="admin")'
        match_start = text.index("dbname")
        match_end = text.index('"prod') + len('"prod')  # kapanis tirnagini KAPSAMIYOR
        result = _result(match_start, match_end, "yuksek", "katman2_presidio", tip="LOCATION")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        content_start = text.index("prod")
        content_end = content_start + len("prod")
        assert (accepted[0].start, accepted[0].end) == (content_start, content_end)
        assert accepted[0].deger == "prod"
        # "dbname=" oneki ve tirnaklar ORIJINAL metinde (degistirilmeyen
        # kisimda) hala mevcut - bunlar sadece placeholder'in DISINDA kalir.
        assert text[match_start:content_start] == 'dbname="'
        assert text[content_end] == '"'

    def test_partial_value_inside_quotes_expands_to_full_content(self):
        text = "API_KEY = 'AbCdEfGhIjKlMnOp123456'"
        content_start = text.index("AbCdEf")
        content_end = text.rindex("'")
        # Eslesme, tirnak ici degerin sadece bir KISMI (orta parcasi).
        result = _result(content_start + 3, content_start + 10, "orta", "llm", tip="GENERIC_SECRET")

        accepted, rejections = TokenBoundaryValidator().validate(text, [result])

        assert rejections == []
        assert len(accepted) == 1
        assert accepted[0].start == content_start
        assert accepted[0].end == content_end
        assert accepted[0].deger == text[content_start:content_end]


# --------------------------------------------------------------------------
# Entegrasyon: gercek mask_text() akisiyla ast.parse() dogrulamasi
# --------------------------------------------------------------------------

SAMPLE_PYTHON_SOURCE = '''\
import requests
import psycopg2
import redis
from flask import Flask

app = Flask(__name__)

SLACK_OAUTH_ACCESS_TOKEN = 'xoxb-FAKE-TEST-TOKEN-NOT-REAL-VALUE'
SLACK_WEBHOOK = 'https://hooks.slack.com/services/T00000/B00000/XXXXXXXXXXXXXXXXXXXXXXXX'


@app.route('/req_1')
def handler():
    headers = {}
    results = requests.get("http://internal.example.com/api", headers=headers)
    conn = psycopg2.connect(host="db.internal", dbname="prod", user="admin", password="S3cr3tPassw0rdVal!123")
    redis_db = redis.StrictRedis(host="localhost", port=6379, db=0)
    return results, conn, redis_db


if __name__ == "__main__":
    app.run(debug=True)
'''

_TEST_IDENTITY = ("pytest-overlap-boundary-integration", "P-TEST-0001", "pytest-branch")


def _cleanup_test_identity() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.execute(
            __import__("sqlalchemy").text(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": _TEST_IDENTITY[0], "pn": _TEST_IDENTITY[1], "b": _TEST_IDENTITY[2]},
        ).first()
        if row is None:
            return
        context_id = row[0]
        sql = __import__("sqlalchemy").text
        run_ids = [
            r[0]
            for r in db.execute(
                sql("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sql("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sql("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sql("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sql("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sql("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sql("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


@pytest.fixture(autouse=True)
def _clean_test_identity_before_and_after():
    _cleanup_test_identity()
    yield
    _cleanup_test_identity()


def test_masked_python_output_is_valid_syntax_and_uncorrupted():
    from app.db.session import SessionLocal
    from app.services.mapping_service import MaskingRunContext, get_or_create_context, mask_text

    with SessionLocal() as db:
        context = get_or_create_context(db, *_TEST_IDENTITY)
        run_ctx = MaskingRunContext(
            context=context,
            runtime_params={
                "project_name": _TEST_IDENTITY[0],
                "sicil_no": _TEST_IDENTITY[1],
                "branch_name": _TEST_IDENTITY[2],
            },
        )
        masked_text, _mappings = mask_text(db, run_ctx, SAMPLE_PYTHON_SOURCE)
        db.commit()

    # 1) ANA DOGRULAMA: cikti gecerli Python syntax'i olmali.
    ast.parse(masked_text)

    # 2) bug_report'taki spesifik bozulma seklinin (placeholder + artik
    #    karakterler, orn. "mask_url_20ute") ARTIK olusmadigini dogrula.
    assert not re.search(r"mask_[a-z0-9_]+_\d+[a-z]", masked_text)

    # 3) Bare kod ifadeleri (attribute erisimi/fonksiyon cagrisi)
    #    HIC dokunulmadan kalmali - TokenBoundaryValidator bunlari reddetti.
    for intact in ("@app.route(", "requests.get(", "psycopg2.connect(", "redis.StrictRedis(", "app.run("):
        assert intact in masked_text, f"beklenmedik sekilde bozulmus/kaybolmus: {intact!r}"

    # 4) Eger SLACK_WEBHOOK maskelendiyse, tirnaklar SAGLAM kalmali (ne
    #    kaybolmus ne de placeholder'in disina tasmis olmali).
    webhook_line = next(line for line in masked_text.splitlines() if line.startswith("SLACK_WEBHOOK"))
    assert webhook_line.count("'") == 2
    assert re.match(r"^SLACK_WEBHOOK = '[^']*'$", webhook_line)


def test_comment_terms_glued_to_words_export_and_restore(db_session, monkeypatch, tmp_path):
    # Regresyon: yorumda yalnizca terim maskeleniyordu ("mask_x_1OMEGA",
    # "mask_x_1Service", "mask_x_12") - round-trip dosyayi engelliyordu.
    from app.services import exporter
    from app.services.term_upload import build_filter_rule
    from app.services.unmasker import unmask_project

    for term in ("VEGA", "OMEGA"):
        db_session.add(build_filter_rule(term=term, category="pytest_comment", status="ok", priority=1))
    db_session.flush()
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    identity = dict(project_name="comment-glue", sicil_no="P-COMMENT", branch_name="main", initiated_by="test")
    source, target, restored = tmp_path / "source", tmp_path / "masked", tmp_path / "restored"
    source.mkdir()
    original = (
        "class X {\n"
        "  // VEGAOMEGA sistemi, OMEGAVEGA ve VEGAService\n"
        "  /* VEGA2 ile eskiVEGA, VEGAdan gelen */\n"
        "  int a = 1;\n"
        "}\n"
    )
    (source / "X.java").write_text(original, encoding="utf-8")

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target), **identity,
    ))

    assert report.files_validation_failed == 0, report.summary_text()
    masked = (target / "X.java").read_text(encoding="utf-8")
    assert "VEGA" not in masked.upper() and "OMEGA" not in masked.upper()
    result = unmask_project(db_session, source_path=str(target), target_path=str(restored), **identity)
    assert result.status == "completed", result.summary_text()
    assert (restored / "X.java").read_text(encoding="utf-8") == original
