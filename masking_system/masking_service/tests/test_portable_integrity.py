"""End-to-end preservation uses bytes and real mappings, without running source."""
import asyncio
import json
import os

import pytest

from app.services import exporter
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.integrity_manifest import MANIFEST_NAME
from app.services.unmasker import unmask_project

VALUE = "OrionPrivateValue"
IDENTITY = dict(project_name="portable-integrity", sicil_no="P-PORTABLE", branch_name="main", initiated_by="test")
SAMPLES = {
    "py": 'value = "OrionPrivateValue"\r\n',
    "js": 'const value = "OrionPrivateValue";\n',
    "ts": 'const value: string = "OrionPrivateValue";\n',
    "tsx": 'const value = <div title="OrionPrivateValue" />;\n',
    "java": 'class App { String value = "OrionPrivateValue"; }\n',
    "c": 'const char *value = "OrionPrivateValue";\n',
    "cpp": 'const char *value = "OrionPrivateValue";\n',
    "cs": 'class App { string value = "OrionPrivateValue"; }\n',
    "go": 'package main\nvar value = "OrionPrivateValue"\n',
    "rs": 'const VALUE: &str = "OrionPrivateValue";\n',
    "kt": 'val value = "OrionPrivateValue"\n',
    "swift": 'let value = "OrionPrivateValue"\n',
    "rb": 'value = "OrionPrivateValue"\n',
    "php": '<?php $value = "OrionPrivateValue"; ?>\n',
    "sh": 'value="OrionPrivateValue"\n',
    "ps1": '$value = "OrionPrivateValue"\n',
    "lua": 'local value = "OrionPrivateValue"\n',
    "sql": "SELECT 'OrionPrivateValue';\n",
    "json": '{"value": "OrionPrivateValue"}\n',
    "xml": '<value>OrionPrivateValue</value>\n',
    "yaml": 'value: "OrionPrivateValue"\n',
    "toml": 'value = "OrionPrivateValue"\n',
    "html": '<div title="OrionPrivateValue"></div>\n',
    "css": '.value { content: "OrionPrivateValue"; }\n',
    "md": 'Value: OrionPrivateValue\n',
    "hs": 'value = "OrionPrivateValue"\n',
    "clj": '(def value "OrionPrivateValue")\n',
    "ex": 'value = "OrionPrivateValue"\n',
    "unknown": 'value = "OrionPrivateValue"\n',
    "": 'value="OrionPrivateValue"\n',
}


@pytest.fixture()
def export_sample(db_session, tmp_path, monkeypatch):
    class Detector:
        async def scan(self, text, metadata=None):
            start = text.find(VALUE)
            results = [] if start < 0 else [DetectionResult(
                deger=VALUE, tip="SECRET", guven_seviyesi="yuksek", kaynak_motor="dictionary",
                start=start, end=start+len(VALUE), rule=synthetic_llm_rule("SECRET"))]
            return DetectorOutput(results=results)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **kw: Detector())
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source = tmp_path / "source"
    target = tmp_path / "masked"
    source.mkdir()
    def export(filename="file.py", raw=None):
        original = raw if raw is not None else SAMPLES["py"].encode()
        (source / filename).write_bytes(original)
        (source / filename).chmod(0o750)
        report = asyncio.run(exporter.export_project(db_session, source_path=str(source),
                               target_path=str(target), **IDENTITY))
        assert (target / filename).is_file(), report.summary_text()
        return source, target, original
    return export


@pytest.mark.parametrize("suffix", list(SAMPLES))
def test_roundtrip_across_language_and_format_boundaries(db_session, tmp_path, export_sample, suffix):
    filename = "sample" + ("." + suffix if suffix else "")
    _, masked, original = export_sample(filename, SAMPLES[suffix].encode())
    assert VALUE.encode() not in (masked / filename).read_bytes()
    restored = tmp_path / "restored"
    report = unmask_project(db_session, source_path=str(masked), target_path=str(restored), **IDENTITY)
    assert report.status == "completed", report.summary_text()
    assert (restored / filename).read_bytes() == original
    if os.name == "posix":
        assert (restored / filename).stat().st_mode & 0o777 == 0o750
    assert not (restored / MANIFEST_NAME).exists()


