"""Export sonucundaki teknik kural dagilimini insan-okunur hale getirir.

Backend raporu denetlenebilirlik icin gercek ``rule_name`` degerlerini
tasir. Streamlit ekrani ise bu adlari (ozellikle kurumsal terimlerin hash'li
kural adlarini) son kullaniciya gostermemeli; ayni Baslik / Proje Adi altinda
gruplayip anlamli veri turleri olarak sunmalidir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


_CORPORATE_RULE_RE = re.compile(r"^kurumsal_terim_(?P<title>.+)_[0-9a-f]{12}$")

_RULE_LABELS = {
    "project_name": "Proje adı",
    "sicil_no": "Sicil numarası",
    "branch_name": "Branch adı",
    "personnel_no": "Personel numarası",
    "contextual_personnel_id": "Personel / sicil bilgisi (bağlamsal)",
    "tc_kimlik_no": "T.C. kimlik numarası",
    "person_name": "Kişi adı",
    "email_address": "E-posta adresi",
    "ipv4_address": "IP adresi",
    "aws_access_key": "AWS erişim anahtarı",
    "github_token": "GitHub erişim tokenı",
    "google_api_key": "Google API anahtarı",
    "http_basic_auth_credentials": "HTTP Basic Auth kullanıcı bilgisi",
    "jwt_token": "JWT tokenı",
    "private_key_block": "Özel anahtar bloğu",
    "slack_token": "Slack tokenı",
    "stripe_api_key": "Stripe API anahtarı",
    "generic_secret_assignment": "Genel gizli bilgi (API anahtarı / parola / token)",
    "xml_element_secret": "XML içindeki gizli bilgi",
    "uuid": "UUID",
    "unc_network_path": "Ağ paylaşım yolu (UNC)",
    "presidio_credentials_file_reference": "Kimlik bilgisi dosya referansı",
    "presidio_dotenv_reference": ".env gizli bilgi referansı",
    "presidio_properties_config_reference": "Properties gizli bilgi referansı",
    "presidio_yaml_secrets_reference": "YAML gizli bilgi referansı",
    "presidio_internal_domain_name": "Kurum içi alan adı",
    "presidio_internal_prod_hostname": "Kurum içi sunucu adı",
    "llm_dynamic": "Yapay zekâ ile tespit edilen hassas bilgi",
}

_TOKEN_LABELS = {
    "api": "API",
    "aws": "AWS",
    "github": "GitHub",
    "http": "HTTP",
    "ip": "IP",
    "ipv4": "IPv4",
    "jwt": "JWT",
    "llm": "Yapay zekâ",
    "sql": "SQL",
    "tc": "T.C.",
    "unc": "UNC",
    "url": "URL",
    "uuid": "UUID",
    "xml": "XML",
}


@dataclass(frozen=True)
class SensitiveBreakdownRow:
    """UI tablosundaki tek, birlestirilmis hassas veri turu satiri."""

    data_type: str
    title: str
    distinct_terms: int | None
    finding_count: int

    def as_table_row(self) -> dict[str, str | int | None]:
        return {
            "Hassas veri türü": self.data_type,
            "Başlık / Proje Adı": self.title,
            "Farklı terim": self.distinct_terms,
            "Maskelenen bulgu": self.finding_count,
        }


def _humanize_identifier(value: str) -> str:
    """Teknik snake_case bir adi okunabilir etikete cevirir."""

    tokens = [token for token in re.split(r"[_\-:]+", value) if token]
    if not tokens:
        return "Diğer hassas bilgi"
    words = [_TOKEN_LABELS.get(token.casefold(), token) for token in tokens]
    label = " ".join(words)
    return label[0].upper() + label[1:]


def _humanize_title(value: str) -> str:
    """Normalize edilmis Baslik / Proje Adi slug'ini baslik biciminde gosterir."""

    tokens = [token for token in re.split(r"[_\-:]+", value) if token]
    if not tokens:
        return "Belirtilmemiş"
    return " ".join(_TOKEN_LABELS.get(token.casefold(), token.capitalize()) for token in tokens)


def display_sensitive_rule_name(rule_name: str) -> str:
    """Tek bir teknik rule_name'i hash gostermeden insan-okunur yapar."""

    corporate_match = _CORPORATE_RULE_RE.fullmatch(rule_name)
    if corporate_match is not None:
        return f"Kurumsal sözlük — {_humanize_title(corporate_match.group('title'))}"
    if rule_name.startswith("llm:"):
        entity_type = rule_name.partition(":")[2]
        return f"Yapay zekâ — {_humanize_identifier(entity_type)}"
    return _RULE_LABELS.get(rule_name, _humanize_identifier(rule_name))


def summarize_sensitive_breakdown(matches_by_rule: Mapping[str, int]) -> list[SensitiveBreakdownRow]:
    """Hash'li kural satirlarini guvenli, anlasilir UI gruplarina indirger.

    Her kurumsal kural tek bir sozluk terimini temsil ettigi icin ayni baslik
    altindaki kural sayisi ``distinct_terms`` olarak gosterilebilir. Standart
    ve LLM kurallarinda rapor benzersiz deger sayisini tasimadigindan bu alan
    bilerek bos birakilir; occurrence sayisiymis gibi yanlis sunulmaz.
    """

    corporate: dict[str, tuple[int, int]] = {}
    standard: dict[str, int] = {}

    for rule_name, raw_count in matches_by_rule.items():
        count = int(raw_count)
        if count <= 0:
            continue
        match = _CORPORATE_RULE_RE.fullmatch(rule_name)
        if match is not None:
            title_slug = match.group("title")
            term_count, finding_count = corporate.get(title_slug, (0, 0))
            corporate[title_slug] = (term_count + 1, finding_count + count)
            continue

        label = display_sensitive_rule_name(rule_name)
        standard[label] = standard.get(label, 0) + count

    rows = [
        SensitiveBreakdownRow(
            data_type="Kurumsal ifade",
            title=_humanize_title(title_slug),
            distinct_terms=term_count,
            finding_count=finding_count,
        )
        for title_slug, (term_count, finding_count) in corporate.items()
    ]
    rows.extend(
        SensitiveBreakdownRow(
            data_type=label,
            title="—",
            distinct_terms=None,
            finding_count=count,
        )
        for label, count in standard.items()
    )
    return sorted(rows, key=lambda row: (-row.finding_count, row.data_type, row.title))
