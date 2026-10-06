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
    # FIX'e kadar: sonraki migrasyonlar stamp ile geri sarilamaz (kolonlari kalir).
    migrate("upgrade", FIX)
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
    # Elle kurulmus DB: kural zaten sicil_no (eski app/db/seed_data.py bicimi, farkli aciklama).
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


# --- Gecmis etki raporu (scripts/sicil_etki_raporu.py, salt okunur) -----------

def _report(path: Path, *args: str) -> str:
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "sicil_etki_raporu.py"), "--db", str(path), *args],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_impact_report_lists_runs_without_values_or_paths(migrate, tmp_path):
    migrate("upgrade", BEFORE)
    source, target = tmp_path / "gizli-proje-kaynak", tmp_path / "gizli-proje-cikti"
    for root in (source, target):
        (root / "ekip" / "Z998877").mkdir(parents=True)
        (root / "ekip" / "Z998877" / "not.md").write_text("Acan: Z998877\n", encoding="utf-8")
        (root / "temiz.md").write_text("sicil yok\n", encoding="utf-8")
    with sqlite3.connect(migrate.path) as db:
        db.execute("INSERT INTO maskeleme_baglamlari (id, proje_adi, personel_no, branch_adi) "
                   "VALUES (1, 'gizli-proje', 'Z998877', 'main')")
        for run_id, date in ((1, "2026-09-01 10:00:00"), (2, "2026-09-02 10:00:00")):
            db.execute("INSERT INTO maskeleme_calismalari (id, baglam_id, islem_tipi, kaynak_yol, hedef_yol, "
                       "baslatan, durum, baslangic_tarihi, esleme_surumu) VALUES (?, 1, 'mask', ?, ?, 'Z998877', "
                       "'completed', ?, 2)", (run_id, str(source), str(target), date))
        db.execute("INSERT INTO maskeleme_calismalari (id, baglam_id, islem_tipi, kaynak_yol, hedef_yol, baslatan, "
                   "durum, esleme_surumu) VALUES (3, 1, 'unmask', 'x', 'y', 'Z998877', 'completed', 2)")
        rule_id = db.execute("SELECT id FROM filtre_kurallari WHERE kural_adi = 'personnel_no'").fetchone()[0]
        db.execute("INSERT INTO deger_eslemeleri (calisma_id, baglam_id, kural_id, orijinal_deger_sifreli, "
                   "orijinal_deger_duz_metin, orijinal_deger_hash, yer_tutucu_degeri) "
                   "VALUES (2, 1, ?, 'x', 'x', 'h', 'mask_personel_no_1')", (rule_id,))
    before = (sqlite3.connect(migrate.path).execute("SELECT COUNT(*), SUM(id) FROM maskeleme_calismalari").fetchone())

    output = _report(migrate.path, "--tara")
    lines = output.splitlines()
    assert lines[0] == "sicil_kurali=ETKILENIYOR export_sayisi=2"
    assert lines[1] == ("run_id=1 tarih=2026-09-01 10:00:00 durum=completed sicil_eslemesi=0 sonuc=olasi "
                        "kaynakta=1 ciktida=1")
    assert lines[2].startswith("run_id=2 ") and lines[2].endswith("sonuc=etkilenmedi")
    assert lines[-1] == "olasi_etkilenen=1"
    assert "Z998877" not in output and "gizli" not in output and str(tmp_path) not in output
    assert _report(migrate.path, "--once", "2026-09-02").splitlines()[-1] == "olasi_etkilenen=1"
    # Salt okunur: DB degismedi.
    assert sqlite3.connect(migrate.path).execute(
        "SELECT COUNT(*), SUM(id) FROM maskeleme_calismalari").fetchone() == before

    migrate("upgrade", FIX)
    assert _report(migrate.path).splitlines()[0] == "sicil_kurali=duzeltilmis export_sayisi=2"


def test_preflight_runtime_rules_stage(migrate, capsys):
    from scripts import check_llm_preflight as preflight

    migrate("upgrade", BEFORE)
    assert preflight.check_runtime_rules(migrate.path) == 1
    output = capsys.readouterr().out
    assert "FAIL stage=runtime_rules kategori=personnel_no reason=hicbir_zaman_eslesmez" in output
    assert "FAIL stage=runtime_rules kategori=sicil_no reason=aktif_kural_yok_deger_maskelenmez" in output
    migrate("upgrade", FIX)
    assert preflight.check_runtime_rules(migrate.path) == 0
    assert "PASS stage=runtime_rules project_name=aktif sicil_no=aktif branch_name=aktif" in capsys.readouterr().out
