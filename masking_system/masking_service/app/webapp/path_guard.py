"""Web arayuzunun 'Klasor Yolu' modunda kaynak/hedef olarak kabul edilecek
dizinleri, sunucu yoneticisinin WEB_ALLOWED_ROOTS ile beyaz listeye aldigi
kok dizinlerle sinirlar.

Bu modul olmadan: giris (auth) yapmis olmak tek basina yeterli DEGILDI -
gecerli herhangi bir web-login hesabina sahip biri, sunucu process'inin
erisebildigi HERHANGI BIR dizini "Hedef Klasör" gösterip
(hedef hazirlanan yeni ciktiyla degistirildigi icin) degistirebilir,
ya da herhangi bir dizini "Kaynak Klasör"
gösterip disaridan taranmasini bekleyen herhangi bir proje disindaki
veriyi tarayabilirdi. WEB_ALLOWED_ROOTS ile beyaz listeye alma, bu riski
CLI'in (guvenilir operatör, serbest yol) esnekligini kaybetmeden sadece
ag uzerinden erisilebilen web yuzeyi icin kapatir.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings


# Verilen yol, WEB_ALLOWED_ROOTS ile izin verilen hicbir kok dizinin
# altinda degilse (ya da hic kok tanimlanmamissa) firlatilir.
class PathNotAllowedError(ValueError):
    pass


# WEB_ALLOWED_ROOTS hic tanimlanmamissa (bos) 'Klasor Yolu' modu tamamen
# kapali sayilir - varsayilan GUVENLI (izin verilmeyen) tercih.
def allowed_roots_configured() -> bool:
    return bool(settings.web.allowed_root_paths)


# candidate, izinli koklerden en az birinin altinda (ya da tam olarak
# kendisi) mi diye kontrol eder; degilse PathNotAllowedError firlatir,
# gecerliyse cozumlenmis (resolve edilmis) Path'i dondurur.
def ensure_path_allowed(candidate: str, *, label: str) -> Path:
    roots = settings.web.allowed_root_paths
    if not roots:
        raise PathNotAllowedError(
            "'Klasör Yolu' modu bu kurulumda kapalı (sunucuda WEB_ALLOWED_ROOTS tanımlı değil). "
            "Sunucu yöneticisi .env dosyasında izinli kök dizinleri tanımlamalı, ya da "
            "'Dosya Yükle' modunu kullanın."
        )

    resolved = Path(candidate).resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved

    allowed_list = ", ".join(str(r) for r in roots)
    raise PathNotAllowedError(f"{label} ({resolved}) izin verilen dizinlerin dışında. İzinli kök dizinler: {allowed_list}")
