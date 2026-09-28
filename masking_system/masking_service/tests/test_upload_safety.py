import io
import zipfile

import pytest

from app.webapp import uploads


class Upload:
    def __init__(self, name, content):
        self.name, self.content = name, content

    def getvalue(self):
        return self.content


@pytest.fixture
def staging(tmp_path, monkeypatch):
    root = tmp_path / 'staging'
    root.mkdir()
    monkeypatch.setattr(uploads.tempfile, 'mkdtemp', lambda **kwargs: str(root))
    return root


@pytest.mark.parametrize('names,directory', [
    (['a/config.txt', 'b/config.txt'], False),
    (['a\\config.txt', 'b/config.txt'], False),
    (['a/config.txt', 'a/config.txt'], True),
    (['a/config.txt', 'a/./config.txt'], True),
    (['a/config.txt', 'a\\config.txt'], True),
])
def test_duplicate_upload_rejected_and_staging_removed(staging, names, directory):
    with pytest.raises(ValueError, match='aynı hedefe'):
        uploads.save_uploaded_files_to_temp_dir(
            [Upload(name, value) for name, value in zip(names, [b'first', b'second'])],
            is_directory_upload=directory,
        )
    assert not staging.exists()


def test_exclusive_write_preserves_first_content(tmp_path):
    path = tmp_path / 'same.txt'
    path.write_bytes(b'first')
    with pytest.raises(ValueError, match='aynı hedefe'):
        uploads._open_new_file(path)
    assert path.read_bytes() == b'first'


@pytest.mark.parametrize('directory', [False, True])
def test_distinct_uploads_preserve_bytes(staging, directory):
    names = ['a/config.txt', 'b/config.txt'] if directory else ['a.txt', 'b.txt']
    result = uploads.save_uploaded_files_to_temp_dir(
        [Upload(name, value) for name, value in zip(names, [b'first', b'second'])],
        is_directory_upload=directory,
    )
    assert [(result / name).read_bytes() for name in names] == [b'first', b'second']


@pytest.mark.parametrize('second', ['a/./config.txt', 'a\\config.txt'])
def test_zip_alias_duplicate_rejected(staging, second):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('a/config.txt', b'first')
        archive.writestr(second, b'second')
    with pytest.raises(ValueError, match='aynı hedefe'):
        uploads.save_uploaded_files_to_temp_dir([Upload('input.zip', buffer.getvalue())])
    assert not staging.exists()


def test_zip_preserves_files_and_explicit_directory(staging):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('a/config.txt', b'first')
        archive.writestr('a/', b'')
        archive.writestr('b/config.txt', b'second')
    result = uploads.save_uploaded_files_to_temp_dir([Upload('input.zip', buffer.getvalue())])
    assert (result / 'a/config.txt').read_bytes() == b'first'
    assert (result / 'b/config.txt').read_bytes() == b'second'


def test_cleanup_missing_directory_is_success(tmp_path):
    uploads.cleanup_temp_dir(tmp_path / 'missing', None)


def test_cleanup_failure_does_not_skip_other_directory(tmp_path, monkeypatch, caplog):
    locked, other = tmp_path / 'locked', tmp_path / 'other'
    locked.mkdir()
    other.mkdir()
    original = uploads.shutil.rmtree

    def remove(path):
        if path == locked:
            raise PermissionError('simulated lock')
        original(path)

    monkeypatch.setattr(uploads.shutil, 'rmtree', remove)
    with pytest.raises(OSError, match='hassas veri diskte'):
        uploads.cleanup_temp_dir(locked, other)
    assert locked.exists()
    assert not other.exists()
    assert str(locked) in caplog.text


def test_upload_failure_also_reports_cleanup_failure(staging, monkeypatch):
    def locked(path):
        raise PermissionError('simulated lock')
    monkeypatch.setattr(uploads.shutil, 'rmtree', locked)
    with pytest.raises(OSError, match='hassas veri diskte'):
        uploads.save_uploaded_files_to_temp_dir([Upload('same', b'1'), Upload('same', b'2')])
    assert (staging / 'same').read_bytes() == b'1'


def test_missing_child_error_is_not_silenced(tmp_path, monkeypatch):
    def missing_child(path):
        raise FileNotFoundError('child disappeared')
    monkeypatch.setattr(uploads.shutil, 'rmtree', missing_child)
    with pytest.raises(OSError, match='tamamen silinemedi'):
        uploads.cleanup_temp_dir(tmp_path)
