"""Maskeli projenin kendi basina cozulebilir kalmasi: dosya adlari, import'lar,
string icindeki SQL/yol/API referanslari ayni yer tutucuyu alir."""

import asyncio
import re
import shutil
import subprocess
import sys

from app.core.crypto import decrypt_value
from app.services.detectors import DetectionResult
from app.services.rule_engine import RuleSpec
from app.services.term_upload import build_filter_rule
from app.services.token_boundary_validator import TokenBoundaryValidator


def _rule(rule_name: str) -> RuleSpec:
    return RuleSpec(
        id=1, rule_name=rule_name, category="test", pattern_type="regex", regex_pattern=None,
        regex_flags=None, placeholder_prefix="mask_test", priority=1,
    )


def _found(text: str, value: str, rule_name: str = "kurumsal_terim_test_x") -> DetectionResult:
    start = text.index(value)
    return DetectionResult(
        deger=value, tip="test", guven_seviyesi="yuksek", kaynak_motor="dictionary",
        start=start, end=start + len(value), rule=_rule(rule_name),
    )


def _spans(text: str, results: list[DetectionResult]) -> list[str]:
    accepted, _rejections = TokenBoundaryValidator().validate(text, results)
    return sorted(text[r.start:r.end] for r in accepted)


def test_corporate_term_in_string_masks_only_its_token():
    text = 'db.execute("SELECT * FROM zephyrqx_tablo WHERE id=?")\n'
    assert _spans(text, [_found(text, "zephyrqx")]) == ["zephyrqx_tablo"]


def test_runtime_identity_in_string_still_masks_whole_literal():
    # Proje adi "test", parolanin yalnizca bir parcasi: geri kalani acikta kalmamali.
    text = 'DB_PASSWORD = "test-2024-super-secret"\n'
    assert _spans(text, [_found(text, "test", "proje_adi")]) == ["test-2024-super-secret"]


def test_corporate_term_widens_back_when_another_finding_takes_the_literal():
    text = 'url = "https://zephyrqx.example/?token=abc123"\n'
    secret = DetectionResult(
        deger="https://zephyrqx.example/?token=abc123", tip="secret", guven_seviyesi="yuksek",
        kaynak_motor="dictionary", start=text.index("https"), end=text.index('"\n'),
        rule=_rule("genel_secret"),
    )
    spans = _spans(text, [_found(text, "zephyrqx"), secret])
    assert spans == ["https://zephyrqx.example/?token=abc123"] * 2


def test_corporate_term_glued_to_escape_sequence_masks_whole_literal():
    text = 'print("baslik\\nZephyrqx bilgisi")\n'
    assert _spans(text, [_found(text, "Zephyrqx")]) == ["baslik\\nZephyrqx bilgisi"]


def test_compound_term_matches_spaced_underscored_and_hyphenated_forms():
    rule = build_filter_rule(term="ZephyrqxNovacrit", category="pytest_sep", status="ok", priority=1)
    pattern = re.compile(decrypt_value(rule.regex_pattern), re.IGNORECASE)
    for text in ("ZephyrqxNovacrit", "Zephyrqx Novacrit Komutanligi", "zephyrqx_novacrit_tablo", "zephyrqx-novacrit"):
        assert pattern.search(text), text
    assert not pattern.search("Zephyrqx.Novacrit")
    spaced = build_filter_rule(term="Zephyrqx Novacrit", category="pytest_sep", status="ok", priority=1)
    assert re.search(decrypt_value(spaced.regex_pattern), "ZephyrqxNovacritServisi", re.IGNORECASE)


def test_masked_python_project_still_imports_and_reads_its_files(db_session, monkeypatch, tmp_path):
    from app.services import exporter
    from app.services.unmasker import unmask_project

    db_session.add(build_filter_rule(term="Zephyrqx", category="pytest_consistency", status="ok", priority=1))
    db_session.flush()
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    identity = dict(project_name="code-path", sicil_no="P-CONSIST", branch_name="main", initiated_by="test")
    source, target, restored = tmp_path / "source", tmp_path / "masked", tmp_path / "restored"
    (source / "app").mkdir(parents=True)
    (source / "data").mkdir()
    files = {
        "app/__init__.py": "",
        "app/ZephyrqxBakimServisi.py": (
            "import json, pathlib, sqlite3\n"
            "KOK = pathlib.Path(__file__).resolve().parent.parent\n"
            "class ZephyrqxBakimServisi:\n"
            "    def oku(self):\n"
            "        return json.loads((KOK / 'data/ZephyrqxKatalog.json').read_text(encoding='utf-8'))\n"
            "    def say(self):\n"
            "        db = sqlite3.connect(':memory:')\n"
            "        db.execute('CREATE TABLE zephyrqx_kayit (id INTEGER)')\n"
            "        db.execute('INSERT INTO zephyrqx_kayit VALUES (1)')\n"
            "        return db.execute('SELECT COUNT(*) FROM zephyrqx_kayit').fetchone()[0]\n"
        ),
        "main.py": (
            "from app.ZephyrqxBakimServisi import ZephyrqxBakimServisi\n"
            "s = ZephyrqxBakimServisi()\n"
            "print(s.oku()['ad'], s.say())\n"
        ),
        "data/ZephyrqxKatalog.json": '{"ad": "katalog"}\n',
    }
    for name, content in files.items():
        (source / name).write_text(content, encoding="utf-8")

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target), **identity,
    ))

    assert report.files_validation_failed == 0, report.summary_text()
    masked_names = [p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()]
    assert not any("zephyrqx" in name.lower() for name in masked_names), masked_names
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json"):
            assert "zephyrqx" not in path.read_text(encoding="utf-8").lower(), path.name
    # Kopyada calistirilir: __pycache__ maskeli paketin butunluk kaydini bozmasin.
    runnable = shutil.copytree(target, tmp_path / "run")
    run = subprocess.run([sys.executable, "-B", "main.py"], cwd=runnable, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "katalog 1"

    result = unmask_project(db_session, source_path=str(target), target_path=str(restored), **identity)
    assert result.status == "completed", result.summary_text()
    for name, content in files.items():
        assert (restored / name).read_text(encoding="utf-8") == content
