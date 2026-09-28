from __future__ import annotations

from app.services.rule_engine import RuleSpec, find_matches


def _parametric_rule(category: str, prefix: str = "TEST") -> RuleSpec:
    return RuleSpec(
        id=1,
        rule_name=category,
        category=category,
        pattern_type="parametric",
        regex_pattern=None,
        regex_flags=None,
        placeholder_prefix=prefix,
        priority=1,
        validator_name=None,
        description=None,
    )


def test_parametric_matching_does_not_match_inside_words_or_identifiers():
    rule = _parametric_rule("branch_name", "mask_branch")
    text = "branch = 'main'\nname = 'maintenance'\nmodule = main_handler\n"

    matches, _already = find_matches([rule], text, {"branch_name": "main"})

    assert [match.original_value for match in matches] == ["main"]
    assert text[matches[0].start - 1] == "'"
    assert text[matches[0].end] == "'"


def test_parametric_matching_catches_turkish_suffix_without_apostrophe():
    # Turkce'de "Poseidon'un" gibi kesme isaretli yazim yerine "Poseidonun"
    # gibi kesme isaretsiz yazim COK yaygin - proje adi metinde boyle
    # gectiginde de yakalanmali (kor testte kacirildigi gozlemlendi).
    rule = _parametric_rule("project_name", "mask_proje_adi")
    text = "Poseidonun modulu calisti. Poseidondan veri geldi. Poseidon'un ayri kaydi da var.\n"

    matches, _already = find_matches([rule], text, {"project_name": "Poseidon"})

    assert len(matches) == 3
    assert all(m.original_value == "Poseidon" for m in matches)


def test_parametric_matching_does_not_match_short_word_prefix_of_unrelated_word():
    # "main" + "tenance" = "maintenance" - "ten" gorunuse gore bir Turkce ek
    # (-ten) ile basliyor ama kelime orada bitmiyor; boyle bir tesaduf
    # eslesme kabul edilmemeli (bkz. _TR_SUFFIX_TRANSITION).
    rule = _parametric_rule("branch_name", "mask_branch")
    text = "branch = 'main'\nname = 'maintenance'\n"

    matches, _already = find_matches([rule], text, {"branch_name": "main"})

    assert [match.original_value for match in matches] == ["main"]


def test_sicil_number_rejects_technical_numeric_contexts():
    rule = _parametric_rule("sicil_no", "mask_personel_no")
    text = "PKCS12 p12 {12} v1.24.11 sha256 rsa2048 objects/ab/1234567890abcdef sicil=12"

    matches, _already = find_matches([rule], text, {"sicil_no": "12"})
    version_matches, _already = find_matches([rule], text, {"sicil_no": "24"})

    assert [match.start for match in matches] == [text.index("12", text.index("sicil="))]
    assert version_matches == []


def test_sicil_number_rejects_technical_token_values():
    rule = _parametric_rule("sicil_no", "mask_personel_no")
    text = "store = 'p12'\nhash_name = 'sha256'\nkey = 'rsa2048'\n"

    matches_p12, _ = find_matches([rule], text, {"sicil_no": "p12"})
    matches_sha, _ = find_matches([rule], text, {"sicil_no": "sha256"})
    matches_rsa, _ = find_matches([rule], text, {"sicil_no": "rsa2048"})

    assert matches_p12 == []
    assert matches_sha == []
    assert matches_rsa == []


def test_parametric_matching_catches_camelcase_compound_identifiers():
    """"\\b" iki harf arasinda ASLA sinir saymadigindan, project_name gibi
    parametrik degerler eskiden camelCase birlesik bir tanimlayicinin
    (orn. "projePoseidonKurulumu") icinde HIC eslesmezdi - sessiz bir
    false-negative. compound_aware_boundary_pattern bunu camelCase/
    kisaltma gecislerini de sinir sayarak cozer (bkz. rule_engine.py
    _CASE_TRANSITION) - ama alt cizgiyi (test_parametric_matching_does_not_
    match_inside_words_or_identifiers) ya da rakam bitisikligini (asagida)
    HALA bir sinir saymaz, cunku ikisi de parametrik kurallar icin ayri
    guvenlik gerekceleriyle BILEREK haric tutuldu."""
    rule = _parametric_rule("project_name", "mask_proje_adi")
    text = "config = projePoseidonKurulumu\nurl = 'APIPoseidonServer'\n"

    matches, _already = find_matches([rule], text, {"project_name": "Poseidon"})

    assert [match.original_value for match in matches] == ["Poseidon", "Poseidon"]


