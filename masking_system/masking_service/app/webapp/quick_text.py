"""Dışarı Çıkar ve Geri Dönüştür ekranlarının ortak "Hızlı Metin / Kod"
modu yardımcıları: yapıştırılan metin, seçilen uzantıyla tek dosyalık bir
yükleme olarak backend'e gider; sonuç iki sütunda yan yana gösterilir."""

from __future__ import annotations

import json
import re
from pathlib import Path

from defusedxml import ElementTree

# streamlit: yan yana karsilastirma sutunlari ve kod bloklari icin.
import streamlit as st


# Yapistirilan icerik bu uzantiyla tek dosya olarak gonderilir; uzanti
# backend'in dosya turune ozgu tarama kurallarini secer. Varsayilan secim
# "Otomatik algila"dir (bkz. detect_quick_text_type); liste aranabilir.
QUICK_TEXT_TYPES = {
    # Metin ve veri
    "Düz metin (.txt)": ".txt",
    "Log (.log)": ".log",
    "Markdown (.md)": ".md",
    "CSV (.csv)": ".csv",
    "TSV (.tsv)": ".tsv",
    # Yapilandirma
    "Properties (.properties)": ".properties",
    "YAML (.yml)": ".yml",
    "YAML (.yaml)": ".yaml",
    "JSON (.json)": ".json",
    "XML (.xml)": ".xml",
    "TOML (.toml)": ".toml",
    "INI (.ini)": ".ini",
    "Config (.conf)": ".conf",
    "Config (.cfg)": ".cfg",
    "Ortam değişkenleri (.env)": ".env",
    "Gradle (.gradle)": ".gradle",
    "SQL (.sql)": ".sql",
    "HTML (.html)": ".html",
    # Programlama dilleri
    "Java (.java)": ".java",
    "Kotlin (.kt)": ".kt",
    "Groovy (.groovy)": ".groovy",
    "Scala (.scala)": ".scala",
    "C# (.cs)": ".cs",
    "C (.c)": ".c",
    "C/C++ başlık (.h)": ".h",
    "C++ (.cpp)": ".cpp",
    "C++ başlık (.hpp)": ".hpp",
    "Go (.go)": ".go",
    "Rust (.rs)": ".rs",
    "Python (.py)": ".py",
    "JavaScript (.js)": ".js",
    "TypeScript (.ts)": ".ts",
    "React JSX (.jsx)": ".jsx",
    "React TSX (.tsx)": ".tsx",
    "PHP (.php)": ".php",
    "Ruby (.rb)": ".rb",
    "Swift (.swift)": ".swift",
    "Dart (.dart)": ".dart",
    "Lua (.lua)": ".lua",
    "Perl (.pl)": ".pl",
    "Ada gövde (.adb)": ".adb",
    "Ada belirtim (.ads)": ".ads",
    "VHDL (.vhd)": ".vhd",
    "Verilog (.v)": ".v",
    "Shell (.sh)": ".sh",
    "PowerShell (.ps1)": ".ps1",
    "Batch (.bat)": ".bat",
}

_CODE_LANGUAGES = {
    ".properties": "properties", ".java": "java", ".sql": "sql", ".json": "json",
    ".xml": "xml", ".yml": "yaml", ".yaml": "yaml", ".py": "python", ".cs": "csharp",
    ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".ini": "ini",
    ".jsx": "jsx", ".tsx": "tsx", ".kt": "kotlin", ".groovy": "groovy", ".gradle": "groovy",
    ".scala": "scala", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".go": "go",
    ".rs": "rust", ".php": "php", ".rb": "ruby", ".swift": "swift", ".dart": "dart",
    ".lua": "lua", ".pl": "perl", ".adb": "ada", ".ads": "ada", ".vhd": "vhdl",
    ".v": "verilog", ".ps1": "powershell", ".bat": "batch", ".toml": "toml",
    ".html": "html", ".md": "markdown", ".conf": "ini", ".cfg": "ini", ".env": "bash",
}

AUTO_DETECT_LABEL = "🔍 Otomatik algıla"
_LABEL_BY_EXTENSION = {extension: label for label, extension in QUICK_TEXT_TYPES.items()}


# Dosya turu secimi: varsayilan "Otomatik algila"; liste aranabilir.
def render_quick_text_type_select(*, key: str, help: str) -> str:
    return st.selectbox(
        "Dosya türü",
        [AUTO_DETECT_LABEL, *QUICK_TEXT_TYPES],
        help=help + " Otomatik algılama yanlış türü bulursa listeden doğrusunu seçip tekrar çalıştırın.",
        key=key,
    )


# Secimi uzantiya cevirir. Doner: (uzanti, otomatik_algilandi_mi).
def resolve_quick_text_type(choice: str, text: str) -> tuple[str, bool]:
    if choice in QUICK_TEXT_TYPES:
        return QUICK_TEXT_TYPES[choice], False
    return detect_quick_text_type(text), True


