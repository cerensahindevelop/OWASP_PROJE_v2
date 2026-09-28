import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


def test_job_migration_preserves_legacy_rows_and_enforces_scope(tmp_path):
    path = tmp_path / 'migration.db'
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, DB_PATH=str(path))
    def migrate(command, revision, success=True):
        result = subprocess.run([sys.executable, '-m', 'alembic', command, revision],
                                cwd=root, env=env, capture_output=True, text=True, timeout=30)
        assert (result.returncode == 0) == success, result.stderr
        return result
    migrate('upgrade', 'b6f209a731cd')
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO maskeleme_baglamlari (id,proje_adi,personel_no,branch_adi) VALUES (1,'project','Ceren','main')")
        db.execute("INSERT INTO maskeleme_calismalari (id,baglam_id,islem_tipi,kaynak_yol,baslatan,durum) VALUES (1,1,'mask','src','Ceren','completed')")
        db.execute("INSERT INTO deger_eslemeleri (baglam_id,orijinal_deger_sifreli,orijinal_deger_duz_metin,orijinal_deger_hash,yer_tutucu_degeri) VALUES (1,'encrypted','old','old-hash','mask_ip_1')")
    migrate('upgrade', 'head')
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        assert db.execute('SELECT calisma_id,orijinal_deger_sifreli,orijinal_deger_duz_metin,yer_tutucu_degeri FROM deger_eslemeleri').fetchall() == [(None,'encrypted','old','mask_ip_1')]
        assert db.execute('SELECT esleme_surumu FROM maskeleme_calismalari').fetchall() == [(1,)]
        for job in [2, 3]:
            db.execute("INSERT INTO maskeleme_calismalari (id,baglam_id,islem_tipi,kaynak_yol,baslatan,durum,esleme_surumu) VALUES (?,1,'mask','src','Ceren','completed',2)", (job,))
            db.execute("INSERT INTO deger_eslemeleri (calisma_id,baglam_id,orijinal_deger_sifreli,orijinal_deger_duz_metin,orijinal_deger_hash,yer_tutucu_degeri) VALUES (?,1,'encrypted','new','new-hash','mask_ip_1')", (job,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO deger_eslemeleri (calisma_id,baglam_id,orijinal_deger_sifreli,orijinal_deger_duz_metin,orijinal_deger_hash,yer_tutucu_degeri) VALUES (2,1,'encrypted','other','other-hash','mask_ip_1')")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    failure = migrate('downgrade', 'b6f209a731cd', success=False)
    assert 'veri kaybettirir' in failure.stderr
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM deger_eslemeleri').fetchone()[0] == 3
