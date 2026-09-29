"""Bagimlilik lock dosyalari icin dar izin listesi ve ic registry URL maskelemesi.

Lock dosyalari SCAN_ONLY'dir (bkz. exporter._finalize_scan_only): bugune kadar
tek bir bulgu dosyanin tamamini onaya gonderiyordu. Bu modul iki dar kural
ekler:

1. Izin listesi: paket ozetleri (`sha512-<base64>`, `sha256:<hex>` bicimi ya da
   checksum/shasum/hash/integrity anahtarina bagli onaltilik ozet) ve TAM host
   adi izin listesindeki public registry URL'leri. Kimlik bilgisi iceren URL
   izin listesine girmez. Sozluk kaynakli bulgular (kurumsal terim, ogrenilmis
   hassas deger) ASLA izin listesiyle dusurulmez.
2. Kalan her bulgu ic (izin listesi disi) bir registry URL'sinin icindeyse,
   parser'i olan (JSON/YAML/TOML) lock dosyalarinda dosyadaki TUM ic URL'ler
   (host + yol) maskelenir. Sinir dogrulamasi string icindeki bulguyu tum
   string'e genislettigi icin hangi parcanin hassas oldugu bilinemez; URL'nin
   tamami maskelenir. Kimlik bilgisi iceren ic URL ya da URL disinda tek bir
   bulgu kalirsa dosya eskisi gibi onaya gider.

Bu modul yalnizca araliklarla calisir; deger loglamaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# Algoritma onekli ozet: bicimin kendisi anlamini tasir (SRI / pip / npm).
_PREFIXED_DIGEST_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sha1|sha224|sha256|sha384|sha512)[-:][A-Za-z0-9+/=]{16,}(?![A-Za-z0-9+/=])",
    re.IGNORECASE,
)
# Oneksiz onaltilik ozet yalnizca ayni satirdaki ozet anahtarina bagliysa.
_KEYED_HEX_RE = re.compile(
    r"""["']?(?:checksum|shasum|hash|integrity)["']?\s*[:=]\s*["']?(?P<value>[0-9a-fA-F]{32,128})(?![0-9A-Za-z])""",
    re.IGNORECASE,
)
# `registry+https://...` / `sparse+https://...` (Cargo) onekleri de URL sayilir.
_URL_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9.-]*\+)?https?://(?P<authority>[^/\s\"'<>?#]+)[^\s\"'<>]*",
    re.IGNORECASE,
)
_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")

# Parser'i olan lock dosyalari: maskeleme sonrasi gecerlilik dogrulanabilir.
_STRUCTURED_FORMATS = {
    "package-lock.json": "json",
    "npm-shrinkwrap.json": "json",
    "pipfile.lock": "json",
    "composer.lock": "json",
    "pnpm-lock.yaml": "yaml",
    "poetry.lock": "toml",
    "cargo.lock": "toml",
}


def structured_format(filename: str) -> str | None:
    return _STRUCTURED_FORMATS.get(filename.rsplit("/", 1)[-1].lower())


@dataclass(frozen=True)
class _Url:
    start: int
    end: int
    public: bool
    has_userinfo: bool


def _urls(text: str, public_hosts: tuple[str, ...]) -> list[_Url]:
    urls: list[_Url] = []
    for match in _URL_RE.finditer(text):
        authority = match.group("authority")
        has_userinfo = "@" in authority
        host_offset = authority.rfind("@") + 1
        host_match = _HOST_RE.match(authority, host_offset)
        if host_match is None:
            continue
        host = host_match.group(0).lower()
        urls.append(_Url(
            start=match.start(), end=match.end(),
            public=host in public_hosts and not has_userinfo, has_userinfo=has_userinfo,
        ))
    return urls


def _allowlisted_spans(text: str, urls: list[_Url]) -> list[tuple[int, int]]:
    spans = [m.span() for m in _PREFIXED_DIGEST_RE.finditer(text)]
    spans += [m.span("value") for m in _KEYED_HEX_RE.finditer(text)]
    spans += [(url.start, url.end) for url in urls if url.public]
    return spans


def _within(start: int, end: int, spans) -> bool:
    return any(a <= start and end <= b for a, b in spans)


@dataclass(frozen=True)
class LockfilePlan:
    # Izin listesinden sonra kalan bulgular: (start, end).
    remaining: list[tuple[int, int]] = field(default_factory=list)
    allowlisted: int = 0
    # Kalan TUM bulgular ic URL'lerdeyse maskelenecek URL araliklari; aksi halde None.
    url_spans: list[tuple[int, int]] | None = None


def plan_lockfile(
    text: str, filename: str, findings: list[tuple[int, int, bool]], public_hosts: tuple[str, ...],
) -> LockfilePlan:
    """findings: (start, end, sozluk_kaynakli_mi). Sozluk bulgusu izin listesinden gecmez."""
    urls = _urls(text, public_hosts)
    allowlist = _allowlisted_spans(text, urls)
    remaining: list[tuple[int, int]] = []
    allowlisted = 0
    for start, end, from_dictionary in findings:
        if not from_dictionary and _within(start, end, allowlist):
            allowlisted += 1
        else:
            remaining.append((start, end))
    if not remaining or structured_format(filename) is None:
        return LockfilePlan(remaining, allowlisted, None)

    internal = [url for url in urls if not url.public]
    # Kimlik bilgisi tasiyan ic URL: lock dosyasina yazilmis bir parola insan
    # tarafindan gorulmeli, otomatik maskelenip gecilmez.
    if any(url.has_userinfo for url in internal):
        return LockfilePlan(remaining, allowlisted, None)
    url_spans = [(url.start, url.end) for url in internal]
    if not all(_within(start, end, url_spans) for start, end in remaining):
        return LockfilePlan(remaining, allowlisted, None)
    return LockfilePlan(remaining, allowlisted, url_spans)
