"""Kurumsal terimin desenin goremedigi yazimlari: kaba son kontrol, otomatik
duzeltme ve uctan uca disa aktarma/geri donus."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import AuditWarning
from app.services.exporter import export_project
from app.services.term_search import structural_parts
from app.services.term_upload import commit_term_upload, find_leaked_terms
from app.services.unmasker import unmask_project

_TERMS = "KuzeyPusulasi\nSelin Karaoglu\nQRX\n"
_IDENTITY = dict(project_name="pytest-ayrik", sicil_no="P-AYRIK", branch_name="test")


@pytest.fixture()
def terms(db_session):
    commit_term_upload(db_session, filename="t.txt", content=_TERMS.encode(), category="pytest_ayrik_yazim")


@pytest.mark.parametrize("value, term, expected", [
    ("Selin,Karaoglu", "Selin Karaoglu", ["Selin", "Karaoglu"]),
    ('Kuzey" + "Pusulasi', "KuzeyPusulasi", ["Kuzey", "Pusulasi"]),
    ("Selin ; Karaoglu", "Selin Karaoglu", ["Selin", "Karaoglu"]),
    ("Kuzey.Pusulasi", "KuzeyPusulasi", ["Kuzey", "Pusulasi"]),
    ("Kuzey\n# Pusulasi", "KuzeyPusulasi", ["Kuzey", "Pusulasi"]),
    # Bolunmez: yatay bosluk, kelime ortasindan bolme, kisa parca, cozulmemis kod.
    ("K u z e y P u s u l a s i", "KuzeyPusulasi", ["K u z e y P u s u l a s i"]),
    ("Kuzey Pusula(si", "KuzeyPusulasi", ["Kuzey Pusula(si"]),
    ("Ali,Canpolat", "Ali Canpolat", ["Ali,Canpolat"]),
    ("&#75;uzey.Pusulasi", "KuzeyPusulasi", ["&#75;uzey.Pusulasi"]),
])
def test_structural_parts(value, term, expected):
    assert structural_parts(value, term) == expected


@pytest.mark.parametrize("text, value", [
    ("proje Kuzey.Pusulasi hazir", "Kuzey.Pusulasi"),
    ("proje Kuzey\nPusulasi hazir", "Kuzey\nPusulasi"),
    ("proje K u z e y P u s u l a s i hazir", "K u z e y P u s u l a s i"),
    ("ad,soyad\nSelin,Karaoglu\n", "Selin,Karaoglu"),
    ("proje &#75;uzey.Pusulasi", "&#75;uzey.Pusulasi"),
])
def test_separated_check_finds_spellings_the_pattern_misses(db_session, terms, text, value):
    assert find_leaked_terms(db_session, text) == []
    leaks = find_leaked_terms(db_session, text, include_separated=True)
    assert [leak.matched_value for leak in leaks] == [value]


@pytest.mark.parametrize("text", [
    "Q.R.X ve q r x",                        # kisa terim kaba kontrole girmez
    "Kuzey mask_kurumsal_ifade_1 Pusulasi",  # yer tutucunun iki yani birlestirilmez
    "Kuzeyde pusula yok",
])
def test_separated_check_stays_quiet(db_session, terms, text):
    assert find_leaked_terms(db_session, text, include_separated=True) == []


def test_separated_check_is_off_for_paths(db_session, terms):
    path = "Kuzey.Pusulasi/rapor.txt"
    assert find_leaked_terms(db_session, path, exclude_path_spanning=True, include_separated=True) == []


def test_strict_leak_is_not_reported_twice(db_session, terms):
    leaks = find_leaked_terms(db_session, "KuzeyPusulasi", include_separated=True)
    assert [leak.matched_value for leak in leaks] == ["KuzeyPusulasi"]


def test_export_auto_remediates_separated_spellings_and_restores(db_session, terms, tmp_path):
    files = {
        "a.txt": "proje Kuzey.Pusulasi hazir\n",
        "b.txt": "proje Kuzey\nPusulasi hazir\n",
        "c.csv": "ad,soyad\nSelin,Karaoglu\n",
        "d.py": 'x = "Kuzey" + "Pusulasi"\n',
        "e.py": "# eski Kuzey.Pusulasi kaydi\nx = 1\n",
        "f.txt": "Q.R.X degismez\n",
        "g.py": "# eski Kuzey\nPusulasi = 1\n",
    }
    source, masked, restored = (tmp_path / name for name in ("src", "masked", "restored"))
    source.mkdir()
    for name, content in files.items():
        (source / name).write_text(content, encoding="utf-8")

    report = asyncio.run(export_project(
        db_session, source_path=str(source), target_path=str(masked), initiated_by="P-AYRIK", **_IDENTITY,
    ))

    assert report.files_quarantined_pending_audit == 0, report.summary_text()
    assert db_session.scalars(select(AuditWarning).where(AuditWarning.run_id == report.run_id)).all() == []
    for name in files:
        text = (masked / name).read_text(encoding="utf-8")
        assert find_leaked_terms(db_session, text, include_separated=True) == [], name
    assert (masked / "c.csv").read_text(encoding="utf-8").splitlines()[1].count(",") == 1
    assert (masked / "f.txt").read_text(encoding="utf-8") == files["f.txt"]
    # Satira bolunmus terim parca parca maskelenir: alt satirdaki kod yorumun
    # icine kaymaz.
    comment, code = (masked / "g.py").read_text(encoding="utf-8").splitlines()
    assert comment.startswith("# eski mask_kurumsal_ifade_")
    assert code.startswith("mask_kurumsal_ifade_") and code.endswith(" = 1")

    result = unmask_project(
        db_session, source_path=str(masked), target_path=str(restored), initiated_by="P-AYRIK", **_IDENTITY,
    )
    assert not result.has_unresolved_placeholders
    for name, content in files.items():
        assert (restored / name).read_text(encoding="utf-8") == content, name


def test_final_check_ignores_mixed_case_glued_slice(db_session, tmp_path):
    commit_term_upload(db_session, filename="t.txt", content=b"ebys\n", category="pytest_karisik_dilim")
    text = "policy.routeBySeverity = true; ETTIEBYS ve ettiebys\n"
    assert [leak.matched_value for leak in find_leaked_terms(db_session, text)] == ["EBYS", "ebys"]
