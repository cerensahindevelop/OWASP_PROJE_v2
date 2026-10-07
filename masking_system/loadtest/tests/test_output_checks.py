import hashlib
import io
import json
import zipfile

import pytest

from loadtest.output_checks import inspect_output, inspect_synthetic_sql, MANIFEST_NAME


def package(*, complete=True, job_id=7, contents=b"masked", digest=None, manifest=True):
    stream = io.BytesIO()
    payload = {"version": 2, "job_id": job_id, "complete": complete,
               "files": {"file.txt": {"masked_sha256": digest or hashlib.sha256(contents).hexdigest()}}}
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("file.txt", contents)
        if manifest:
            archive.writestr(MANIFEST_NAME, json.dumps({"payload": payload}))
    return stream.getvalue()


def test_complete_and_partial_outputs_are_distinct():
    report = {"run_id": 7, "files_ready": 1, "files_scanned": 1}
    assert inspect_output(package(), report)["output_quality"] == "complete"
    report["files_scanned"] = 2
    assert inspect_output(package(complete=False), report)["output_quality"] == "partial"


@pytest.mark.parametrize("data", [b"not a zip", package(manifest=False), package(job_id=8),
                                  package(digest="bad"), package(complete=False)],
                         ids=["not-zip", "missing-manifest", "wrong-job", "wrong-digest", "wrong-completeness"])
def test_corrupt_missing_foreign_or_inconsistent_artifacts_fail(data):
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        inspect_output(data, {"run_id": 7, "files_ready": 1, "files_scanned": 1})


def test_report_count_must_match_download():
    with pytest.raises(ValueError, match="file count"):
        inspect_output(package(), {"run_id": 7, "files_ready": 2, "files_scanned": 2})


def test_synthetic_sql_oracle_rejects_unmasked_values():
    source = [("db/seed.sql", b"-- ornek veri yukleme\nCREATE TABLE musteri (id INT);\n"
               b"INSERT INTO musteri VALUES (1, 'Test Person', '12345678901', 'test@example.test', '+90 555 123 45 67', 'TR123456');\n")]
    assert inspect_synthetic_sql(package(), source) == {"sql_sensitive_values_checked": 5}
    with pytest.raises(ValueError, match="sensitive value remains"):
        inspect_synthetic_sql(package(contents=b'contact test@example.test'), source)
