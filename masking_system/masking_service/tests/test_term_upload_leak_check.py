"""Kurumsal terim sozlugu ozelligi / Adim 8: maskeleme-sonrasi son kontrol
(find_leaked_terms) testleri - export'un ana tespit gecisinden BAGIMSIZ,
ikinci bir savunma katmani (bkz. term_upload.py modul dokstringi)."""

from __future__ import annotations

import pytest

from app.services.term_upload import commit_term_upload, find_leaked_terms


def test_placeholder_category_does_not_trigger_leak_but_open_occurrences_do(db_session):
    commit_term_upload(
        db_session, filename="terms.txt", content=b"Poseidon\n",
        category="pytest_poseidon",
    )
    assert find_leaked_terms(db_session, "mask_pytest_poseidon_1\n") == []
    leaked = find_leaked_terms(db_session, "mask_pytest_poseidon_1\n  Poseidon\nPoseidon\n")
    assert [(t.line_number, t.column_number) for t in leaked] == [(2, 3), (3, 1)]
    assert [t.matched_value for t in leaked] == ["Poseidon", "Poseidon"]


def test_malformed_placeholder_is_not_exempt_from_leak_check(db_session):
    commit_term_upload(
        db_session, filename="terms.txt", content=b"Poseidon\n",
        category="pytest_poseidon",
    )
    assert find_leaked_terms(db_session, "PYTEST_POSEIDON_TEST_broken")


def test_find_leaked_terms_detects_active_term_still_present(db_session):
    commit_term_upload(
        db_session,
        filename="terimler.txt",
        content=b"Poseidon\n",
        category="pytest_leak_kat1",
    )

    leaked = find_leaked_terms(db_session, "config = poseidonServer\n")

    assert len(leaked) == 1
    assert leaked[0].category == "pytest_leak_kat1"


def test_find_leaked_terms_empty_when_term_absent(db_session):
    commit_term_upload(
        db_session,
        filename="terimler.txt",
        content=b"Poseidon\n",
        category="pytest_leak_kat2",
    )

    leaked = find_leaked_terms(db_session, "config = mask_pytest_leak_kat2_1\n")

    assert leaked == []


def test_multi_segment_path_term_still_caught_in_content_by_default(db_session):
    """A corporate term that is itself a multi-segment file path (e.g. a
    hardcoded credential path) must still be caught by the default
    (exclude_path_spanning=False) scan wherever it appears as literal file
    CONTENT - that is exactly what such a term is for. Uses distinctive
    invented words (not generic ones) to avoid colliding with unrelated
    corporate terms already seeded in the shared dev database."""
    commit_term_upload(
        db_session, filename="terms.txt", content=b"/opt/zephyrqx/novacrit/omegalabs\n",
        category="pytest_path_term",
    )
    leaked = find_leaked_terms(db_session, "wallet_path = '/opt/zephyrqx/novacrit/omegalabs'\n")
    assert len(leaked) == 1
    assert leaked[0].matched_value == "/opt/zephyrqx/novacrit/omegalabs"


def test_multi_segment_path_term_excluded_from_path_spanning_check(db_session):
    """The SAME term must be excluded when exclude_path_spanning=True (the
    mode exporter.py's path-masking preflight uses): mask_relative_path()
    scans one path COMPONENT at a time and can structurally never mask a
    term whose literal text spans multiple '/'-separated segments - keeping
    it in the path check would fail every export whose folder layout
    happens to reproduce the term's text, with no way for the user to fix
    it (see exporter.py's find_leaked_terms(..., exclude_path_spanning=True)
    call site)."""
    commit_term_upload(
        db_session, filename="terms.txt", content=b"zephyrqx/novacrit/omegalabs\n",
        category="pytest_path_term2",
    )
    masked_path_text = "zephyrqx/novacrit/omegalabs/config.py"  # as_posix(), never actually masked
    assert find_leaked_terms(db_session, masked_path_text) != []  # sanity: default still finds it
    assert find_leaked_terms(db_session, masked_path_text, exclude_path_spanning=True) == []


def test_find_leaked_terms_ignores_inactive_suspicious_terms(db_session):
    """'data' gibi supheli bir terim pasif eklendigi icin bu son kontrolun
    de disinda kalmali - aksi halde hicbir zaman maskelenmesi beklenmeyen
    (kullanicinin elle onaylamadigi) bir terim yuzunden HER dosya
    karantinaya alinirdi."""
    commit_term_upload(
        db_session,
        filename="terimler.txt",
        content=b"data\n",
        category="pytest_leak_kat3",
    )

    leaked = find_leaked_terms(db_session, "data = load_data()\n")

    assert leaked == []


@pytest.mark.parametrize("path", [
    "axioserror/mask_kurumsal_ifade_31ServiceImpl.java",
    "axioserror/Önmask_kurumsal_ifade_31Çizim.java",
    "axioserror/premask_kurumsal_ifade_31_service.java",
    "axioserror/mask_kurumsal_ifade_31service.java",
    "axioserror/mask_kurumsal_ifade_31/mask_kurumsal_ifade_31Service.java",
])
def test_path_leak_check_recognizes_only_known_compound_tokens(db_session, path):
    from app.services.term_upload import build_filter_rule

    db_session.add(build_filter_rule(
        term="kurumsal", category="pytest_path_token", status="ok", priority=1,
    ))
    db_session.flush()
    # The content detector keeps its word boundaries. Only path validation
    # may exempt a known token embedded in a larger file/directory name.
    assert find_leaked_terms(db_session, path)
    assert find_leaked_terms(
        db_session, path, exclude_path_spanning=True,
        path_placeholders=["mask_kurumsal_ifade_31"],
    ) == []


@pytest.mark.parametrize("path,expected", [
    ("mask_kurumsal_ifade_31KurumsalService.java", "Kurumsal"),
    ("Kurumsal/mask_kurumsal_ifade_31Service.java", "Kurumsal"),
    ("mask_kurumsal_ifade_316Service.java", "kurumsal"),
    ("mask_kurumsal_ifade_brokenService.java", "kurumsal"),
    ("mask_kurumsal_ifade_99.java", "kurumsal"),
])
def test_path_leak_check_still_catches_open_terms_and_unknown_tokens(db_session, path, expected):
    from app.services.term_upload import build_filter_rule

    db_session.add(build_filter_rule(
        term="kurumsal", category="pytest_path_token", status="ok", priority=1,
    ))
    db_session.flush()
    leaked = find_leaked_terms(
        db_session, path, exclude_path_spanning=True,
        path_placeholders=["mask_kurumsal_ifade_31"],
    )
    assert [term.matched_value for term in leaked] == [expected]
