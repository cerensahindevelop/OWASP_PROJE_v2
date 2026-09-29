"""Bagimlilik lock dosyalari (Asama 7a): LLM kapali, integrity/public registry
izin listesi, ic registry URL'lerinin maskelenmesi.

Sozluk (kurumsal terim / ogrenilmis hassas deger) bulgulari izin listesiyle
ASLA dusurulmez; host disinda kalan her bulgu dosyayi eskisi gibi onaya
gonderir. Maskelenen lock dosyasi parser, geri donus ve acik terim
kontrollerinden gecmeden yazilmaz.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.db.models import AuditWarning
from app.services import exporter
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.lockfile_policy import plan_lockfile, structured_format
from app.services.unmasker import unmask_project

PUBLIC = ("registry.npmjs.org", "pypi.org")

PACKAGE_LOCK = json.dumps({
    "name": "app", "lockfileVersion": 3,
    "packages": {
        "node_modules/left-pad": {
            "version": "1.3.0",
            "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
            "integrity": "sha512-XI5MPzVNApjAyhQzphX8BkmKsKUxD4LdyK24iZeQNBAa3XwJ5yUVVjRLsVjxv4hU5HRcsuvaSC8GGCj6N5WmAA==",
        },
        "node_modules/odeme-sdk": {
            "version": "2.0.0",
            "resolved": "https://npm.kurum.local:4873/odeme-sdk/-/odeme-sdk-2.0.0.tgz",
            "integrity": "sha512-AAAAPzVNApjAyhQzphX8BkmKsKUxD4LdyK24iZeQNBAa3XwJ5yUVVjRLsVjxv4hU5HRcsuvaSC8GGCj6N5WmAA==",
        },
    },
}, indent=2) + "\n"


# --- plan_lockfile birim testleri -------------------------------------------

def _span(text, value, occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(value, start + 1)
    return start, start + len(value)


def test_integrity_and_public_registry_findings_are_allowlisted():
    text = PACKAGE_LOCK
    integrity = _span(text, "sha512-XI5M")
    public_url = _span(text, "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz")
    plan = plan_lockfile(text, "package-lock.json", [(*integrity, False), (*public_url, False)], PUBLIC)
    assert plan.remaining == [] and plan.allowlisted == 2


def test_dictionary_findings_are_never_allowlisted():
    text = PACKAGE_LOCK
    in_public_path = _span(text, "left-pad", 1)
    plan = plan_lockfile(text, "package-lock.json", [(*in_public_path, True)], PUBLIC)
    assert len(plan.remaining) == 1 and plan.url_spans is None


INTERNAL_URL = "https://npm.kurum.local:4873/odeme-sdk/-/odeme-sdk-2.0.0.tgz"


@pytest.mark.parametrize("flagged", ["kurum", INTERNAL_URL])
def test_finding_inside_internal_url_plans_masking_of_every_internal_url(flagged):
    # Sinir dogrulamasi string icindeki bulguyu tum URL'ye genisletir; ikisi de ayni plana varir.
    text = PACKAGE_LOCK
    plan = plan_lockfile(text, "package-lock.json", [(*_span(text, flagged), True)], PUBLIC)
    assert [text[a:b] for a, b in plan.url_spans] == [INTERNAL_URL]


@pytest.mark.parametrize("url", [
    "https://registry.npmjs.org.attacker.io/x.tgz",   # alt alan adi / sonek
    "https://evil.registry.npmjs.org/x.tgz",
    "https://user:token@registry.npmjs.org/x.tgz",    # kimlik bilgisi
])
def test_public_host_match_is_exact_and_rejects_userinfo(url):
    text = json.dumps({"resolved": url}) + "\n"
    plan = plan_lockfile(text, "package-lock.json", [(*_span(text, url), False)], PUBLIC)
    assert plan.remaining


def test_credentials_in_internal_url_go_to_review():
    text = json.dumps({"resolved": "https://ci:s3cret@npm.kurum.local/a.tgz"}) + "\n"
    plan = plan_lockfile(text, "package-lock.json", [(*_span(text, "s3cret"), False)], PUBLIC)
    assert plan.url_spans is None


def test_finding_outside_internal_urls_is_not_remediable():
    text = json.dumps({"author": "Hakan Yilmaz", "resolved": "https://npm.kurum.local/a.tgz"}) + "\n"
    plan = plan_lockfile(text, "package-lock.json",
                         [(*_span(text, "Hakan Yilmaz"), False), (*_span(text, "kurum"), True)], PUBLIC)
    assert plan.url_spans is None


def test_keyed_hex_checksum_is_allowlisted_but_bare_hex_is_not():
    digest = "a" * 64
    keyed = f'checksum = "{digest}"\n'
    assert plan_lockfile(keyed, "Cargo.lock", [(*_span(keyed, digest), False)], PUBLIC).remaining == []
    bare = f'secret = "{digest}"\n'
    assert plan_lockfile(bare, "Cargo.lock", [(*_span(bare, digest), False)], PUBLIC).remaining


def test_structured_formats():
    assert structured_format("package-lock.json") == "json"
    assert structured_format("Pipfile.lock") == "json"
    assert structured_format("composer.lock") == "json"
    assert structured_format("pnpm-lock.yaml") == "yaml"
    assert structured_format("poetry.lock") == "toml"
    assert structured_format("Cargo.lock") == "toml"
    assert structured_format("yarn.lock") is None
    assert structured_format("Gemfile.lock") is None


# --- export duzeyi ------------------------------------------------------------

class _FlagValues:
    """Verilen degerleri (deger, tip, kaynak) dosyada isaretleyen sahte tespit."""

    def __init__(self, values):
        self.values = values
        self.metadata = []

    async def scan(self, text, metadata=None):
        self.metadata.append(dict(metadata or {}))
        results = []
        for value, tip, source in self.values:
            start = text.find(value)
            if start >= 0:
                results.append(DetectionResult(
                    deger=value, tip=tip, guven_seviyesi="yuksek", kaynak_motor=source,
                    start=start, end=start + len(value), rule=synthetic_llm_rule(tip),
                ))
        return DetectorOutput(results=results)


def _export(db_session, tmp_path, monkeypatch, files, flags, suffix):
    detector = _FlagValues(flags)
    monkeypatch.setattr(exporter, "build_orchestrator", lambda *a, **k: detector)
    source = tmp_path / f"{suffix}-src"
    for rel, content in files.items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_text(content, encoding="utf-8")
    target = tmp_path / f"{suffix}-out"
    identity = dict(project_name=f"pytest-{suffix}", sicil_no="P-LOCK", branch_name="main")
    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target), initiated_by="P-LOCK", **identity,
    ))
    return report, target, source, identity, detector


def _outcome(report, rel):
    return next(o for o in report.outcomes if o.relative_path == rel)


def test_llm_is_disabled_for_lock_files_only(db_session, tmp_path, monkeypatch):
    _report, _target, _source, _identity, detector = _export(
        db_session, tmp_path, monkeypatch, {"package-lock.json": PACKAGE_LOCK, "a.py": "x = 1\n"}, [], "lock-llm")
    by_file = {m["file_path"]: m for m in detector.metadata}
    assert by_file["package-lock.json"]["enable_llm"] is False
    assert by_file["a.py"]["enable_llm"] is True


def test_allowlisted_only_findings_copy_lock_file_byte_identical(db_session, tmp_path, monkeypatch):
    lock = PACKAGE_LOCK.replace("npm.kurum.local:4873", "registry.npmjs.org")
    report, target, *_ = _export(db_session, tmp_path, monkeypatch, {"package-lock.json": lock}, [
        ("sha512-XI5MPzVNApjAyhQzphX8BkmKsKUxD4LdyK24iZeQNBAa3XwJ5yUVVjRLsVjxv4hU5HRcsuvaSC8GGCj6N5WmAA==", "SECRET", "katman1_regex"),
        ("https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz", "URL", "katman2_presidio"),
    ], "lock-allow")
    assert (target / "package-lock.json").read_text(encoding="utf-8") == lock
    assert _outcome(report, "package-lock.json").status == "scan_only_clean"


def test_internal_registry_url_is_masked_and_restored(db_session, tmp_path, monkeypatch):
    report, target, source, identity, _ = _export(
        db_session, tmp_path, monkeypatch, {"package-lock.json": PACKAGE_LOCK},
        [("npm.kurum.local", "HOSTNAME", "katman2_presidio")], "lock-mask")

    output = (target / "package-lock.json").read_text(encoding="utf-8")
    assert "kurum" not in output and "odeme-sdk-2.0.0.tgz" not in output
    json.loads(output)  # dosya gecerli JSON kaldi
    assert "registry.npmjs.org/left-pad" in output and "sha512-XI5M" in output
    assert _outcome(report, "package-lock.json").final_state == "READY"
    assert db_session.query(AuditWarning).filter_by(run_id=report.run_id).count() == 0

    restored = tmp_path / "lock-restored"
    unmask_project(db_session, source_path=str(target), target_path=str(restored),
                   initiated_by="P-LOCK", **identity)
    assert (restored / "package-lock.json").read_text(encoding="utf-8") == PACKAGE_LOCK


@pytest.mark.parametrize("name, content, flags", [
    # Host disinda bir bulgu: eskisi gibi onaya.
    ("package-lock.json", PACKAGE_LOCK.replace('"name": "app"', '"name": "Hakan Yilmaz"'),
     [("Hakan Yilmaz", "PERSON", "katman2_presidio")]),
    # Public registry yolunda sozluk terimi: izin listesi sozlugu dusurmez.
    ("package-lock.json", PACKAGE_LOCK, [("left-pad-1.3.0", "KURUMSAL", "dictionary")]),
    # yarn.lock v1: dogrulayacak parser yok, ic host bulgusu yine onaya.
    ("yarn.lock", 'odeme-sdk@^2.0.0:\n  resolved "https://npm.kurum.local/odeme-sdk-2.0.0.tgz"\n',
     [("npm.kurum.local", "HOSTNAME", "katman2_presidio")]),
])
def test_non_remediable_lock_findings_still_go_to_review(db_session, tmp_path, monkeypatch, name, content, flags):
    report, target, *_ = _export(db_session, tmp_path, monkeypatch, {name: content}, flags, "lock-review")
    assert not (target / name).exists()
    assert _outcome(report, name).final_state == "SECURITY_QUARANTINE"


def test_masked_lock_that_fails_leak_check_goes_to_review(db_session, tmp_path, monkeypatch):
    # URL maskelense de dosyada acik kurumsal terim kalirsa yazilmaz.
    from app.services.term_upload import commit_term_upload

    commit_term_upload(db_session, filename="t.txt", content=b"OdemeSdkGizli\n", category="pytest_lock_term")
    lock = PACKAGE_LOCK.replace('"name": "app"', '"name": "OdemeSdkGizli"')
    report, target, *_ = _export(db_session, tmp_path, monkeypatch, {"package-lock.json": lock},
                                 [("npm.kurum.local", "HOSTNAME", "katman2_presidio")], "lock-leak")
    assert not (target / "package-lock.json").exists()
    warning = db_session.query(AuditWarning).filter_by(run_id=report.run_id).one()
    assert "açık terim" in warning.reasoning
