"""Java class fixtures were compiled from fixtures/java/Sample.java with javac 21.
Tests never execute user inputs. Optional JVM verification runs our own fixture.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest
from sqlalchemy import select

from app.db.models import AuditWarning
from app.services import exporter
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.integrity_manifest import read_manifest
from app.services.java_classfile import (
    ClassFormatError, JAVA_CLASS_ENCODING, decode_mutf8, encode_mutf8, parse_class,
)
from app.services.unmasker import unmask_project

FIXTURE = Path(__file__).parent / 'fixtures/java/Sample.class.b64'
IDENTITY = dict(project_name='pytest-java-class-support', sicil_no='JAVA-TEST', branch_name='test', initiated_by='test')


@pytest.fixture
def class_bytes():
    return base64.decodebytes(FIXTURE.read_bytes())


@pytest.mark.parametrize('value', ['', 'abc', 'Türkçe\0😀', '\u007f\u0080\u07ff\u0800\uffff'])
def test_java_modified_utf8_is_lossless(value):
    assert decode_mutf8(encode_mutf8(value)) == value


@pytest.mark.parametrize('raw', [b'\0', b'\xc1\x80', b'\xc0\x81', b'\xf0\x90\x80\x80', b'\xe0\x80\x80', b'\xed\xa0\x80', b'\xc2'])
def test_invalid_modified_utf8_is_rejected(raw):
    with pytest.raises(ClassFormatError):
        decode_mutf8(raw)


def test_class_rebuild_preserves_unmodified_bytes_and_masks_constants(class_bytes):
    document = parse_class(class_bytes)
    assert document.rebuild(document.text) == class_bytes
    masked_text = document.text.replace('alice@example.com', 'mask_email_123').replace('office@example.com', 'mask_email_456')
    masked = document.rebuild(masked_text)
    assert b'alice@example.com' not in masked and b'office@example.com' not in masked
    decoded = parse_class(masked)
    assert 'Türkçe' in decoded.text and '😀' in decoded.text
    restored = decoded.rebuild(decoded.text.replace('mask_email_123','alice@example.com').replace('mask_email_456','office@example.com'))
    assert restored == class_bytes


@pytest.mark.parametrize('mutation', ['magic', 'truncated', 'extra', 'unknown_tag', 'unknown_attribute'])
def test_malformed_or_opaque_classes_fail_closed(class_bytes, mutation):
    raw = class_bytes
    if mutation == 'magic': raw = b'XXXX' + raw[4:]
    elif mutation == 'truncated': raw = raw[:-1]
    elif mutation == 'extra': raw += b'private hidden data'
    elif mutation == 'unknown_tag': raw = raw[:10] + b'\xff' + raw[11:]
    else: raw = raw.replace(b'SourceFile', b'OpaqueData')
    with pytest.raises(ClassFormatError): parse_class(raw)


def test_structural_names_and_oversized_replacements_are_blocked(class_bytes):
    document = parse_class(class_bytes)
    with pytest.raises(ClassFormatError, match='yapısal'):
        document.rebuild(document.text.replace('"Sample"', '"mask_project_1"'))
    with pytest.raises(ClassFormatError, match='65535'):
        document.rebuild(document.text.replace('alice@example.com', 'x'*65536))
    with pytest.raises(ClassFormatError): document.rebuild('[]')


@pytest.fixture
def run_class_export(db_session, tmp_path, monkeypatch, class_bytes):
    def run(values=('alice@example.com', 'office@example.com', '10.24.36.48'), extra_files=None, skip_class=False):
        class Detector:
            async def scan(self, text, metadata=None):
                if skip_class and metadata['file_path'].endswith('.json'):
                    return DetectorOutput()
                hits = []
                for value in values:
                    start = text.find(value)
                    if start >= 0:
                        hits.append(DetectionResult(
                            deger=value, tip='JAVA_TEXT', guven_seviyesi='yuksek', kaynak_motor='dictionary',
                            start=start, end=start+len(value), rule=synthetic_llm_rule('JAVA_TEXT'),
                        ))
                return DetectorOutput(results=hits)
        monkeypatch.setattr(exporter, 'build_orchestrator', lambda *a, **kw: Detector())
        monkeypatch.setattr(exporter.settings.vllm, 'enabled', False)
        source, target = tmp_path/'source', tmp_path/'masked'
        source.mkdir()
        (source/'Sample.class').write_bytes(class_bytes)
        for name, content in (extra_files or {}).items(): (source/name).write_bytes(content)
        report = asyncio.run(exporter.export_project(db_session, source_path=str(source), target_path=str(target), **IDENTITY))
        return report, source, target
    return run


def test_real_pipeline_masks_class_and_restores_exact_bytes(run_class_export, db_session, tmp_path, class_bytes):
    report, source, target = run_class_export()
    assert report.files_masked == 1, report.summary_text()
    assert report.files_validation_failed == report.files_skipped_unsupported == 0
    assert report.validation_warnings  # coverage limits are visible, not hidden
    masked = (target/'Sample.class').read_bytes()
    for value in (b'alice@example.com', b'office@example.com', b'10.24.36.48'):
        assert value not in masked
    assert masked.startswith(b'\xca\xfe\xba\xbe')
    manifest = read_manifest(target, report.context_id)
    assert manifest['files']['Sample.class']['encoding'] == JAVA_CLASS_ENCODING
    assert manifest['files']['Sample.class']['source_bytes_tag']
    restored = tmp_path/'restored'
    result = unmask_project(db_session, source_path=str(target), target_path=str(restored), **IDENTITY)
    assert result.status == 'completed', result.summary_text()
    assert (restored/'Sample.class').read_bytes() == class_bytes
    assert (source/'Sample.class').read_bytes() == class_bytes


def test_real_seed_rules_detect_email_ip_and_named_password(db_session, tmp_path, monkeypatch, class_bytes):
    from app.services.detectors import DetectorRegistry, DetectionOrchestrator, RuleBasedDetector
    def build(rules, params, *args, **kwargs):
        registry = DetectorRegistry()
        registry.register(RuleBasedDetector(rules, params))
        return DetectionOrchestrator(registry)
    monkeypatch.setattr(exporter, 'build_orchestrator', build)
    monkeypatch.setattr(exporter.settings.vllm, 'enabled', False)
    source, target = tmp_path/'source', tmp_path/'masked'
    source.mkdir()
    (source/'Sample.class').write_bytes(class_bytes)
    report = asyncio.run(exporter.export_project(db_session, source_path=str(source), target_path=str(target), **IDENTITY))
    assert report.files_masked == 1, report.summary_text()
    masked = (target/'Sample.class').read_bytes()
    for value in (b'alice@example.com', b'10.24.36.48', b'localSecretValue123'):
        assert value not in masked
    result = unmask_project(db_session, source_path=str(target), target_path=str(tmp_path/'restored'), **IDENTITY)
    assert result.status == 'completed', result.summary_text()
    assert (tmp_path/'restored/Sample.class').read_bytes() == class_bytes


def test_structural_sensitive_match_is_not_published(run_class_export):
    report, _, target = run_class_export(values=('Sample',))
    assert report.files_validation_failed == 1
    assert not (target/'Sample.class').exists()
    assert 'yapısal' in report.outcomes[0].error


def test_bad_class_does_not_stop_other_files(run_class_export):
    report, _, target = run_class_export(extra_files={'Bad.class':b'not-a-class','other.bin':bytes(range(256))})
    assert report.files_masked == 1
    assert report.files_errored == 1
    assert report.files_skipped_unsupported == 1
    assert not (target/'Bad.class').exists()
    assert not (target/'other.bin').exists()


def test_consistency_pass_masks_a_class_missed_by_primary_detector(run_class_export, db_session, tmp_path, class_bytes):
    report, _, target = run_class_export(values=('alice@example.com',),
                                        extra_files={'notes.txt': b'alice@example.com'}, skip_class=True)
    assert report.files_masked == 2, report.summary_text()
    assert b'alice@example.com' not in (target/'Sample.class').read_bytes()
    restored = tmp_path/'restored'
    result = unmask_project(db_session, source_path=str(target), target_path=str(restored), **IDENTITY)
    assert result.status == 'completed', result.summary_text()
    assert (restored/'Sample.class').read_bytes() == class_bytes


def test_consistency_structural_change_is_blocked_without_crashing_run(run_class_export):
    report, _, target = run_class_export(values=('Sample',), extra_files={'notes.txt': b'Sample'}, skip_class=True)
    assert report.files_failed_consistency_validation == 1, report.summary_text()
    assert not (target/'Sample.class').exists()
    assert (target/'notes.txt').exists()


def test_nontext_byte_corruption_is_blocked_before_export(run_class_export, monkeypatch):
    write = exporter.write_text_preserving_encoding
    def corrupt(path, text, encoding, **kwargs):
        result = write(path, text, encoding, **kwargs)
        if encoding == JAVA_CLASS_ENCODING:
            path.write_bytes(path.read_bytes().replace(struct.pack('>q',1234567890123456789),
                                                       struct.pack('>q',1234567890123456788)))
        return result
    monkeypatch.setattr(exporter,'write_text_preserving_encoding',corrupt)
    report, _, target = run_class_export()
    assert report.files_failed_finalization == 1, report.summary_text()
    assert not (target/'Sample.class').exists()


def test_class_quarantine_cannot_publish_json_as_bytecode(db_session):
    from app.services.audit_warning_service import AuditWarningService
    from app.db.models import MaskingRun
    warning = AuditWarning(file_path='Sample.class', encoding=JAVA_CLASS_ENCODING, masked_content='[]')
    service = AuditWarningService(db_session)
    assert 'yeniden' in asyncio.run(service._final_pass(warning, MaskingRun()))
    with pytest.raises(ValueError, match='yeniden'):
        service._release_to_target(warning)


def test_binary_tampering_cannot_pass_text_only_restore_proof(run_class_export, db_session, tmp_path):
    report, _, target = run_class_export()
    path = target/'Sample.class'
    raw = path.read_bytes()
    old = struct.pack('>q',1234567890123456789)
    assert old in raw
    # Numeric pool bytes are not in the text view. Signed byte proof must
    # still detect this change even though textual reconstruction is exact.
    path.write_bytes(raw.replace(old, struct.pack('>q',1234567890123456788)))
    restored = unmask_project(db_session, source_path=str(target), target_path=str(tmp_path/'restored'), **IDENTITY)
    assert restored.status == 'completed_with_warnings'
    assert any('Java class baytları' in w for w in restored.validation_warnings)


def test_jvm_verifies_and_executes_only_our_masked_fixture(class_bytes, tmp_path):
    java = shutil.which('java')
    if not java: pytest.skip('JVM unavailable; production adapter does not require Java')
    doc = parse_class(class_bytes)
    (tmp_path/'Sample.class').write_bytes(doc.rebuild(doc.text.replace('alice@example.com','mask_email_123')))
    result = subprocess.run([java,'-Xverify:all','-cp',str(tmp_path),'Sample'],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'mask_email_123'


@pytest.mark.parametrize('content', [b'not-a-class', b'\xca\xfe\xba\xbe'])
def test_fake_class_extension_never_becomes_plain_text(content, tmp_path):
    from app.services.file_pipeline import read_scanned_file, ReadStatus
    from app.services.scanner import ScannedFile
    path = tmp_path/'fake.class'; path.write_bytes(content)
    out = tmp_path/'out.class'
    result = read_scanned_file(ScannedFile(path,Path('fake.class'),False),out,1000,copy_unscannable=False)
    assert result.status == ReadStatus.ERROR
    assert not out.exists()
