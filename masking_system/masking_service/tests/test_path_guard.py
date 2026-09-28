"""app/webapp/path_guard.py icin regresyon testleri - web arayuzunun
'Klasor Yolu' modunda WEB_ALLOWED_ROOTS disina cikamadigini dogrular.
settings.web.allowed_roots dogrudan (monkeypatch ile, test sonunda geri
alinir) ayarlanarak farkli kurulum senaryolari (hic tanimlanmamis / tanimli)
simule edilir - .env dosyasindan bagimsiz, deterministik bir test icin.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.webapp.path_guard import PathNotAllowedError, allowed_roots_configured, ensure_path_allowed


def test_allowed_roots_configured_is_false_when_empty(monkeypatch):
    monkeypatch.setattr(settings.web, "allowed_roots", "")
    assert allowed_roots_configured() is False


def test_allowed_roots_configured_is_true_when_set(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.web, "allowed_roots", str(tmp_path))
    assert allowed_roots_configured() is True


def test_path_mode_disabled_entirely_when_no_roots_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.web, "allowed_roots", "")
    with pytest.raises(PathNotAllowedError):
        ensure_path_allowed(str(tmp_path), label="Kaynak Klasör")


def test_path_inside_an_allowed_root_is_accepted(monkeypatch, tmp_path):
    allowed = tmp_path / "projeler"
    allowed.mkdir()
    target = allowed / "poseidon"
    target.mkdir()
    monkeypatch.setattr(settings.web, "allowed_roots", str(allowed))

    resolved = ensure_path_allowed(str(target), label="Kaynak Klasör")

    assert resolved == target.resolve()


def test_path_equal_to_an_allowed_root_is_accepted(monkeypatch, tmp_path):
    allowed = tmp_path / "projeler"
    allowed.mkdir()
    monkeypatch.setattr(settings.web, "allowed_roots", str(allowed))

    resolved = ensure_path_allowed(str(allowed), label="Kaynak Klasör")

    assert resolved == allowed.resolve()


def test_path_outside_all_allowed_roots_is_rejected(monkeypatch, tmp_path):
    allowed = tmp_path / "projeler"
    allowed.mkdir()
    outside = tmp_path / "baska-yer"
    outside.mkdir()
    monkeypatch.setattr(settings.web, "allowed_roots", str(allowed))

    with pytest.raises(PathNotAllowedError):
        ensure_path_allowed(str(outside), label="Hedef Klasör")


def test_sibling_directory_with_overlapping_name_prefix_is_not_falsely_allowed(monkeypatch, tmp_path):
    # 'projeler-eski', 'projeler' ile ayni ONEKE sahip ama ALTINDA degil -
    # saf string.startswith() kontrolu bunu yanlislikla izin verirdi;
    # ensure_path_allowed Path.parents/esitlik kullanarak bunu ONLER.
    allowed = tmp_path / "projeler"
    allowed.mkdir()
    sibling = tmp_path / "projeler-eski"
    sibling.mkdir()
    monkeypatch.setattr(settings.web, "allowed_roots", str(allowed))

    with pytest.raises(PathNotAllowedError):
        ensure_path_allowed(str(sibling), label="Kaynak Klasör")


def test_multiple_allowed_roots_are_all_honored(monkeypatch, tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(settings.web, "allowed_roots", f"{first},{second}")

    assert ensure_path_allowed(str(first / "x"), label="x") == (first / "x").resolve()
    assert ensure_path_allowed(str(second / "y"), label="y") == (second / "y").resolve()
