"""f1c3a5e7b9d2: sicil kurali kategorisi personnel_no -> sicil_no.

Gercek alembic komutlariyla, gecici bir SQLite dosyasinda: yalnizca seed
kaydi duzeltilir, ikinci calistirma ve elle kurulmus DB bozulmaz, downgrade
yalnizca duzeltilen kaydi geri cevirir.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "e3a7c1f9d2b5"
FIX = "f1c3a5e7b9d2"
SEED_DESC = "Personel numarasi - calisma zamaninda saglanan literal deger."


@pytest.fixture
def migrate(tmp_path):
    path = tmp_path / "migration.db"
    env = dict(os.environ, DB_PATH=str(path), SECURITY_ENCRYPTION_KEY=Fernet.generate_key().decode())

    def run(command: str, revision: str) -> str:
        result = subprocess.run([sys.executable, "-m", "alembic", command, revision],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout + result.stderr

    run.path = path
    return run


def _rules(path: Path) -> dict[str, tuple]:
    with sqlite3.connect(path) as db:
        return {row[1]: row for row in db.execute(
            "SELECT id, kural_adi, kategori, aktif_mi, oncelik, aciklama, yer_tutucu_on_eki "
            "FROM filtre_kurallari WHERE desen_tipi = 'parametric'")}


def _sql(path: Path, statement: str) -> None:
    with sqlite3.connect(path) as db:
        db.execute(statement)


def test_fresh_install_gets_sicil_no_rule(migrate):
    migrate("upgrade", "head")
    rules = _rules(migrate.path)
    assert "personnel_no" not in rules
    assert rules["sicil_no"][2] == "sicil_no" and rules["sicil_no"][6] == "mask_personel_no"
    assert {row[2] for row in rules.values()} == {"project_name", "sicil_no", "branch_name"}


def test_fix_preserves_id_prefix_and_operational_settings(migrate):
    migrate("upgrade", BEFORE)
    _sql(migrate.path, "UPDATE filtre_kurallari SET aktif_mi = 0, oncelik = 7 WHERE kural_adi = 'personnel_no'")
    before = _rules(migrate.path)["personnel_no"]
    migrate("upgrade", FIX)
    after = _rules(migrate.path)["sicil_no"]
    assert after[0] == before[0]  # ayni kayit: mevcut eslemeler (kural_id) gecerli kalir
    assert (after[3], after[4], after[5], after[6]) == (0, 7, SEED_DESC, "mask_personel_no")


def test_running_twice_and_downgrade_round_trip(migrate):
    migrate("upgrade", "head")
    fixed = _rules(migrate.path)
    migrate("stamp", BEFORE)  # ayni migrasyonun duzeltilmis DB'de ikinci kez calismasi
    migrate("upgrade", FIX)
    assert _rules(migrate.path) == fixed
    migrate("downgrade", BEFORE)
    rules = _rules(migrate.path)
    assert "sicil_no" not in rules and rules["personnel_no"][2] == "personnel_no"
    assert rules["personnel_no"][0] == fixed["sicil_no"][0]
    migrate("downgrade", BEFORE)  # zaten bu surumde: no-op
    migrate("upgrade", "head")
    assert _rules(migrate.path) == fixed


def test_user_modified_seed_rule_is_left_alone_with_warning(migrate):
    migrate("upgrade", BEFORE)
    _sql(migrate.path, "UPDATE filtre_kurallari SET aciklama = 'kurum ici aciklama' WHERE kural_adi = 'personnel_no'")
    output = migrate("upgrade", FIX)
    rules = _rules(migrate.path)
    assert rules["personnel_no"][2] == "personnel_no" and "sicil_no" not in rules
    assert "DEGISTIRILMEDI" in output and "aktif 'sicil_no' parametrik kurali yok" in output


def test_manually_installed_sicil_rule_is_untouched_both_ways(migrate):
    # Elle kurulmus DB: kural zaten sicil_no (seed_data.py bicimi, farkli aciklama).
    migrate("upgrade", BEFORE)
    _sql(migrate.path, "UPDATE filtre_kurallari SET kural_adi = 'sicil_no', kategori = 'sicil_no', "
                       "aciklama = 'sicil numarasi - calisma zamaninda saglanan literal deger.' "
                       "WHERE kural_adi = 'personnel_no'")
    manual = _rules(migrate.path)
    migrate("upgrade", FIX)
    assert _rules(migrate.path) == manual
    migrate("downgrade", BEFORE)
    assert _rules(migrate.path) == manual


def test_existing_sicil_rule_blocks_rename_without_breaking(migrate):
    migrate("upgrade", BEFORE)
    _sql(migrate.path, "INSERT INTO filtre_kurallari (kural_adi, kategori, kaynak_katman, desen_tipi, "
                       "desen_sifreli_mi, yer_tutucu_on_eki, guven_skoru, allow_list_mi, oncelik, aktif_mi) "
                       "VALUES ('sicil_no', 'sicil_no', 'katman1', 'parametric', 0, 'mask_sicil', 0.85, 0, 2, 1)")
    output = migrate("upgrade", FIX)
    rules = _rules(migrate.path)
    assert rules["personnel_no"][2] == "personnel_no" and rules["sicil_no"][6] == "mask_sicil"
    assert "DEGISTIRILMEDI" in output