# Kullaniciya gosterilecek tur adi ("JSON (.json)").
def quick_text_type_label(extension: str) -> str:
    return _LABEL_BY_EXTENSION.get(extension, extension)


_SHEBANGS = (("python", ".py"), ("pwsh", ".ps1"), ("powershell", ".ps1"), ("sh", ".sh"))
_SQL_RE = re.compile(
    r"^\s*(select|insert\s+into|update|delete\s+from|create\s+(or\s+replace\s+)?(table|view|index|procedure|function|package|trigger|sequence)"
    r"|alter\s+table|drop\s+(table|view|index)|merge\s+into|grant|with\s+\w+\s+as\s*\()\b", re.I | re.M)
_LOG_RE = re.compile(
    r"^\s*(\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}|\[?(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|FATAL)\]?[\s:]"
    r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")
_KEY_VALUE_RE = re.compile(r"^\s*(export\s+)?[A-Za-z_][\w.\-]*\s*=")
_YAML_RE = re.compile(r"^\s*(-\s+\S|[\w.\-\"']+\s*:(\s|$))")
_INI_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
# (desen, uzanti) - siralama onemli: daha ayirt edici olan once gelir.
_CODE_SIGNATURES = (
    (re.compile(r"^\s*#include\s*[<\"]", re.M), ".cpp"),
    (re.compile(r"^\s*using\s+System|^\s*namespace\s+[\w.]+|\{\s*get;\s*(set;)?\s*\}|\bConsole\.Write", re.M), ".cs"),
    (re.compile(r"^\s*package\s+[\w.]+\s*;", re.M), ".java"),
    (re.compile(r"^\s*import\s+java\.|^\s*(public|private|protected)\s+(static\s+)?(final\s+)?(class|interface|enum)\s", re.M), ".java"),
    (re.compile(r"^\s*@(Override|Autowired|Service|Component|Test|Bean)\b"
                r"|^\s*(public|private|protected)\s+(static\s+)?(final\s+)?[\w<>\[\],.]+\s+\w+\s*\(", re.M), ".java"),
    (re.compile(r"^\s*package\s+\w+\s*$.*^\s*func\s", re.M | re.S), ".go"),
    (re.compile(r"^\s*(pub\s+)?fn\s+\w+.*->|^\s*let\s+mut\s|^\s*use\s+\w+::", re.M), ".rs"),
    (re.compile(r"^\s*fun\s+\w+\s*\(|^\s*val\s+\w+\s*(:\s*\w+)?\s*=|^\s*data\s+class\s", re.M), ".kt"),
    (re.compile(r"^\s*(with|use)\s+[\w.]+\s*;.*^\s*(procedure|function|package)\s", re.M | re.S | re.I), ".adb"),
    (re.compile(r"^\s*(entity|architecture)\s+\w+\s+(is|of)\b", re.M | re.I), ".vhd"),
    (re.compile(r"^\s*module\s+\w+\s*[(#;]", re.M), ".v"),
    (re.compile(r"<\?php"), ".php"),
    (re.compile(r"^\s*(def\s+\w+\s*\(.*\)\s*(->\s*[^:]+)?:|class\s+\w+(\(.*\))?:|from\s+[\w.]+\s+import\s|import\s+\w+\s*$)"
                r"|^\s*(for|while|if|elif|with|try|except)\b[^;{]*:\s*$", re.M), ".py"),
    (re.compile(r"^\s*(export\s+)?(interface|type)\s+\w+.*[={]|:\s*(string|number|boolean)\b", re.M), ".ts"),
    (re.compile(r"^\s*(const|let|var)\s+\w+\s*=|function\s+\w+\s*\(|=>\s*[{(]|require\(|module\.exports", re.M), ".js"),
    (re.compile(r"^\s*(param\s*\(|\$\w+\s*=|Get-\w+|Set-\w+)", re.M | re.I), ".ps1"),
    (re.compile(r"^\s*(echo|export|if\s+\[|for\s+\w+\s+in|fi\s*$|done\s*$)", re.M), ".sh"),
)


def _share(lines: list[str], pattern: re.Pattern) -> float:
    return sum(1 for line in lines if pattern.match(line)) / len(lines) if lines else 0.0


# ANAHTAR=deger metni: anahtarlarin hepsi BUYUK_HARF ise .env, degilse .properties.
def _key_value_type(lines: list[str]) -> str:
    uppercase_keys = all(re.match(r"\s*(export\s+)?[A-Z_][A-Z0-9_]*\s*=", line)
                         for line in lines if _KEY_VALUE_RE.match(line))
    return ".env" if uppercase_keys else ".properties"


# Yapistirilan metne bakip dosya turunu tahmin eder. Emin olunamazsa .txt
# doner: maskeleme yine yapilir, yalnizca ture ozgu sozdizimi kontrolu atlanir.
def detect_quick_text_type(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ".txt"
    first_line = stripped.splitlines()[0]
    if first_line.startswith("#!"):
        return next((ext for word, ext in _SHEBANGS if word in first_line), ".sh")
    if stripped[0] in "{[":
        try:
            json.loads(stripped)
            return ".json"
        except ValueError:
            pass
    if stripped.startswith("<"):
        lowered = stripped[:200].lower()
        if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
            return ".html"
        if lowered.startswith("<?php"):
            return ".php"
        try:
            ElementTree.fromstring(stripped)
            return ".xml"
        except (ElementTree.ParseError, ValueError):
            if lowered.startswith("<?xml"):
                return ".xml"
    lines = [line for line in stripped.splitlines()
             if line.strip() and not line.lstrip().startswith(("#", "//", "--", ";", "!"))]
    if _SQL_RE.search(stripped):
        return ".sql"
    # Neredeyse tum satirlari ANAHTAR=deger olan metin yapilandirmadir; "export"
    # satirlari onu kabuk betigi yapmaz.
    key_value_share = _share(lines, _KEY_VALUE_RE)
    if key_value_share >= 0.8 and not any(_INI_SECTION_RE.match(line) for line in lines):
        return _key_value_type(lines)
    for pattern, extension in _CODE_SIGNATURES:
        if pattern.search(stripped):
            return extension
    if _share(lines, _LOG_RE) >= 0.5:
        return ".log"
    if any(_INI_SECTION_RE.match(line) for line in lines) and _share(lines, _KEY_VALUE_RE) >= 0.5:
        return ".ini"
    if key_value_share >= 0.7:
        return _key_value_type(lines)
    if stripped.startswith("---") or _share(lines, _YAML_RE) >= 0.7:
        # Girintisiz, noktali anahtarli "a.b: deger" satirlari .properties yazimidir.
        flat_dotted = all(not line[:1].isspace() and re.match(r"[\w-]+(\.[\w-]+)+\s*:", line) for line in lines)
        return ".properties" if flat_dotted else ".yml"
    if len(lines) >= 2:
        for delimiter, extension in (("\t", ".tsv"), (",", ".csv"), (";", ".csv")):
            counts = {line.count(delimiter) for line in lines}
            if len(counts) == 1 and counts.pop() >= 1:
                return extension
    return ".txt"


# api_client'in yukleme fonksiyonlarinin bekledigi (name + getvalue())
# arayuzune uyan, bellekteki metin icin UploadedFile yerine gecen nesne.
class InMemoryUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


# Yapistirilan metnin gonderilecegi dosya adi ("hizli_metin.properties").
def quick_text_file_name(extension: str) -> str:
    return f"hizli_metin{extension}"


# Dosya uzantisina gore st.code icin sozdizimi vurgulama dilini secer.
def code_language(name: str) -> str:
    return _CODE_LANGUAGES.get(Path(name).suffix.lower(), "text")


# Karsilastirma kutularinin sabit yuksekligi (piksel): uzun metin sayfayi
# uzatmaz, kutu icinde kaydirilir.
COMPARE_HEIGHT = 420


def _read_only_pane(name: str, title: str, text: str) -> None:
    st.markdown(f"**{title}**")
    st.code(text, language=code_language(name), height=COMPARE_HEIGHT, wrap_lines=True)


# Iki metni basliklariyla yan yana, sabit yukseklikte kod blogu olarak
# gosterir (st.code'un kopyalama dugmesi sonucu dogrudan panoya almayi saglar).
def render_side_by_side(name: str, left: tuple[str, str], right: tuple[str, str]) -> None:
    for column, (title, text) in zip(st.columns(2), (left, right)):
        with column:
            _read_only_pane(name, title, text)


# Soldaki metni salt okunur, sagdakini duzenlenebilir gosterir ve sagdaki
# metnin guncel (duzenlenmis) halini dondurur. Duzenleme `key` altinda
# session_state'te saklanir; ayni sonuc yeniden cizildiginde korunur.
def render_editable_side_by_side(name: str, left: tuple[str, str], right: tuple[str, str], *, key: str) -> str:
    if key not in st.session_state:
        st.session_state[key] = right[1]
    # Duzenlenebilir kutu da soldaki kod blogu gibi es aralikli yazilir;
    # satirlar iki tarafta ayni yerden kirilir, karsilastirma kolaylasir.
    st.html(
        f"<style>.st-key-{key} textarea {{font-family: 'Source Code Pro', monospace; "
        "font-size: 14px; line-height: 1.6;}}</style>"
    )
    left_column, right_column = st.columns(2)
    with left_column:
        _read_only_pane(name, *left)
    with right_column:
        st.markdown(f"**{right[0]}**")
        return st.text_area(right[0], key=key, height=COMPARE_HEIGHT, label_visibility="collapsed")
