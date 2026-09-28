import hashlib
import hmac

# cryptography.fernet: hassas degerleri (IP, e-posta, secret vb.) simetrik
# olarak sifreleyip cozen kutuphane - geri donusturulebilir maskeleme burada dayanir.
from cryptography.fernet import Fernet

from app.core.config import settings

# .env'deki SECURITY_ENCRYPTION_KEY ile olusturulan, tum sifreleme/cozme
# islemlerinde kullanilan tekil (module-level) Fernet nesnesi.
_fernet = Fernet(settings.encryption_key.encode())


# Verilen duz metni (orijinal hassas degeri) Fernet ile sifreleyip DB'de saklanacak hale getirir.
def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


# DB'de sakli sifreli degeri cozup orijinal duz metni geri dondurur.
def decrypt_value(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# Context + deger uzerinden HMAC-SHA256 hash uretir; sifre cozmeden ayni degerin
# daha once kaydedilip kaydedilmedigini (tekillik/dedup) kontrol etmek icin kullanilir.
def hash_value(context_id: int, plaintext: str) -> str:
    """HMAC-SHA256 over (context_id, plaintext), used as a lookup key so
    uniqueness/dedup can be checked without decrypting stored values."""
    key = settings.encryption_key.encode()
    msg = f"{context_id}:{plaintext}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()
