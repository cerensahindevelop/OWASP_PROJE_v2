"""Export (mask) ve unmask akislarinin ikisinde de BIREBIR ayni sekilde
calisan, DB'siz dosya on-isleme adimi: haric tutma/symlink/boyut/binary/
encoding kontrolleri.

AuditLog yazma islemi ve maskeleme/geri-donusum'e ozgu sonraki adimlar
KASITLI olarak burada DEGIL, cagiran tarafta (exporter.py/unmasker.py)
kalir - boylece bu modul rule_engine.py/exclude_engine.py ile ayni ilkeyi
paylasir (DB'siz, saf/trivially test edilebilir) VE iki cagiranin audit
metinlerindeki (export/unmask arasinda kasitli olarak farkli olan)
kucuk farklar hicbir davranis degisikligi olmadan korunur.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Bir dosyanin metin mi binary mi oldugunu (ve encoding'ini) ilk birkac
# byte'ina bakarak tahmin eden yardimci - once bu kontrolden geciriliyor.
from app.services.file_type import peek_classify
from app.services.file_classifier import is_archive_filename, is_lock_filename, is_opaque_binary_filename
from app.services.java_classfile import JAVA_CLASS_ENCODING, JavaClass, ClassFormatError, parse_class


# read_scanned_file'in bir dosya icin varabilecegi olasi sonuc durumlari.
class ReadStatus(str, Enum):
    EXCLUDED = "excluded"
    SKIPPED_SYMLINK = "skipped_symlink"
    SKIPPED_TOO_LARGE = "skipped_too_large"
    COPIED_BINARY = "copied_binary"
    COPIED_UNDECODABLE = "copied_undecodable"
    # ARCHIVE politikasi: proje ici bir arsiv (.zip/.tar/.gz/.rar/.7z) -
    # icerigi hic okunmadan/acilmadan quarantine/review'e yonlendirilir
    # (bkz. exporter.py _prepare_file). Recursive extraction bu surumde yok.
    ARCHIVE_UNSUPPORTED = "archive_unsupported"
    ERROR = "error"
    TEXT_READY = "text_ready"
    # SCAN_ONLY politikasi: gercek bir bagimlilik lock/integrity dosyasi -
    # TEXT_READY ile AYNI text/encoding cikti sekli, ama caller (exporter.py)
    # bunu maskelemek YERINE sadece tarayip byte-identical kopyalar/quarantine
    # eder (bkz. modul basindaki "Taranamadi != Temiz" ilkesi).
    SCAN_ONLY_TEXT_READY = "scan_only_text_ready"


# read_scanned_file'in donus degeri: durum, (varsa) okunan metin/encoding,
# dosya boyutu ve hata bilgisini bir arada tasir.
@dataclass(frozen=True)
class ReadOutcome:
    status: ReadStatus
    text: str | None = None
    encoding: str | None = None
    detected_encoding: str | None = None
    size: int | None = None
    error: str | None = None
    class_document: JavaClass | None = None


# Dosya islemi hatasini asama + gercekten basarisiz olan yol (+ uzunlugu:
# Windows MAX_PATH teshisi icin) ile anlatir. Ornek: Errno 2'nin kaynak mi
# hedef mi, hangi adimda oldugu mesajdan anlasilsin.
def describe_file_error(stage: str, path: Path, exc: BaseException) -> str:
    failing = getattr(exc, "filename", None) or path
    return (f"{stage} basarisiz (yol={failing}, yol_uzunlugu={len(str(failing))}, "
            f"hata={type(exc).__name__}): {exc}")


# Bir dosyayi haric tutma/symlink/boyut/binary/encoding kontrollerinden gecirir;
# metin ise henuz yazmadan isleme birakir; guvenlikle taranamayan dosyayi output'a yazmaz.
def read_scanned_file(
    scanned, dest_path: Path, max_inline_size: int, *, copy_unscannable: bool = True,
    encoding_hint: str | None = None,
) -> ReadOutcome:
    if scanned.excluded_by is not None:
        return ReadOutcome(status=ReadStatus.EXCLUDED)

    if scanned.is_symlink:
        return ReadOutcome(status=ReadStatus.SKIPPED_SYMLINK)

    try:
        size = scanned.absolute_path.stat().st_size
    except OSError as exc:
        return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("stat", scanned.absolute_path, exc))

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    file_name = scanned.absolute_path.name

    # BINARY/UNSCANNED: known opaque formats (compiled/binary/office/image/
    # pdf/jar) short-circuit BEFORE the size check and any content sniffing -
    # their verdict is "unsupported type" regardless of size (a large .jar
    # must not be reported as a failed security check), and decoded as text
    # or not, they must never enter the text masking pipeline (bkz.
    # file_classifier.is_opaque_binary_filename dokstringi).
    if is_opaque_binary_filename(file_name):
        if copy_unscannable:
            try:
                shutil.copy2(scanned.absolute_path, dest_path)
            except OSError as exc:
                return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("kopyalama", scanned.absolute_path, exc))
        return ReadOutcome(status=ReadStatus.COPIED_BINARY, size=size)

    if size > max_inline_size:
        if copy_unscannable:
            try:
                shutil.copy2(scanned.absolute_path, dest_path)
            except OSError as exc:
                return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("kopyalama", scanned.absolute_path, exc))
        return ReadOutcome(status=ReadStatus.SKIPPED_TOO_LARGE, size=size)

    if scanned.absolute_path.suffix.lower() == ".class" or encoding_hint == JAVA_CLASS_ENCODING:
        try:
            document = parse_class(scanned.absolute_path.read_bytes())
            return ReadOutcome(status=ReadStatus.TEXT_READY, text=document.text,
                               encoding=JAVA_CLASS_ENCODING, class_document=document)
        except (OSError, ClassFormatError) as exc:
            return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("class okuma", scanned.absolute_path, exc))

    # ARCHIVE: recursive extraction/repack is out of scope for this version -
    # never opened, never content-sniffed, never silently ignored either.
    if is_archive_filename(file_name):
        if copy_unscannable:
            try:
                shutil.copy2(scanned.absolute_path, dest_path)
            except OSError as exc:
                return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("kopyalama", scanned.absolute_path, exc))
        return ReadOutcome(status=ReadStatus.ARCHIVE_UNSUPPORTED)

    is_lock_file = is_lock_filename(file_name)

    try:
        is_text, encoding = (True, encoding_hint) if encoding_hint else peek_classify(scanned.absolute_path)
    except OSError as exc:
        return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("okuma", scanned.absolute_path, exc))

    if not is_text:
        if copy_unscannable:
            try:
                shutil.copy2(scanned.absolute_path, dest_path)
            except OSError as exc:
                return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("kopyalama", scanned.absolute_path, exc))
        return ReadOutcome(status=ReadStatus.COPIED_BINARY)

    try:
        raw = scanned.absolute_path.read_bytes()
    except OSError as exc:
        return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("okuma", scanned.absolute_path, exc))

    text = None
    detected_encoding = encoding
    for candidate_encoding in [encoding, "utf-8"]:
        if not candidate_encoding:
            continue
        try:
            text = raw.decode(candidate_encoding, errors="strict")
            if text.encode(candidate_encoding, errors="strict") != raw:
                text = None
                continue
            encoding = candidate_encoding
            break
        except (UnicodeError, LookupError):
            continue

    if text is None:
        if copy_unscannable:
            try:
                dest_path.write_bytes(raw)
            except OSError as exc:
                return ReadOutcome(status=ReadStatus.ERROR, error=describe_file_error("kopyalama", scanned.absolute_path, exc))
        return ReadOutcome(status=ReadStatus.COPIED_UNDECODABLE, detected_encoding=detected_encoding)

    final_status = ReadStatus.SCAN_ONLY_TEXT_READY if is_lock_file else ReadStatus.TEXT_READY
    return ReadOutcome(status=final_status, text=text, encoding=encoding)
