import asyncio
import json

import pytest

from app.db.models import MaskingRun
from app.services import exporter
from app.services.detectors import DetectionResult, DetectorOutput, synthetic_llm_rule
from app.services.integrity_manifest import MANIFEST_NAME
from app.services.mapping_service import get_or_create_context, get_or_create_mapping, MappingCache
from app.services.unmasker import load_context_mappings, unmask_project


def make_job(db, project='counter-project', person='Ceren'):
    context = get_or_create_context(db, project, person, 'main')
    job = MaskingRun(context_id=context.id, mapping_version=2, operation_type='mask',
                     source_path='source', target_path='target', initiated_by=person, status='completed')
    db.add(job)
    db.flush()
    return context, job


def test_each_job_and_type_starts_at_one_and_cache_is_job_scoped(db_session):
    cache = MappingCache().mappings
    jobs = [make_job(db_session), make_job(db_session), make_job(db_session, 'project-b', 'Can')]
    for context, job in jobs:
        for kind, first, second in [('IP', '10.10.10.1', '10.10.10.2'),
                                     ('EMAIL', 'a@kurum.com', 'b@kurum.com'),
                                     ('SECRET', 'first-secret', 'second-secret')]:
            rule = synthetic_llm_rule(kind)
            mapping, created = get_or_create_mapping(db_session, context.id, rule, first, cache, run_id=job.id)
            assert created and mapping.placeholder_value == f'mask_{kind.lower()}_1'
            repeated, created = get_or_create_mapping(db_session, context.id, rule, first, cache, run_id=job.id)
            assert repeated.id == mapping.id and not created
            mapping, created = get_or_create_mapping(db_session, context.id, rule, second, cache, run_id=job.id)
            assert created and mapping.placeholder_value == f'mask_{kind.lower()}_2'
            assert mapping.run_id == job.id
        numeric, _ = get_or_create_mapping(db_session, context.id, rule, '1234567', cache, run_id=job.id, numeric=True)
        assert numeric.placeholder_value == '811199000000001'
    assert len({job.id for _, job in jobs}) == 3
    assert len(cache) == 21


def test_job_must_belong_to_identity(db_session):
    first, job = make_job(db_session)
    second, _ = make_job(db_session, 'other')
    with pytest.raises(ValueError, match='kimligiyle eslesmiyor'):
        get_or_create_mapping(db_session, second.id, synthetic_llm_rule('IP'), '10.0.0.1', run_id=job.id)
    with pytest.raises(ValueError, match='proje/sicil/branch'):
        load_context_mappings(db_session, second.project_name, second.sicil_no, second.branch_name, job_id=job.id)


@pytest.fixture
def exports(db_session, tmp_path, monkeypatch):
    values = {}
    class Detector:
        async def scan(self, text, metadata=None):
            results = []
            for value, kind in values.items():
                start = text.find(value)
                if start >= 0:
                    results.append(DetectionResult(
                        deger=value, tip=kind, guven_seviyesi='yuksek', kaynak_motor='dictionary',
                        start=start, end=start+len(value), rule=synthetic_llm_rule(kind),
                    ))
            return DetectorOutput(results=results)
    monkeypatch.setattr(exporter, 'build_orchestrator', lambda *a, **kw: Detector())
    monkeypatch.setattr(exporter.settings.vllm, 'enabled', False)
    runs = []
    for index, (project, person, ip, email) in enumerate([
        ('job-project-a', 'Ceren', '10.10.10.1', 'a@kurum.com'),
        ('job-project-b', 'Can', '192.168.1.50', 'x@kurum.com'),
        ('job-project-a', 'Ceren', '172.16.1.20', 'next@kurum.com'),
    ]):
        values.clear()
        values.update({ip: 'IP', email: 'EMAIL'})
        source, target = tmp_path / f'source{index}', tmp_path / f'masked{index}'
        source.mkdir()
        original = f'{ip}\n{email}\n'
        (source / 'file.txt').write_text(original)
        identity = dict(project_name=project, sicil_no=person, branch_name='main', initiated_by=person)
        report = asyncio.run(exporter.export_project(db_session, source_path=str(source), target_path=str(target), **identity))
        assert (target / 'file.txt').read_text() == 'mask_ip_1\nmask_email_1\n', report.summary_text()
        payload = json.loads((target / MANIFEST_NAME).read_text())['payload']
        assert payload['version'] == 2 and payload['job_id'] == report.run_id
        runs.append((report, target, original, identity))
    return runs


