"""Farkli dosya bicimlerinde gomulu ikili verinin LLM'i yavaslatmasina karsi
sistemsel koruma:

- Blok tespiti dosya turunden bagimsiz, icerige gore calisir (data URI,
  .ipynb, string birlestirme, bayt dizileri, base64url, PEM).
- Okunabilir metne cozulen icerik ve metin benzeri veri asla gizlenmez.
- Kilit/uretilmis dosyalar LLM'e gitmez.
- Taninmayan yeni bir bicim ya da asiri parca sayisi islem kaydinda uyari uretir.
- `llm-is-yuku` export'tan once dosya dosya is yukunu gosterir.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.services import llm_recognizer
from app.services.encoded_blobs import count_unrecognized_encoded_lines, find_encoded_blobs
from app.services.file_classifier import is_lock_filename, should_ignore_path
from app.services.llm_detector import LLMDetector
from app.services.llm_workload import estimate_project

_RNG = random.Random(99)
IMAGE = b"\x89PNG\r\n\x1a\n" + bytes(_RNG.getrandbits(8) for _ in range(3000))
B64 = base64.b64encode(IMAGE).decode()


def _lines(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width)


BINARY_FORMATS = {
    "html-data-uri": f'<img alt="logo" src="data:image/png;base64,{B64}" /><p>Hakan</p>',
    "css-font-data-uri": f"@font-face{{src:url(data:font/woff2;base64,{B64}) format('woff2')}}",
    "ipynb-single-string": json.dumps({"outputs": [{"data": {"image/png": B64}}]}, indent=1),
    "ipynb-line-list": json.dumps({"image/png": [line + "\n" for line in _lines(B64, 76)]}, indent=1),
    "resx-value-same-line": "<value>" + "\n".join(_lines(B64, 80)) + "</value>",
    "csharp-concatenation": "const string Logo =\n" + "\n".join(f'    "{line}" +' for line in _lines(B64, 64))
                            + '\n    "";\nvar owner = "Hakan";',
    "java-byte-array": "byte[] logo = {" + ", ".join(str(b if b < 128 else b - 256) for b in IMAGE[:800]) + "};",
    "c-xxd-array": "unsigned char logo[] = {\n" + ",\n".join(
        "  " + ", ".join(f"0x{b:02x}" for b in IMAGE[i:i + 12]) for i in range(0, 900, 12)) + "\n};",
    "python-escaped-bytes": "LOGO = b'" + "".join(f"\\x{b:02x}" for b in IMAGE[:600]) + "'",
    "base64url": base64.urlsafe_b64encode(IMAGE).decode(),
    "pem-certificate": "-----BEGIN CERTIFICATE-----\n" + "\n".join(_lines(B64[:1300], 64))
                       + "\n-----END CERTIFICATE-----",
}

TEXT_LIKE = {
    "csv-numeric": "\n".join(",".join(str(_RNG.randint(0, 255)) for _ in range(80)) for _ in range(20)),
    "obfuscated-password-bytes": "int[] k = {" + ", ".join(
        str(b) for b in b"password=Sup3rS3cret;user=hakan.yilmaz;host=db.acme.local;" * 3) + "};",
    "base64-encoded-config": base64.b64encode(b"Server=db.acme.local;User=sa;Password=Sup3r!\n" * 20).decode(),
    "minified-js": "!function(e){var t={};function n(r){if(t[r])return t[r].exports;}" * 60,
    "long-identifiers": "\n".join(f"    AcmeCorpInternalBillingServiceProjectModule{i:03d}," for i in range(40)),
}


def _remaining(text: str) -> str:
    kept, cursor = [], 0
    for start, end in find_encoded_blobs(text, 512):
        kept.append(text[cursor:start])
        cursor = end
    kept.append(text[cursor:])
    return "".join(kept)


@pytest.mark.parametrize("name", BINARY_FORMATS)
def test_embedded_binary_is_hidden_in_every_known_format(name):
    text = BINARY_FORMATS[name]
    remaining = _remaining(text)
    # Dolgusuz kisa son satir (en fazla bir satir) bilerek gorunur kalabilir.
    assert len(remaining) < max(120, 0.05 * len(text)), remaining[:200]
    if "Hakan" in text:
        assert "Hakan" in remaining


@pytest.mark.parametrize("name", TEXT_LIKE)
def test_text_like_content_is_never_hidden(name):
    assert find_encoded_blobs(TEXT_LIKE[name], 512) == []


def test_unrecognized_encoded_lines_are_counted_for_early_warning():
    assert count_unrecognized_encoded_lines(TEXT_LIKE["base64-encoded-config"]) == 1
    assert count_unrecognized_encoded_lines(TEXT_LIKE["minified-js"]) == 0
    assert count_unrecognized_encoded_lines(TEXT_LIKE["long-identifiers"]) == 0


@pytest.mark.parametrize("name", [
    "packages.lock.json", "project.assets.json", "npm-shrinkwrap.json", "go.sum", "Package.resolved",
    "gradle.lockfile", "bun.lock",
])
def test_generated_dependency_manifests_are_scan_only(name):
    assert is_lock_filename(name) is True
    assert should_ignore_path(Path(name))[0] is False


@pytest.mark.parametrize("path", ["obj/Debug/project.assets.json", ".vs/solution/v17/.suo", "bower_components/x.js"])
def test_build_and_ide_caches_are_ignored(path):
    assert should_ignore_path(Path(path))[0] is True


def _settings(**overrides):
    values = dict(enabled=True, host="http://llm-guard.test", model="test", api_key=None, timeout_seconds=1,
                  max_file_chars=6000, chunk_overlap_chars=500, max_tokens=512, max_concurrent_requests=4,
                  seed=42, warn_chunks_per_file=3)
    values.update(overrides)
    return SimpleNamespace(**values)


def _empty_llm(monkeypatch):
    async def fake(host, timeout, payload, api_key=None):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"bulgular": []})}}]}

    monkeypatch.setattr(llm_recognizer, "call_vllm", fake)


def test_heavy_file_produces_content_free_workload_notice(monkeypatch):
    _empty_llm(monkeypatch)
    text = "var owner = 'Hakan';\n" * 1200  # ~25 KB -> 5 parca
    out = asyncio.run(LLMDetector(_settings()).detect(text, {"file_path": "big.cs"}))
    [notice] = [n for n in out.notices if n.startswith("llm_is_yuku_yuksek")]
    assert "parca=5" in notice and "Hakan" not in notice


def test_unrecognized_encoding_produces_notice_even_for_small_file(monkeypatch):
    _empty_llm(monkeypatch)
    out = asyncio.run(LLMDetector(_settings()).detect(TEXT_LIKE["base64-encoded-config"], {"file_path": "a.json"}))
    assert any("taninmayan_kodlanmis_satir=1" in notice for notice in out.notices)


def test_normal_file_produces_no_notice(monkeypatch):
    _empty_llm(monkeypatch)
    out = asyncio.run(LLMDetector(_settings()).detect("owner = 'Hakan'\n", {"file_path": "a.py"}))
    assert out.notices == []


def _sample_project(root: Path) -> None:
    (root / "Forms").mkdir(parents=True)
    (root / "obj").mkdir()
    (root / "Forms" / "Main.resx").write_text(
        "<root><value>\n" + "\n".join(_lines(B64 * 20, 80)) + "\n</value><value>Faturalar</value></root>",
        encoding="utf-8",
    )
    (root / "Forms" / "Main.cs").write_text("class Main { string owner = \"Hakan\"; }\n" * 300, encoding="utf-8")
    (root / "obj" / "project.assets.json").write_text('{"x": 1}\n' * 5000, encoding="utf-8")
    (root / "packages.lock.json").write_text('{"dependencies": {}}\n' * 500, encoding="utf-8")


def test_estimate_project_ranks_files_and_skips_generated_ones(tmp_path):
    _sample_project(tmp_path)
    workloads = estimate_project(
        tmp_path, [], _settings(), blob_min_chars=512, max_inline_size=50 * 1024 * 1024,
        legacy_encodings=("cp1254",),
    )
    paths = [workload.path for workload in workloads]
    assert paths == ["Forms/Main.cs", "Forms/Main.resx"]
    resx = workloads[1]
    assert resx.requests == 2 and resx.stats.hidden_chars > 50_000
    assert "gomulu ikili veri gizlenecek" in resx.hints


def test_llm_is_yuku_cli_reports_totals(tmp_path):
    _sample_project(tmp_path)
    result = CliRunner().invoke(cli_app, ["llm-is-yuku", "--kaynak", str(tmp_path), "--istek-suresi", "10"])
    assert result.exit_code == 0, result.output
    assert "LLM'e gidecek metin dosyasi: 2" in result.output
    assert "Forms/Main.resx" in result.output and "obj/" not in result.output
    assert "Tahmini LLM suresi" in result.output
