"""Sabit, tekrar uretilebilir yuk testi projeleri (kucuk / orta / buyuk).

Tum degerler SENTETIKTIR: IP'ler RFC 5737 dokumantasyon bloklarindan,
alan adlari .ornek.local / .example altindan, kisiler rastgele ad-soyad
kombinasyonlarindan, anahtarlar sabit tohumlu rastgele dizilerden uretilir.
Ayni tohum her calistirmada bayt-bayt ayni dosyalari uretir; manifest.json
her dosyanin sha256'sini ve toplam bayti kaydeder.

    loadtest/.venv/bin/python -m loadtest.datasets --out loadtest/data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import string
from pathlib import Path

SEED = 20261006

FIRST = ["Ayse", "Mehmet", "Zeynep", "Mustafa", "Elif", "Ahmet", "Fatma", "Emre", "Selin", "Burak",
         "Derya", "Kerem", "Gizem", "Onur", "Ceren", "Tolga", "Ebru", "Serkan", "Melis", "Hakan"]
LAST = ["Yilmazer", "Kaplanoglu", "Demirkiran", "Ozturkmen", "Aydinlioglu", "Celikbas", "Sahinkaya",
        "Korkmazer", "Erdemli", "Arslanbey", "Dogancay", "Kilicaslan", "Aksoyhan", "Polatkan", "Gunduzer"]
CITIES = ["Ankara", "Izmir", "Bursa", "Konya", "Eskisehir", "Kayseri", "Samsun", "Trabzon"]
SERVICES = ["tahsilat", "abonelik", "faturalama", "musteri", "raporlama", "bildirim", "kimlik", "envanter"]


class Gen:
    def __init__(self, rng: random.Random) -> None:
        self.r = rng

    def person(self) -> str:
        return f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"

    def email(self, person: str | None = None) -> str:
        person = person or self.person()
        first, last = person.lower().split()
        return f"{first}.{last}@kurum-ornek.com.tr"

    def ip(self) -> str:
        block = self.r.choice(["192.0.2", "198.51.100", "203.0.113"])
        return f"{block}.{self.r.randint(2, 250)}"

    def host(self) -> str:
        return f"{self.r.choice(SERVICES)}-{self.r.choice(['db', 'app', 'mq', 'cache'])}{self.r.randint(1, 9)}.intra.ornek.local"

    def secret(self, n: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(self.r.choice(alphabet) for _ in range(n))

    def hexkey(self, n: int = 40) -> str:
        return "".join(self.r.choice("0123456789abcdef") for _ in range(n))

    def password(self) -> str:
        return f"{self.r.choice(['Kis', 'Yaz', 'Bahar', 'Guz'])}{self.r.randint(2019, 2026)}!{self.secret(5)}"

    def tckn(self) -> str:
        digits = [self.r.randint(1, 9)] + [self.r.randint(0, 9) for _ in range(8)]
        d10 = ((sum(digits[0:9:2]) * 7) - sum(digits[1:8:2])) % 10
        digits.append(d10)
        digits.append(sum(digits) % 10)
        return "".join(map(str, digits))

    def phone(self) -> str:
        return f"+90 5{self.r.randint(30, 59)} {self.r.randint(100, 999)} {self.r.randint(10, 99)} {self.r.randint(10, 99)}"

    def iban(self) -> str:
        return "TR" + "".join(str(self.r.randint(0, 9)) for _ in range(24))

    def sicil(self) -> str:
        return f"S{self.r.randint(100000, 999999)}"

    def ident(self) -> str:
        return self.r.choice(["hesapla", "dogrula", "kaydet", "getir", "listele", "guncelle", "sil"]) + \
            self.r.choice(["Tutar", "Musteri", "Fatura", "Abone", "Rapor", "Kayit", "Oturum"])


# ---------------------------------------------------------------------------
# Dosya sablonlari. Her biri (gen, hedef_bayt) alir ve metin doner.
# ---------------------------------------------------------------------------

def py_service(g: Gen, target: int) -> str:
    owner = g.person()
    lines = [
        '"""Servis modulu."""',
        f"# Sorumlu: {owner} <{g.email(owner)}>",
        "import os",
        "import logging",
        "",
        f'DB_URL = "postgresql://svc_{g.r.choice(SERVICES)}:{g.password()}@{g.host()}:5432/ana"',
        f'API_TOKEN = "{g.secret(32)}"',
        f'FALLBACK_IP = "{g.ip()}"',
        "log = logging.getLogger(__name__)",
        "",
    ]
    while sum(len(x) + 1 for x in lines) < target:
        name = g.ident()
        lines += [
            f"def {name}(kayit, limit=10):",
            f'    """{g.r.choice(CITIES)} bolgesi icin {name} islemi."""',
            "    toplam = 0",
            "    for satir in kayit[:limit]:",
            "        toplam += satir.get('tutar', 0)",
            f"    log.info('islem tamamlandi kullanici=%s', '{g.email()}')" if g.r.random() < 0.3 else "    log.debug('ara toplam %s', toplam)",
            "    return toplam",
            "",
        ]
    return "\n".join(lines) + "\n"


def java_class(g: Gen, target: int) -> str:
    cls = g.r.choice(SERVICES).capitalize() + "Servisi"
    owner = g.person()
    lines = [
        "package tr.ornek.kurum.servis;",
        "",
        "import java.util.List;",
        "",
        f"/** Yazar: {owner} ({g.email(owner)}) */",
        f"public class {cls} {{",
        f'    private static final String JDBC = "jdbc:oracle:thin:@{g.host()}:1521/ORCL";',
        f'    private static final String DB_PASSWORD = "{g.password()}";',
        f'    private static final String SFTP_HOST = "{g.ip()}";',
        "",
    ]
    while sum(len(x) + 1 for x in lines) < target:
        name = g.ident()
        lines += [
            f"    public long {name}(List<Long> tutarlar) {{",
            "        long toplam = 0;",
            "        for (Long t : tutarlar) { toplam += t; }",
            f'        // TODO {g.person()} ile kontrol edilecek' if g.r.random() < 0.3 else "        // ara toplam",
            "        return toplam;",
            "    }",
            "",
        ]
    lines.append("}")
    return "\n".join(lines) + "\n"


def cs_class(g: Gen, target: int) -> str:
    lines = [
        "using System;",
        "namespace Ornek.Kurum.Abonelik",
        "{",
        "    public class AbonelikIstemcisi",
        "    {",
        f'        private const string ConnStr = @"Server={g.host()};Database=Abone;User Id=app_user;Password={g.password()};";',
        f'        private const string ApiKey = "{g.hexkey(32)}";',
        "",
    ]
    while sum(len(x) + 1 for x in lines) < target:
        name = g.ident()
        lines += [
            f"        public decimal {name.capitalize()}(decimal tutar)",
            "        {",
            "            var oran = 0.18m;",
            f'            Console.WriteLine("Bildirim: {g.email()}");' if g.r.random() < 0.3 else "            // hesaplama",
            "            return tutar * (1 + oran);",
            "        }",
            "",
        ]
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def js_module(g: Gen, target: int) -> str:
    lines = [
        "'use strict';",
        f"const API_BASE = 'https://{g.host()}/api/v2';",
        f"const CLIENT_SECRET = '{g.secret(40)}';",
        f"// destek: {g.email()} / {g.phone()}",
        "",
    ]
    while sum(len(x) + 1 for x in lines) < target:
        name = g.ident()
        lines += [
            f"export async function {name}(id) {{",
            "  const res = await fetch(`${API_BASE}/kayit/${id}`);",
            "  if (!res.ok) throw new Error('istek basarisiz');",
            "  return res.json();",
            "}",
            "",
        ]
    return "\n".join(lines) + "\n"


def properties(g: Gen, target: int) -> str:
    lines = ["# uygulama ayarlari"]
    while sum(len(x) + 1 for x in lines) < target:
        svc = g.r.choice(SERVICES)
        lines += [
            f"{svc}.datasource.url=jdbc:postgresql://{g.host()}:5432/{svc}",
            f"{svc}.datasource.username={svc}_user",
            f"{svc}.datasource.password={g.password()}",
            f"{svc}.smtp.host={g.ip()}",
            f"{svc}.bildirim.alici={g.email()}",
            f"{svc}.timeout.ms={g.r.randint(1000, 30000)}",
        ]
    return "\n".join(lines) + "\n"


def yaml_conf(g: Gen, target: int) -> str:
    lines = ["servisler:"]
    while sum(len(x) + 1 for x in lines) < target:
        svc = g.r.choice(SERVICES)
        lines += [
            f"  - ad: {svc}",
            f"    adres: {g.ip()}",
            f"    host: {g.host()}",
            f"    anahtar: \"{g.secret(28)}\"",
            f"    sorumlu: \"{g.person()}\"",
            f"    replika: {g.r.randint(1, 5)}",
        ]
    return "\n".join(lines) + "\n"


def sql_script(g: Gen, target: int) -> str:
    lines = ["-- ornek veri yukleme", "CREATE TABLE musteri (id INT, ad VARCHAR(80), tckn CHAR(11), eposta VARCHAR(120), telefon VARCHAR(20), iban VARCHAR(32));"]
    i = 1
    while sum(len(x) + 1 for x in lines) < target:
        p = g.person()
        lines.append(
            f"INSERT INTO musteri VALUES ({i}, '{p}', '{g.tckn()}', '{g.email(p)}', '{g.phone()}', '{g.iban()}');"
        )
        i += 1
    return "\n".join(lines) + "\n"


def markdown_doc(g: Gen, target: int) -> str:
    lines = ["# Kurulum Notlari", ""]
    while sum(len(x) + 1 for x in lines) < target:
        lines += [
            f"## {g.r.choice(SERVICES).capitalize()} servisi",
            f"Sunucu {g.host()} ({g.ip()}) uzerinde calisir. Erisim icin {g.person()} ile iletisime gecin.",
            f"Yonetici hesabi: `admin` / sifre `{g.password()}` (gecici).",
            f"Acil durumda {g.phone()} numarasini arayin.",
            "",
        ]
    return "\n".join(lines) + "\n"


def xml_conf(g: Gen, target: int) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<yapilandirma>"]
    while sum(len(x) + 1 for x in lines) < target:
        svc = g.r.choice(SERVICES)
        lines += [
            f'  <baglanti ad="{svc}">',
            f"    <sunucu>{g.host()}</sunucu>",
            f"    <kullanici>{svc}_svc</kullanici>",
            f"    <parola>{g.password()}</parola>",
            f"    <ip>{g.ip()}</ip>",
            "  </baglanti>",
        ]
    lines.append("</yapilandirma>")
    return "\n".join(lines) + "\n"


def app_log(g: Gen, target: int) -> str:
    lines = []
    sec = 0
    while sum(len(x) + 1 for x in lines) < target:
        sec += g.r.randint(1, 40)
        hh, mm, ss = 9 + sec // 3600, (sec // 60) % 60, sec % 60
        kind = g.r.random()
        if kind < 0.4:
            msg = f"INFO  giris basarili kullanici={g.email()} ip={g.ip()}"
        elif kind < 0.7:
            msg = f"WARN  yavas sorgu sure={g.r.randint(800, 9000)}ms host={g.host()}"
        else:
            msg = f"ERROR odeme reddedildi musteri={g.person()} iban={g.iban()}"
        lines.append(f"2026-09-{g.r.randint(10, 28):02d} {hh:02d}:{mm:02d}:{ss:02d} {msg}")
    return "\n".join(lines) + "\n"


def json_conf(g: Gen, target: int) -> str:
    items = []
    while len(json.dumps(items, indent=2)) < target:
        items.append({
            "servis": g.r.choice(SERVICES), "adres": g.ip(), "host": g.host(),
            "token": g.secret(36), "iletisim": g.email(),
        })
    return json.dumps({"kayitlar": items}, indent=2, ensure_ascii=False) + "\n"


TEMPLATES = {
    "py": ("app/{svc}/servis_{i}.py", py_service),
    "java": ("src/main/java/tr/ornek/kurum/{Svc}Servisi{i}.java", java_class),
    "cs": ("Abonelik/Istemci{i}.cs", cs_class),
    "js": ("web/src/{svc}_{i}.js", js_module),
    "properties": ("config/{svc}_{i}.properties", properties),
    "yml": ("deploy/{svc}_{i}.yml", yaml_conf),
    "sql": ("db/seed_{i}.sql", sql_script),
    "md": ("docs/kurulum_{i}.md", markdown_doc),
    "xml": ("config/baglanti_{i}.xml", xml_conf),
    "log": ("logs/uygulama_{i}.log", app_log),
    "json": ("config/servisler_{i}.json", json_conf),
}

# (sablon, hedef bayt) listeleri. 6000 karakteri asan dosyalar LLM'de
# birden fazla parcaya bolunur (VLLM_MAX_FILE_CHARS=6000).
PROFILES: dict[str, list[tuple[str, int]]] = {
    "small": [
        ("py", 1400), ("java", 1600), ("properties", 900), ("yml", 1000), ("md", 1200), ("js", 1100),
    ],
    "medium": [
        ("py", 2200), ("py", 1800), ("java", 2600), ("cs", 2000), ("js", 1600), ("properties", 1100),
        ("yml", 1200), ("xml", 1400), ("md", 1400), ("sql", 2400),
    ],
    "large": [
        ("py", 2600), ("py", 3400), ("py", 1800), ("java", 7400), ("java", 2800), ("java", 2200),
        ("cs", 3000), ("cs", 1900), ("js", 2400), ("js", 1700), ("properties", 1400), ("properties", 1000),
        ("yml", 1500), ("xml", 1800), ("json", 1600), ("md", 2000), ("md", 1300), ("sql", 3000),
        ("log", 2600), ("py", 6800),
    ],
}


def build(size: str, out_root: Path) -> dict:
    root = out_root / size
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    files = []
    for i, (kind, target) in enumerate(PROFILES[size]):
        rng = random.Random(f"{SEED}:{size}:{i}:{kind}")
        g = Gen(rng)
        svc = rng.choice(SERVICES)
        pattern, fn = TEMPLATES[kind]
        rel = pattern.format(svc=svc, Svc=svc.capitalize(), i=i)
        text = fn(g, target)
        data = text.encode("utf-8")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append({"path": rel, "bytes": len(data), "chars": len(text),
                      "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256("".join(f["sha256"] for f in files).encode()).hexdigest()
    return {"size": size, "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files),
            "files_over_6000_chars": sum(f["chars"] > 6000 for f in files),
            "dataset_sha256": digest, "files": files}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "data"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"seed": SEED, "datasets": {s: build(s, out) for s in PROFILES}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    for s, m in manifest["datasets"].items():
        print(f"{s:7s} dosya={m['file_count']:3d} bayt={m['total_bytes']:7d} >6000kar={m['files_over_6000_chars']} sha={m['dataset_sha256'][:12]}")


if __name__ == "__main__":
    main()