def test_repeated_tokens_restore_using_package_job_including_repeat_export(db_session, tmp_path, exports):
    for index, (report, target, original, identity) in enumerate(exports):
        restored = tmp_path / f'restored{index}'
        result = unmask_project(db_session, source_path=str(target), target_path=str(restored), **identity)
        assert result.job_id == report.run_id
        assert result.total_placeholders_resolved == 2
        assert (restored / 'file.txt').read_text() == original


def test_wrong_job_and_tampered_job_never_touch_target(db_session, tmp_path, exports):
    first, target, _, identity = exports[0]
    another = exports[2][0]
    restored = tmp_path / 'restored'
    restored.mkdir()
    (restored / 'keep.txt').write_text('keep')
    with pytest.raises(ValueError, match='islem kimligiyle eslesmiyor'):
        unmask_project(db_session, source_path=str(target), target_path=str(restored), job_id=another.run_id, **identity)
    manifest = target / MANIFEST_NAME
    document = json.loads(manifest.read_text())
    document['payload']['job_id'] = another.run_id
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='butunluk kaydi gecersiz'):
        unmask_project(db_session, source_path=str(target), target_path=str(restored), **identity)
    assert list(restored.iterdir()) == [restored / 'keep.txt']
    assert (restored / 'keep.txt').read_text() == 'keep'


def test_missing_manifest_requires_explicit_job_and_restores_selected_job(db_session, tmp_path, exports):
    report, target, original, identity = exports[0]
    (target / MANIFEST_NAME).unlink()
    restored = tmp_path / 'restored'
    with pytest.raises(ValueError, match='islem kimligi eksik'):
        unmask_project(db_session, source_path=str(target), target_path=str(restored), **identity)
    assert not restored.exists()
    result = unmask_project(db_session, source_path=str(target), target_path=str(restored), job_id=report.run_id, **identity)
    assert result.total_placeholders_resolved == 2
    assert (restored / 'file.txt').read_text() == original


def test_legacy_manifest_remains_reversible_after_new_jobs(db_session, tmp_path):
    from app.services.integrity_manifest import write_manifest
    context = get_or_create_context(db_session, 'legacy-project', 'Ceren', 'main')
    old_job = MaskingRun(context_id=context.id, operation_type='mask', source_path='old',
                         initiated_by='Ceren', status='completed')
    db_session.add(old_job)
    db_session.flush()
    old_mapping, _ = get_or_create_mapping(db_session, context.id, synthetic_llm_rule('IP'), '10.1.1.1', run_id=old_job.id)
    assert old_mapping.run_id is None
    same_context, new_job = make_job(db_session, 'legacy-project')
    new_mapping, _ = get_or_create_mapping(db_session, context.id, synthetic_llm_rule('IP'), '192.168.0.1', run_id=new_job.id)
    assert new_mapping.placeholder_value == 'mask_ip_1'
    source = tmp_path / 'legacy'
    source.mkdir()
    (source / 'file.txt').write_text(old_mapping.placeholder_value)
    write_manifest(source, context.id, {}, complete=False)
    target = tmp_path / 'restored-legacy'
    unmask_project(db_session, source_path=str(source), target_path=str(target),
                   project_name='legacy-project', sicil_no='Ceren', branch_name='main', initiated_by='Ceren')
    assert (target / 'file.txt').read_text() == '10.1.1.1'
