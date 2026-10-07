"""Check downloaded artifact structure and bytes, separately from job completion.

These checks do not establish masking recall or authenticate the manifest signature.
"""
import hashlib
import io
import json
import re
from pathlib import PurePosixPath
import zipfile

MANIFEST_NAME = ".masking-integrity.json"


def inspect_synthetic_sql(data: bytes, source_files: list[tuple[str, bytes]]) -> dict:
    """Check known synthetic SQL fixture values; never log the values themselves.

    Call only for a structurally complete output, so withholding SQL cannot
    produce a vacuous pass. This is a fixture oracle, not general PII detection.
    """
    sensitive = set()
    for name, content in source_files:
        if not name.endswith('.sql') or not content.startswith(b'-- ornek veri yukleme\nCREATE TABLE musteri '):
            continue
        for line in content.splitlines():
            if line.startswith(b'INSERT INTO musteri VALUES '):
                values = re.findall(rb"'([^']*)'", line)
                if len(values) != 5:
                    raise ValueError("unexpected synthetic SQL fixture shape")
                sensitive.update(values)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith('/') or name == MANIFEST_NAME:
                continue
            content = archive.read(name)
            if any(value in content for value in sensitive):
                raise ValueError("synthetic SQL sensitive value remains in output")
    return {"sql_sensitive_values_checked": len(sensitive)}


def inspect_output(data: bytes, report: dict) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive entry")
        if MANIFEST_NAME not in names:
            raise ValueError("missing manifest")
        payload = json.loads(archive.read(MANIFEST_NAME))["payload"]
        if payload.get("version") != 2 or type(payload.get("job_id")) is not int:
            raise ValueError("invalid manifest identity")
        if payload["job_id"] != report["run_id"]:
            raise ValueError("wrong job output")
        if type(payload.get("complete")) is not bool or not isinstance(payload.get("files"), dict):
            raise ValueError("invalid manifest schema")
        files = payload["files"]
        if set(files) != set(names) - {MANIFEST_NAME}:
            raise ValueError("manifest file list mismatch")
        for name, entry in files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("unsafe archive path")
            if hashlib.sha256(archive.read(name)).hexdigest() != entry["masked_sha256"]:
                raise ValueError("output digest mismatch")
        if len(files) != report["files_ready"]:
            raise ValueError("report file count mismatch")
        if payload["complete"] != (len(files) == report["files_scanned"]):
            raise ValueError("inconsistent completeness flag")
        return {
            "zip_files": len(files), "manifest_job_id": payload["job_id"],
            "manifest_files": len(files), "manifest_complete": payload["complete"],
            "output_validated": True,
            "output_quality": "complete" if payload["complete"] else "partial",
        }
