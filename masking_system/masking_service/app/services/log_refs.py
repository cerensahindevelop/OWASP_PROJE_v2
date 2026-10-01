"""Log ve rapordaki dosya etiketi: maskeli yol + kisa dosya kimligi.

Kural 7 (docs/faz3-tasarim-notu.md, K1/7): log dosyalarina, export raporuna ve
ciktiya giren her seye kaynak (orijinal) yol yazilmaz. Yerine maskeli goreli
yol ile `#` sonrasinda 12 haneli bir kimlik yazilir. Kimlik, job anahtarli
HMAC'tir (core.crypto.hash_value, SECURITY_ENCRYPTION_KEY): anahtar sunucudan
cikmadigi icin kisa yollar tahmin edilerek geri bulunamaz; sunucuda
`python -m app.cli dosya-kimligi` ile DB kaydina (AuditLog/AuditWarning)
eslestirilir. DB orijinal yolla calismaya devam eder (S2).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePath

from app.core.crypto import hash_value

FILE_REF_CHARS = 12
UNKNOWN_FILE_LABEL = "<dosya?>"

_file_label: ContextVar[str | None] = ContextVar("log_file_label", default=None)


def file_ref(context_id: int, run_id: int, relative_path: str | PurePath) -> str:
    posix = PurePath(relative_path).as_posix()
    return hash_value(context_id, f"file-ref-v1:{run_id}:{posix}")[:FILE_REF_CHARS]


def file_label(masked_relative_path: str | Path | None, ref: str) -> str:
    masked = Path(masked_relative_path).as_posix() if masked_relative_path else "?"
    return f"{masked}#{ref}"


@contextmanager
def log_file_label(label: str | None):
    token = _file_label.set(label)
    try:
        yield
    finally:
        _file_label.reset(token)


# Su an islenen dosyanin log etiketi; baglam yoksa orijinal yol yerine sabit metin.
def current_file_label() -> str:
    return _file_label.get() or UNKNOWN_FILE_LABEL
