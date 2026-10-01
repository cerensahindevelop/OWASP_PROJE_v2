"""Bir export calismasinda ciktiya alinmayan dosyalarin nedene gore dagilimi.

Veritabanini SALT OKUNUR acar (SQLite mode=ro); hicbir sey yazmaz. Dosya
yolu, deger, gerekce metni YAZDIRMAZ - yalnizca sayilar ve hata siniflari.

  python scripts/failed_check_summary.py --son
  python scripts/failed_check_summary.py --run-id 42
  python scripts/failed_check_summary.py --son --proje Poseidon

Eski calismalarda (basarisiz_kontrol kolonundan once) neden "bilinmiyor"
gorunur; olcum icin projeyi bu surumle yeniden export edin.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Tespit/denetim hatalarinin gerekce metninden, metni yazdirmadan cikarilan
# sinif. Once app.core.http_diagnostics.http_error_detail'in yapilandirilmis
# alanlari (hata=..., HTTP=...), sonra detector cokmesindeki istisna adi
# ("(TypeError)"), en son bilinen mesajlar. Gerekcenin genel oneki ("cagrisi
# basarisiz oldu ya da zaman asimina ugradi") siniflandirmada kullanilmaz.
_HTTP_ERROR_RE = re.compile(r"hata=(\w+)(?:; HTTP=(\d+))?")
_EXCEPTION_RE = re.compile(r"\((\w+(?:Error|Exception))\)")
_MESSAGE_CLASSES = (
    ("zaman_asimi", re.compile(r"sure siniri asildi")),
    ("yanit_kesildi", re.compile(r"finish_reason")),
    ("yanit_bicimi", re.compile(r"gecerli JSON degil|beklenen sekilde|liste degil")),
    ("ayar", re.compile(r"VLLM_HOST ve VLLM_MODEL zorunludur|prompt dosyasi okunamadi")),
)


def classify_reason(reason: str) -> str:
    reason = reason or ""
    http = _HTTP_ERROR_RE.search(reason)
    if http:
        return f"{http.group(1)}_{http.group(2)}" if http.group(2) else http.group(1)
    exception = _EXCEPTION_RE.search(reason)
    if exception:
        return exception.group(1)
    for name, pattern in _MESSAGE_CLASSES:
        if pattern.search(reason):
            return name
    return "diger"


def _connect(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise SystemExit(f"Veritabani bulunamadi: {database_path}")
    return sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)


def _pick_run(db: sqlite3.Connection, run_id: int | None, project: str | None) -> tuple[int, int | None]:
    query = (
        "SELECT c.id, c.dosya_sayisi FROM maskeleme_calismalari c JOIN maskeleme_baglamlari b ON b.id=c.baglam_id "
        "WHERE c.islem_tipi='mask'"
    )
    params: list = []
    if run_id is not None:
        query += " AND c.id=?"
        params.append(run_id)
    if project:
        query += " AND b.proje_adi=?"
        params.append(project)
    row = db.execute(query + " ORDER BY c.id DESC LIMIT 1", params).fetchone()
    if row is None:
        raise SystemExit("Uygun export calismasi bulunamadi")
    return row[0], row[1]


def summarize(db: sqlite3.Connection, run_id: int) -> dict:
    columns = {row[1] for row in db.execute("PRAGMA table_info(denetim_uyarilari)")}
    check_column = "basarisiz_kontrol" if "basarisiz_kontrol" in columns else "NULL"
    rows = db.execute(
        f"SELECT dosya_yolu, {check_column}, gerekce FROM denetim_uyarilari WHERE calisma_id=? ORDER BY id",
        (run_id,),
    ).fetchall()
    # Bir dosyanin ilk uyarisi export sirasindaki nedendir.
    first_by_file: dict[str, tuple[str | None, str]] = {}
    for path, check, reason in rows:
        first_by_file.setdefault(path, (check, reason))
    by_check: dict[str, int] = {}
    error_classes: dict[str, dict[str, int]] = {}
    for check, reason in first_by_file.values():
        key = check or "bilinmiyor"
        by_check[key] = by_check.get(key, 0) + 1
        if key in {"llm_tespit", "llm_denetimi_tamamlanamadi", "tespit_katmani", "bilinmiyor"}:
            bucket = error_classes.setdefault(key, {})
            label = classify_reason(reason)
            bucket[label] = bucket.get(label, 0) + 1
    remediation: dict[str, int] = {}
    for (detail,) in db.execute(
        "SELECT detay FROM denetim_kaydi WHERE calisma_id=? AND detay LIKE 'auto_remediation=failed check=%'",
        (run_id,),
    ):
        code = detail.split("check=", 1)[1].split()[0]
        remediation[code] = remediation.get(code, 0) + 1
    remediated = db.execute(
        "SELECT COUNT(DISTINCT dosya_yolu) FROM denetim_kaydi WHERE calisma_id=? AND detay LIKE 'auto_remediated %'",
        (run_id,),
    ).fetchone()[0]
    # Yalnizca olcum (Faz 2a, kural 9): dosyayi engellemez, ayri sayilir.
    mismatch_files = mismatch_terms = 0
    for (detail,) in db.execute(
        "SELECT detay FROM denetim_kaydi WHERE calisma_id=? AND detay LIKE 'path_content_check check=%'",
        (run_id,),
    ):
        mismatch_files += 1
        mismatch_terms += int(detail.rsplit("terms=", 1)[1].split()[0])
    return {
        "yol_icerik_uyusmazligi": {"dosya": mismatch_files, "terim": mismatch_terms},
        "uyarili_dosya": len(first_by_file),
        "nedene_gore": dict(sorted(by_check.items(), key=lambda item: (-item[1], item[0]))),
        "hata_siniflari": error_classes,
        "otomatik_duzeltme_basarisiz": remediation,
        "otomatik_duzeltilen_dosya": remediated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", type=int)
    group.add_argument("--son", action="store_true", help="En son export calismasi")
    parser.add_argument("--proje", help="Yalnizca bu proje adinin calismalari")
    parser.add_argument("--db", type=Path, help="Varsayilan: .env'deki DB_PATH")
    args = parser.parse_args()

    if args.db is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.core.config import DatabaseSettings

        args.db = Path(DatabaseSettings().resolved_path)
    db = _connect(args.db)
    try:
        run_id, scanned = _pick_run(db, args.run_id, args.proje)
        summary = summarize(db, run_id)
    finally:
        db.close()

    total = scanned or 0
    print(f"run_id={run_id} taranan_dosya={total} uyarili_dosya={summary['uyarili_dosya']}"
          + (f" oran={summary['uyarili_dosya'] / total:.0%}" if total else ""))
    print("Nedene gore (dosya):")
    for check, count in summary["nedene_gore"].items():
        print(f"  {check}: {count}")
    for check, classes in summary["hata_siniflari"].items():
        print(f"Hata siniflari [{check}]: " + ", ".join(f"{k}={v}" for k, v in sorted(classes.items())))
    if summary["otomatik_duzeltme_basarisiz"]:
        print("Otomatik duzeltme basarisizlik nedeni: "
              + ", ".join(f"{k}={v}" for k, v in sorted(summary["otomatik_duzeltme_basarisiz"].items())))
    print(f"Otomatik duzeltilip yayinlanan dosya: {summary['otomatik_duzeltilen_dosya']}")
    mismatch = summary["yol_icerik_uyusmazligi"]
    print(f"Yol/icerik uyusmazligi (yalnizca olcum, yayinlanan dosya): dosya={mismatch['dosya']} "
          f"terim={mismatch['terim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