def test_parametric_matching_still_ignores_digit_adjacency():
    """Rakam<->harf gecisi BILEREK parametrik kurallara uygulanmadi (bkz.
    rule_engine.py _DIGIT_LETTER_TRANSITION dokstringi) - SAYISAL bir
    sicil_no degerinin "PKCS12"/"AES256" gibi rastgele teknik token'larin
    sonundaki rakamlarla cakismasini onlemek icin. Bu testin amaci: bu
    guvenlik ozelliginin gelecekte yanlislikla kaldirilmadigini
    dogrulamak (regresyon koruma)."""
    rule = _parametric_rule("sicil_no", "mask_personel_no")
    text = "cipher = AES256\n"

    matches, _already = find_matches([rule], text, {"sicil_no": "256"})

    assert matches == []


def test_rule_engine_returns_overlapping_corporate_and_runtime_candidates_for_central_resolution():
    """Katman 1, kisa runtime degerini erken tuketip tam kurumsal terimi
    kaybetmemeli; iki aday da merkezi OverlapResolver'a ulasmalidir."""
    runtime = _parametric_rule("project_name", "mask_proje_adi")
    corporate = RuleSpec(
        id=2,
        rule_name="kurumsal_terim_kurum_123",
        category="kurum_kimligi",
        pattern_type="regex",
        regex_pattern=r"ALFA\ IC\ DENETIM\ VE\ GUVENLIK\ BASKANLIGI",
        regex_flags="i",
        placeholder_prefix="mask_kurum",
        priority=281,
    )
    text = "ALFA IC DENETIM VE GUVENLIK BASKANLIGI"

    matches, _already = find_matches(
        [runtime, corporate],
        text,
        {"project_name": "ic denetim"},
    )

    assert [(match.rule.rule_name, match.original_value) for match in matches] == [
        ("kurumsal_terim_kurum_123", text),
        ("project_name", "IC DENETIM"),
    ]


def _regex_rule(pattern: str, prefix: str = "TOK") -> RuleSpec:
    return RuleSpec(
        id=2,
        rule_name="tok",
        category="tok",
        pattern_type="regex",
        regex_pattern=pattern,
        regex_flags=None,
        placeholder_prefix=prefix,
        priority=1,
        validator_name=None,
        description=None,
    )


class TestChunkedScanning:
    """find_matches/find_matches_compiled artik text_chunking.chunk_text ile
    parcalara bolerek tarar (Katman 2 Presidio ile ayni yardimci) - buyuk
    dosyalarda tek bir regex.finditer cagrisinin sinirsiz girdi almasini
    onlemek icin. Kucuk max_chunk_chars/overlap degerleriyle davranisi
    zorlayarak dogruluğu (eslesme kaybi/tekrari olmadigini) test eder."""

    def test_matches_scattered_across_many_chunks_are_all_found(self):
        rule = _regex_rule(r"ID-\d{4}")
        tokens = [f"ID-{1000 + i}" for i in range(20)]
        text = "\n".join(f"padding line {i} " + tokens[i] for i in range(20))

        matches, _already = find_matches([rule], text, max_chunk_chars=40, chunk_overlap_chars=10)

        assert sorted(m.original_value for m in matches) == sorted(tokens)
        for m in matches:
            assert text[m.start : m.end] == m.original_value

    def test_match_fully_inside_overlap_window_is_not_duplicated(self):
        rule = _regex_rule(r"ID-\d{4}")
        # max_chunk_chars=30 -> gercek overlap = min(chunk_overlap_chars, 30//4) = 7.
        # Token (7 karakter) TAM olarak ilk chunk'in son 7 karakterine ("x"*23
        # sonrasi) denk gelecek sekilde yerlestirildi, boylece hem chunk 0 hem
        # de bindirmeli chunk 1 tarafindan AYNI global konumda bulunur -
        # seen_spans dedup'inin gercekten calistigini (chunk'in "kismen gordu
        # de kacirdi" degil, "tam gordu ve tekillestirdi") dogrular.
        text = "x" * 23 + "ID-9999" + "y" * 40

        matches, _already = find_matches([rule], text, max_chunk_chars=30, chunk_overlap_chars=7)

        assert len(matches) == 1
        assert matches[0].original_value == "ID-9999"
        assert matches[0].start == 23 and matches[0].end == 30

    def test_chunked_and_unchunked_results_are_identical_for_small_text(self):
        rule = _regex_rule(r"ID-\d{4}")
        text = "prefix ID-1234 middle ID-5678 suffix"

        default_matches, _ = find_matches([rule], text)
        chunked_matches, _ = find_matches([rule], text, max_chunk_chars=20, chunk_overlap_chars=5)

        assert [m.original_value for m in default_matches] == [m.original_value for m in chunked_matches]
        assert [(m.start, m.end) for m in default_matches] == [(m.start, m.end) for m in chunked_matches]
