"""Sicil sizintisi (personnel_no/sicil_no) gecmis etki raporu - SALT OKUNUR.

Alembic seed'inden gelen sicil kurali `personnel_no` kategorisindeyken
(migrasyon f1c3a5e7b9d2 oncesi) export edilen hicbir dosyada kullanicinin
sicil degeri maskelenmedi. Bu komut hangi export'larin etkilenmis
olabilecegini listeler. Yalnizca run kimligi, tarih, durum ve sayilar
yazar; sicil degeri, proje adi ya da dosya/klasor yolu YAZMAZ.

Siniflandirma:
- `etkilenmedi`: o run'da sicil icin en az bir esleme var (sicil maskelendi).
- `olasi`: sicil eslemesi yok. Duzeltme oncesindeyse sicil kaynakta
  geciyorduysa maskelenmeden ciktiya gitti; duzeltme sonrasindaysa sicil
  kaynakta hic gecmemistir. DB bu ikisini ayirt edemez (eslenmeyen deger
  kaydedilmez).
- `--tara` ile `olasi` run'larin kaynak ve hedef klasorleri (sunucuda hala
  varsa) sicil degeri icin taranir (maskelemeyle ayni parametrik
  eslestirici, yalnizca okuma). Sonuc yalnizca dosya SAYISIDIR:
  `kaynakta=N`: sicil kaynakta N dosyada geciyor (N>0 ve esleme yok ->
  sizinti); `ciktida=N`: hedef klasorde bugun N dosyada acik. Hedef klasor
  sonraki bir export'la uzerine yazilmis olabilir; `ciktida` bugunku durumu
  gosterir.

Kullanim (masking_service klasorunde):
    .venv\\Scripts\\python.exe scripts\\sicil_etki_raporu.py [--db yol] [--once 2026-10-02] [--tara]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

_MAX_SCAN_BYTES = 5 * 1024 * 1024
_SICIL_CATEGORIES = ("sicil_no", "personnel_no")


def _connect(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise SystemExit(f"Veritabani bulunamadi: {database_path}")
    return sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)


def rule_state(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT kategori, aktif_mi FROM filtre_kurallari WHERE desen_tipi = 'parametric' AND kategori IN (?, ?)",
        _SICIL_CATEGORIES,
    ).fetchall()
    if any(category == "sicil_no" and active for category, active in rows):
        return "duzeltilmis"
    return "ETKILENIYOR" if rows else "sicil_kurali_yok"


def _sicil_matcher(sicil: str):
    from app.services.rule_engine import RuleSpec, find_matches

    rule = RuleSpec(id=0, rule_name="sicil_no", category="sicil_no", pattern_type="parametric",
                    regex_pattern=None, regex_flags=None, placeholder_prefix="mask_personel_no", priority=2)

    def contains(text: str) -> bool:
        matches, _already_masked = find_matches([rule], text, {"sicil_no": sicil})
        return bool(matches)
    return contains


def count_files(directory: str | None, sicil: str) -> int | None:
    """Klasorde (dosya icerigi ya da goreli yolu) sicil gecen dosya sayisi; klasor yoksa None."""
    if not directory or not sicil:
        return None
    root = Path(directory)
    if not root.is_dir():
        return None
    contains = _sicil_matcher(sicil)
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        hit = contains(path.relative_to(root).as_posix())
        if not hit and path.stat().st_size <= _MAX_SCAN_BYTES:
            data = path.read_bytes()
            if b"\x00" not in data[:8192]:
                hit = contains(data.decode("utf-8", errors="ignore"))
        count += hit
    return count


def affected_runs(db: sqlite3.Connection, before: str | None = None) -> list[dict]:
    rule_ids = [row[0] for row in db.execute(
        "SELECT id FROM filtre_kurallari WHERE desen_tipi = 'parametric' AND kategori IN (?, ?)", _SICIL_CATEGORIES,
    )]
    marks = ",".join("?" * len(rule_ids)) or "NULL"
    query = f"""
        SELECT r.id, r.baslangic_tarihi, r.durum, r.kaynak_yol, r.hedef_yol, c.personel_no,
               (SELECT COUNT(*) FROM deger_eslemeleri m WHERE m.kural_id IN ({marks})
                  AND (m.calisma_id = r.id OR (m.calisma_id IS NULL AND m.baglam_id = r.baglam_id))) AS eslemeler
        FROM maskeleme_calismalari r JOIN maskeleme_baglamlari c ON c.id = r.baglam_id
        WHERE r.islem_tipi = 'mask'
    """
    params: list = list(rule_ids)
    if before:
        query += " AND r.baslangic_tarihi < ?"
        params.append(before)
    query += " ORDER BY r.id"
    return [
        dict(run_id=row[0], tarih=row[1], durum=row[2], kaynak=row[3], hedef=row[4], sicil=row[5], eslemeler=row[6])
        for row in db.execute(query, params)
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, help="Varsayilan: .env'deki DB_PATH")
    parser.add_argument("--once", help="Yalnizca bu tarihten (YYYY-MM-DD) once baslayan export'lar")
    parser.add_argument("--tara", action="store_true", help="olasi run'larin kaynak/hedef klasorlerini salt okunur tara")
    args = parser.parse_args(argv)
    if args.db is None:
        from app.core.config import DatabaseSettings

        args.db = Path(DatabaseSettings().resolved_path)
    db = _connect(args.db)
    try:
        state = rule_state(db)
        runs = affected_runs(db, args.once)
    finally:
        db.close()

    print(f"sicil_kurali={state} export_sayisi={len(runs)}")
    possible = 0
    for run in runs:
        verdict = "etkilenmedi" if run["eslemeler"] else "olasi"
        line = (f"run_id={run['run_id']} tarih={run['tarih']} durum={run['durum']} "
                f"sicil_eslemesi={run['eslemeler']} sonuc={verdict}")
        if verdict == "olasi":
            possible += 1
            if args.tara:
                source = count_files(run["kaynak"], run["sicil"])
                target = count_files(run["hedef"], run["sicil"])
                line += (f" kaynakta={'yok' if source is None else source}"
                         f" ciktida={'yok' if target is None else target}")
        print(line)
    print(f"olasi_etkilenen={possible}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