@pytest.mark.parametrize("encoding,bom", [
    ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
    ("utf-32-le", b"\xff\xfe\x00\x00"), ("utf-32-be", b"\x00\x00\xfe\xff"),
    ("cp1254", b""),
])
def test_real_export_unmask_preserves_encoding_and_bom(db_session, tmp_path, export_sample, encoding, bom):
    original = bom + ('value = "OrionPrivateValue"\r\n# şğİıöüç\n').encode(encoding)
    _, masked, _ = export_sample(raw=original)
    restored = tmp_path / "restored"
    report = unmask_project(db_session, source_path=str(masked), target_path=str(restored), **IDENTITY)
    assert report.status == "completed", report.summary_text()
    assert (restored / "file.py").read_bytes() == original


def test_arbitrary_token_edit_is_reported_by_source_integrity(db_session, tmp_path, export_sample):
    _, masked, _ = export_sample()
    (masked / "file.py").write_text('value = "not-a-placeholder-anymore"\n')
    report = unmask_project(db_session, source_path=str(masked), target_path=str(tmp_path / "restored"), **IDENTITY)
    assert report.status == "completed_with_warnings"
    assert any("butunluk kaydiyla uyusmuyor" in warning for warning in report.validation_warnings)


def test_deleted_file_is_reported(db_session, tmp_path, export_sample):
    _, masked, _ = export_sample()
    (masked / "file.py").unlink()
    report = unmask_project(db_session, source_path=str(masked), target_path=str(tmp_path / "restored"), **IDENTITY)
    assert report.status == "completed_with_warnings"
    assert any("kaynakta eksik" in warning for warning in report.validation_warnings)


def test_tampered_manifest_does_not_touch_target(db_session, tmp_path, export_sample):
    _, masked, _ = export_sample()
    path = masked / MANIFEST_NAME
    data = json.loads(path.read_text())
    data["payload"]["files"]["file.py"]["encoding"] = "ascii"
    path.write_text(json.dumps(data))
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "old").write_text("old")
    with pytest.raises(ValueError, match="butunluk kaydi gecersiz"):
        unmask_project(db_session, source_path=str(masked), target_path=str(restored), **IDENTITY)
    assert (restored / "old").read_text() == "old"


def test_oversized_input_is_warned_even_with_a_valid_manifest(db_session, tmp_path, export_sample):
    _, masked, _ = export_sample()
    report = unmask_project(db_session, source_path=str(masked), target_path=str(tmp_path / "restored"),
                           max_inline_size=1, **IDENTITY)
    assert report.status == "completed_with_warnings"
    assert report.files_skipped_too_large == 1
    assert report.total_placeholders_resolved == 0


def test_damaged_marker_is_reported_without_manifest():
    from app.services.rule_engine import reverse_text
    for token in ("MASK_SECRET_1", "mask_secret_1_suffix", "mask_secret_1Suffix"):
        assert reverse_text(token, {"mask_secret_1": "original"}) == (token, 0, [token])


@pytest.mark.parametrize("damage", ["missing_mapping", "changed_file", "missing_manifest"])
def test_unresolved_tokens_still_warn_without_exact_authenticated_source(
    db_session, tmp_path, export_sample, damage,
):
    from sqlalchemy import delete
    from app.db.models import ValueMapping

    _, masked, _ = export_sample(raw=(
        'label = "service_test_1"\nvalue = "OrionPrivateValue"\n'
    ).encode())
    if damage == "missing_mapping":
        # This fixture runs in a disposable DB; remove mappings to simulate
        # genuinely lost restore data while keeping a valid signed manifest.
        db_session.execute(delete(ValueMapping))
        db_session.flush()
    elif damage == "changed_file":
        path = masked / "file.py"
        path.write_bytes(path.read_bytes() + b"# changed\n")
    else:
        (masked / MANIFEST_NAME).unlink()
        with pytest.raises(ValueError, match="islem kimligi eksik"):
            unmask_project(
                db_session, source_path=str(masked), target_path=str(tmp_path / "restored"), **IDENTITY,
            )
        assert not (tmp_path / "restored").exists()
        return

    report = unmask_project(
        db_session, source_path=str(masked), target_path=str(tmp_path / "restored"), **IDENTITY,
    )
    assert report.status == "completed_with_warnings"
    assert report.has_unresolved_placeholders


def test_wrong_encryption_key_is_explained_before_touching_output(db_session, tmp_path, export_sample, monkeypatch):
    from cryptography.fernet import Fernet
    from app.core import crypto
    _, masked, _ = export_sample()
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "old").write_text("old")
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    with pytest.raises(ValueError, match="sifreleme anahtari DB ile uyusmuyor"):
        unmask_project(db_session, source_path=str(masked), target_path=str(restored), **IDENTITY)
    assert (restored / "old").read_text() == "old"
