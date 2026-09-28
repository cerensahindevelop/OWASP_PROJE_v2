"""Streamlit'in st.file_uploader ile yuklenen dosyalari, mevcut
export_project/unmask_project (klasor bazli) hattina beslemek icin gerekli
kucuk yardimcilar. Is mantigini TEKRAR YAZMAZ - sadece "yuklenen
dosya(lar) -> gecici bir kaynak klasoru" ve "hedef klasoru -> indirilebilir
bayt dizisi" donusumlerini yapar; asil maskeleme/geri-donusum export.py'de.

Uc yukleme bicimi desteklenir:
  - Tek bir .zip dosyasi: klasor yapisini (alt klasorler dahil) korumak
    icin - zip guvenli sekilde (path traversal kontrolu ile) acilir.
  - "Klasor" modu (st.file_uploader(accept_multiple_files="directory")):
    tarayici klasor secicisi (webkitdirectory) ile secilen TUM alt
    klasor yapisi korunarak yuklenir - UploadedFile.name bu modda
    'src/utils/helper.py' gibi GORELI bir yol tasir (duz dosya adi degil).
  - Bir ya da daha fazla duz dosya (klasor modu DEGIL): her biri hedef
    klasorun KOKUNE (alt klasor bilgisi olmadan) yazilir - normal dosya
    secici penceresi klasor yapisini Streamlit'e iletmez.
"""

from __future__ import annotations

import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

UPLOADS_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "uploads_output"
logger = logging.getLogger(__name__)


def _open_new_file(path: Path):
    # Exclusive creation also catches aliases on case-insensitive filesystems.
    try:
        return path.open("xb")
    except FileExistsError:
        raise ValueError("Yüklemede aynı hedefe gelen dosyalar var; dosya adlarını veya klasör yapısını düzeltin.") from None


# Bir zip girdisi ya da klasor-modu dosya yolu, hedef klasorun disina cikmaya
# calisiyorsa (path traversal / 'zip slip') firlatilir - dosya hic yazilmaz.
class UnsafePathError(ValueError):
    pass


# candidate, base klasorunun icinde mi (path traversal kontrolunun temel
# yapitasi) diye bakar.
def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


# Bir zip arsivini, her girdinin cozulmus yolu hedef klasorun disina
# cikmadigini onceden dogrulayarak acar ('zip slip' saldirisina karsi).
def _safe_extract_zip(zf: zipfile.ZipFile, target_dir: Path) -> None:
    resolved_target = target_dir.resolve()
    for member in zf.namelist():
        member_path = (resolved_target / member).resolve()
        if not _is_within(resolved_target, member_path):
            raise UnsafePathError(f"guvensiz zip girdisi (path traversal): {member!r}")
    for member in zf.infolist():
        destination = _safe_relative_path(resolved_target, member.filename)
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, _open_new_file(destination) as target:
                shutil.copyfileobj(source, target)


# Klasor modunda gelen "src/utils/helper.py" gibi goreli bir yolu, hedef
# klasorun disina cikamayacak sekilde dogrulayip mutlak bir Path'e cevirir.
def _safe_relative_path(resolved_root: Path, raw_name: str) -> Path:
    normalized = raw_name.replace("\\", "/").lstrip("/")
    candidate = (resolved_root / normalized).resolve()
    if not _is_within(resolved_root, candidate):
        raise UnsafePathError(f"guvensiz dosya yolu (path traversal): {raw_name!r}")
    return candidate


# Yuklenen dosya(lar)i yeni, bos bir gecici klasore yazar ve o klasorun
# yolunu dondurur.
#   - is_directory_upload=True: her UploadedFile.name GORELI BIR YOL olarak
#     ele alinir, alt klasor yapisi korunur.
#   - aksi halde: tek dosya bir .zip ise klasor yapisini koruyarak acilir;
#     birden fazla duz dosyaysa hepsi kokte duz olarak yazilir.
def save_uploaded_files_to_temp_dir(uploaded_files: list, *, is_directory_upload: bool = False) -> Path:
    if not uploaded_files:
        raise ValueError("en az bir dosya yuklenmeli")

    tmp_dir = Path(tempfile.mkdtemp(prefix="osw_upload_src_"))
    try:
        if is_directory_upload:
            resolved_root = tmp_dir.resolve()
            for uploaded in uploaded_files:
                dest = _safe_relative_path(resolved_root, uploaded.name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with _open_new_file(dest) as target:
                    target.write(uploaded.getvalue())
        elif len(uploaded_files) == 1 and uploaded_files[0].name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(uploaded_files[0].getvalue())) as zf:
                _safe_extract_zip(zf, tmp_dir)
        else:
            for uploaded in uploaded_files:
                dest = tmp_dir / Path(uploaded.name.replace("\\", "/")).name
                with _open_new_file(dest) as target:
                    target.write(uploaded.getvalue())
        return tmp_dir
    except Exception:
        cleanup_temp_dir(tmp_dir)
        raise


# Sonucun tek bir dosya olarak (zip'lemeden) indirilebilir olup olmadigini
# belirler. Klasor modunda alt yollar oldugu icin (orn. "src/a.py") bu
# kisayol atlanir - sonuc her zaman zip'lenir, aksi halde "duz dosya adi"
# varsayimi yanlis konuma yazmaya calisirdi.
def is_single_plain_file_upload(uploaded_files: list, *, is_directory_upload: bool = False) -> bool:
    if is_directory_upload:
        return False
    return len(uploaded_files) == 1 and not uploaded_files[0].name.lower().endswith(".zip")


# Bir hedef klasorun tum icerigini (alt klasorler dahil) tek bir ZIP
# arsivinin bayt dizisine paketler; st.download_button ile dogrudan
# indirilebilir sonucu uretmek icin kullanilir.
def zip_directory_to_bytes(source_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir))
    return buffer.getvalue()


# Yukleme modunda uretilen ciktinin kalici klasoru - export bitince SILINMEZ
# (karantinadaki bir dosya sonradan serbest birakilirsa hedefin var olmasi gerekir).
def uploaded_output_dir(token: str) -> Path:
    target = UPLOADS_OUTPUT_ROOT / token
    target.mkdir(parents=True, exist_ok=True)
    return target


# Yukleme/import sirasinda olusturulan gecici klasoru (kaynak veya import
# hedefi) siler. Bir hedef silinemese de digerleri denenir; hata gizlenmez.
def cleanup_temp_dir(*paths: Path | None) -> None:
    failed = False
    for path in paths:
        if path is None:
            continue
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            if not path.exists():
                continue
            failed = True
            logger.error("Geçici klasör temizlenemedi: %s", path)
        except OSError:
            failed = True
            logger.error("Geçici klasör temizlenemedi: %s", path)
    if failed:
        raise OSError("Geçici dosyalar tamamen silinemedi; hassas veri diskte kalmış olabilir. Yönetici sunucu kayıtlarını kontrol etmelidir.")
