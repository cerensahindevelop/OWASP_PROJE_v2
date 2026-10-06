"""Dışarı Çıkar ve Geri Dönüştür ekranlarının ortak "Hızlı Metin / Kod"
modu yardımcıları: yapıştırılan metin, seçilen uzantıyla tek dosyalık bir
yükleme olarak backend'e gider; sonuç iki sütunda yan yana gösterilir."""

from __future__ import annotations

from pathlib import Path

# streamlit: yan yana karsilastirma sutunlari ve kod bloklari icin.
import streamlit as st

# Yapistirilan icerik bu uzantiyla tek dosya olarak gonderilir; uzanti
# backend'in dosya turune ozgu tarama kurallarini secer.
QUICK_TEXT_TYPES = {
    "Düz metin (.txt)": ".txt",
    "Properties (.properties)": ".properties",
    "Java (.java)": ".java",
    "SQL (.sql)": ".sql",
    "JSON (.json)": ".json",
    "XML (.xml)": ".xml",
    "YAML (.yml)": ".yml",
    "Python (.py)": ".py",
    "C# (.cs)": ".cs",
    "JavaScript (.js)": ".js",
}

_CODE_LANGUAGES = {
    ".properties": "properties", ".java": "java", ".sql": "sql", ".json": "json",
    ".xml": "xml", ".yml": "yaml", ".yaml": "yaml", ".py": "python", ".cs": "csharp",
    ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".ini": "ini",
}


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


# Iki metni basliklariyla yan yana kod blogu olarak gosterir (st.code'un
# kopyalama dugmesi sonucu dogrudan panoya almayi saglar).
def render_side_by_side(name: str, left: tuple[str, str], right: tuple[str, str]) -> None:
    language = code_language(name)
    for column, (title, text) in zip(st.columns(2), (left, right)):
        with column:
            st.markdown(f"**{title}**")
            st.code(text, language=language)
