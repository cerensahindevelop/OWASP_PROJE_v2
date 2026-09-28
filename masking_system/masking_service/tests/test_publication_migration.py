import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_status_migration_preserves_existing_data_and_indexes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "migration.db"
    env = dict(os.environ, DB_PATH=str(path))
    def migrate(revision):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision],
                                cwd=root, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    migrate("1c04eb3b4eae")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO maskeleme_baglamlari (id,proje_adi,personel_no,branch_adi) VALUES (1,'test','test','main')")
        connection.execute("INSERT INTO maskeleme_calismalari (id,baglam_id,islem_tipi,kaynak_yol,hedef_yol,baslatan,durum) VALUES (1,1,'mask','source','target','test','completed')")
        connection.execute("INSERT INTO denetim_kaydi (calisma_id,dosya_yolu,eylem,detay) VALUES (1,'file','skipped','test')")
        connection.execute("INSERT INTO deger_eslemeleri (baglam_id,orijinal_deger_sifreli,orijinal_deger_duz_metin,orijinal_deger_hash,yer_tutucu_degeri) VALUES (1,'encrypted-test','plain-test','test-hash','mask_test_1')")
        before = list(connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='maskeleme_calismalari'"))
    migrate("head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT orijinal_deger_duz_metin,yer_tutucu_degeri FROM deger_eslemeleri").fetchall() == [("plain-test", "mask_test_1")]
        assert connection.execute("SELECT count(*) FROM denetim_kaydi").fetchone()[0] == 1
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
        assert list(connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='maskeleme_calismalari'")) == before
        connection.execute("UPDATE maskeleme_calismalari SET durum='failed' WHERE id=1")
