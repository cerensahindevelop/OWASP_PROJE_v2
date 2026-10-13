"""Bir kurumsal terimin bir dosyada neden maskelenmedigini teshis eder.

Backend'in Python'u ile masking_service klasorunden calistirilir:

    .venv\\Scripts\\python.exe scripts\\diagnose_term.py --term "fistikci" --file "C:\\proje\\config\\app.env"

Hicbir sey degistirmez. Ciktida terim, dosya icerigi veya yol yazilmaz; yalnizca
evet/hayir sonuclari, sayilar ve satir numaralari yazilir. Cikti guvenle
paylasilabilir.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TR_FOLD = str.maketrans({"ş": "s", "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ü": "u"})


# Turkce harf, buyuk/kucuk harf ve ayrisik (NFD) yazim farklarini yok sayan
# kaba karsilastirma bicimi: deseni atlatan gecisleri bulmak icin.
def loose(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.translate(_TR_FOLD)


def combining_count(text: str) -> int:
    return sum(1 for ch in text if unicodedata.combining(ch))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--term", required=True, help="Sozlukteki terim (yaklasik yazim yeterli)")
    parser.add_argument("--file", required=True, type=Path, help="Kaynak projedeki (maskelenmemis) dosya")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.services.exclude_admin import load_active_exclude_specs
    from app.services.exclude_engine import matches_exclude
    from app.services.file_classifier import ignored_directory_reason
    from app.services.mapping_service import load_active_rules
    from app.services.rule_engine import _compile_flags
    from app.services.term_upload import corporate_term_pattern, list_corporate_terms

    wanted = loose(args.term.strip())
    with SessionLocal() as db:
        records = [r for r in list_corporate_terms(db, include_deleted=True) if loose(r.term) == wanted]
        rules = {rule.id: rule for rule in load_active_rules(db)}
        exclude_specs = load_active_exclude_specs(db)

    print(f"sozluk_kaydi_sayisi={len(records)}")
    if not records:
        print("SONUC: terim sozlukte bulunamadi (yazim farkli olabilir ya da hic eklenmemis).")
    patterns = []
    for index, record in enumerate(records, start=1):
        rule = rules.get(record.id)
        current = corporate_term_pattern(record.term)
        print(
            f"kayit{index}: aktif={record.is_active} silinmis={record.deleted_at is not None} "
            f"pasif_nedeni={record.inactive_reason or '-'} "
            f"kayitli_yazim_nfc={unicodedata.is_normalized('NFC', record.term)} "
            f"ayrisik_isaret_sayisi={combining_count(record.term)} "
            f"tarama_kurali_yuklendi={rule is not None} "
            f"desen_guncel={rule is not None and rule.regex_pattern == current}"
        )
        if rule is not None:
            patterns.append(re.compile(rule.regex_pattern, _compile_flags(rule.regex_flags)))

    path = args.file
    if not path.is_file():
        print("SONUC: dosya bulunamadi.")
        return 1
    excluded = matches_exclude(path.name, exclude_specs, "file")
    ignored_dir = any(ignored_directory_reason(Path(*path.parts[i:])) for i in range(len(path.parts)))
    print(f"dosya_haric_tutma_kuralina_uyuyor={excluded is not None} "
          f"yol_yok_sayilan_klasorde_olabilir={ignored_dir}")

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1254", errors="replace")
        encoding = "utf-8 degil (cp1254 denendi)"
    print(f"dosya_kodlamasi={encoding} dosyadaki_ayrisik_isaret_sayisi={combining_count(text)}")

    missed, matched = [], 0
    for number, line in enumerate(text.splitlines(), start=1):
        if wanted not in loose(line):
            continue
        if any(p.search(line) for p in patterns):
            matched += 1
        else:
            missed.append(number)
    print(f"terimi_iceren_satir={matched + len(missed)} desenle_yakalanan={matched} "
          f"desenin_kacirdigi={len(missed)} kacirilan_satirlar={missed[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
