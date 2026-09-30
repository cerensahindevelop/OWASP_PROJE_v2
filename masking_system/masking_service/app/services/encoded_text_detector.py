"""Kodlanmis (base64/hex/bayt dizisi) metnin icindeki hassas veri kontrolu.

`Password=` base64'te `UGFzc3dvcmQ9` olur: sozluk/regex, Presidio ve LLM
katmanlari kodlanmis metni oldugu gibi gordugu icin icindeki parola,
connection string, IP ya da kurum terimi maskelenmeden disari cikabilirdi.

Bu katman, cozuldugunde okunabilir metin veren bloklari (bkz.
encoded_blobs.find_encoded_text_blocks) Katman 1 (sozluk/regex/ogrenilmis
karar) ve Katman 2 (Presidio) ile ayrica tarar. Bulgu varsa dosya
KARANTINAYA alinir: deger kodlanmis blogun icinde maskelenmez, cunku yeniden
kodlanan metin geri donusta birebir ayni dosyayi uretme garantisini
karmasiklastirir. Karar insan onayina birakilir (fail-closed).

Guvenlik ilkesi: cozulmus metin, bulunan deger ya da kurum terimi basligi
hicbir mesaja/loga yazilmaz; yalnizca satir numarasi, kodlama turu ve genel
bulgu turleri raporlanir.
"""

from __future__ import annotations

import re

from app.services.detectors import DetectionOrchestrator, DetectionResult, DetectorOutput
from app.services.encoded_blobs import find_encoded_text_blocks
from app.services.placeholder_policy import is_corporate_rule

_CORPORATE_TERM_TYPE = "KURUMSAL_TERIM"
_CREDENTIAL_TYPE = "KIMLIK_BILGISI"

# Kodlanmis metin, kimlik bilgisi saklamanin yaygin bir yoludur ve cozulmus
# hali hicbir katmanda gorulmez. Bu yuzden yerel katmanlarin (orn. 12+
# karakter isteyen generic_secret_assignment) kacirabilecegi kimlik bilgisi
# sekilleri burada ayrica aranir; sonuc yalnizca karantinadir.
_CREDENTIAL_SHAPES = (
    # password=..., "sifre": "...", api_key: ... (deger uzunlugundan bagimsiz)
    re.compile(
        r"(?:password|passwd|pwd|parola|sifre|şifre|secret|token|api[_-]?key)[\w-]*[\"']?\s*[:=]\s*[\"']?[^\s\"';,]{3,}",
        re.IGNORECASE,
    ),
    # HTTP Basic kimlik ciftinin kendisi: "kullanici:parola" (tum metin; URL
    # "http://..." ve Windows yolu "C:\..." haric).
    re.compile(r"\A[^\s:]{2,64}:(?!//)[^\s:]{3,128}\Z"),
)


# Bulgunun raporda gorunecek genel turu; kurum sozlugu kategorileri
# (kullanicinin verdigi basliklar) genel bir ada indirgenir.
def _public_type(result: DetectionResult) -> str:
    if result.rule is not None and is_corporate_rule(result.rule.rule_name):
        return _CORPORATE_TERM_TYPE
    return result.tip


# Sozluk bulgulari kesindir; Presidio'nun dusuk guvenli tahminleri tek basina
# dosyayi karantinaya almaz.
def _counts(result: DetectionResult) -> bool:
    return result.kaynak_motor == "dictionary" or result.guven_seviyesi in ("yuksek", "orta")


class EncodedTextDetector:
    name = "encoded_text"

    # inner: yalnizca yerel katmanlari (sozluk + Presidio) iceren orchestrator.
    # LLM burada kullanilmaz: kisa cozulmus metin icin istek maliyeti yuksek,
    # yerel katmanlar parola/IP/terim desenlerini zaten kesin olarak bulur.
    def __init__(self, inner: DetectionOrchestrator) -> None:
        self.inner = inner

    async def detect(self, content: str, metadata: dict | None = None) -> DetectorOutput:
        file_path = (metadata or {}).get("file_path", "")
        leaks: list[str] = []
        for block in find_encoded_text_blocks(content):
            scanned = await self.inner.scan(block.decoded, {"file_path": file_path, "enable_llm": False})
            types = {_public_type(result) for result in scanned.results if _counts(result)}
            if any(shape.search(block.decoded) for shape in _CREDENTIAL_SHAPES):
                types.add(_CREDENTIAL_TYPE)
            types = sorted(types)
            if scanned.crashes:
                types.append("TARANAMADI")
            if types:
                line = content.count("\n", 0, block.start) + 1
                leaks.append(f"satir {line}: {block.kind} ile kodlanmis metinde {', '.join(types)}")
        return DetectorOutput(encoded_leaks=leaks)
