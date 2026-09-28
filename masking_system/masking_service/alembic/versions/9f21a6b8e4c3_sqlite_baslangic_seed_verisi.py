"""SQLite baslangic seed verisi

Revision ID: 9f21a6b8e4c3
Revises: 41a96747a9b4
Create Date: 2026-08-05 11:00:00.000000

Squash oncesi (~35 Postgres migration'i) sistemde biriken filtre_kurallari
(26 satir), haric_tutma_desenleri (7 satir) ve dosya_tipi_kategori_kisitlamasi
(40 dosya uzantisi x 8 Presidio kategorisi = 320 satir) verisinin, squash
ANINDA canli Postgres DB'sinden dogrudan okunmus BIREBIR kopyasi. Bu, tek
tek eski migration'lari (bazilari sonraki migration'larda UPDATE ile
duzeltilmis regex'ler icermekteydi) tekrar oynatmak yerine, o an
GERCEKTEN aktif olan nihai veriyi tek bir yerde toplar.

dosya_tipi_kategori_kisitlamasi icin 320 satirin tamami (extension, kategori)
ikilisinin kartezyen carpimi oldugu dogrulandi (istisnasiz her uzanti ayni
8 kategoriye sahip) - bu yuzden elle 320 satir yazmak yerine iki listenin
carpimi olarak uretilir.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f21a6b8e4c3"
down_revision: Union[str, None] = "41a96747a9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


filtre_kurallari_table = sa.table(
    "filtre_kurallari",
    sa.column("kural_adi", sa.String),
    sa.column("kategori", sa.String),
    sa.column("desen_tipi", sa.String),
    sa.column("regex_deseni", sa.Text),
    sa.column("regex_bayraklari", sa.String),
    sa.column("yer_tutucu_on_eki", sa.String),
    sa.column("oncelik", sa.Integer),
    sa.column("aktif_mi", sa.Boolean),
    sa.column("aciklama", sa.Text),
    sa.column("dogrulayici_adi", sa.String),
    sa.column("kaynak_katman", sa.String),
    sa.column("entity_tipi", sa.String),
    sa.column("guven_skoru", sa.Float),
    sa.column("allow_list_mi", sa.Boolean),
    sa.column("desen_sifreli_mi", sa.Boolean),
)

haric_tutma_desenleri_table = sa.table(
    "haric_tutma_desenleri",
    sa.column("desen_adi", sa.String),
    sa.column("glob_deseni", sa.String),
    sa.column("uygulanir", sa.String),
    sa.column("aktif_mi", sa.Boolean),
)

dosya_tipi_kategori_kisitlamasi_table = sa.table(
    "dosya_tipi_kategori_kisitlamasi",
    sa.column("dosya_uzantisi", sa.String),
    sa.column("izinli_kategori", sa.String),
    sa.column("aktif_mi", sa.Boolean),
)


SEED_FILTRE_KURALLARI = [
    dict(
        kural_adi="project_name", kategori="project_name", desen_tipi="parametric", regex_deseni=None,
        regex_bayraklari=None, yer_tutucu_on_eki="mask_proje_adi", oncelik=1, aktif_mi=True,
        aciklama="Proje adi - calisma zamaninda saglanan literal deger, whole-word eslesir.",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="personnel_no", kategori="personnel_no", desen_tipi="parametric", regex_deseni=None,
        regex_bayraklari=None, yer_tutucu_on_eki="mask_personel_no", oncelik=2, aktif_mi=True,
        aciklama="Personel numarasi - calisma zamaninda saglanan literal deger.",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="branch_name", kategori="branch_name", desen_tipi="parametric", regex_deseni=None,
        regex_bayraklari=None, yer_tutucu_on_eki="mask_branch", oncelik=3, aktif_mi=True,
        aciklama="Git branch adi - calisma zamaninda saglanan literal deger.",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="aws_access_key", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\bAKIA[0-9A-Z]{16}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_aws_key", oncelik=10, aktif_mi=True,
        aciklama="AWS access key ID formati.", dogrulayici_adi=None, kaynak_katman="katman1",
        entity_tipi=None, guven_skoru=0.85, allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="generic_secret_assignment", kategori="secret", desen_tipi="regex",
        regex_deseni=(
            r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd)"
            r"(?:_[A-Za-z0-9]+)*\b\s*[:=]\s*(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
        ),
        regex_bayraklari="i", yer_tutucu_on_eki="mask_secret", oncelik=20, aktif_mi=True,
        aciklama="api_key/secret/token/password = <deger> seklindeki genel atama kalibi.",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="email_address", kategori="email", desen_tipi="regex",
        regex_deseni=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_email", oncelik=30, aktif_mi=True,
        aciklama="E-posta adresi.", dogrulayici_adi=None, kaynak_katman="katman1",
        entity_tipi=None, guven_skoru=0.85, allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="ipv4_address", kategori="ip", desen_tipi="regex",
        regex_deseni=(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\b"
        ),
        regex_bayraklari=None, yer_tutucu_on_eki="mask_ip", oncelik=40, aktif_mi=True,
        aciklama="IPv4 adresi (0-255 oktet dogrulamali).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="tc_kimlik_no", kategori="tc_kimlik_no", desen_tipi="regex",
        regex_deseni=r"\b[1-9][0-9]{10}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_tc_kimlik", oncelik=50, aktif_mi=True,
        aciklama="TC kimlik numarasi (11 hane, resmi checksum ile dogrulanir).",
        dogrulayici_adi="tc_kimlik_no", kaynak_katman="katman1", entity_tipi=None,
        guven_skoru=0.85, allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="person_name", kategori="person_name", desen_tipi="llm", regex_deseni=None,
        regex_bayraklari=None, yer_tutucu_on_eki="mask_kisi_adi", oncelik=90, aktif_mi=True,
        aciklama=(
            "Kişi adı - metinde geçen gerçek, spesifik bir insanın ad soyadı; genel/ortak "
            "kelimeler, unvanlar veya şirket adları değil."
        ),
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="allow_rfc5737_192_0_2", kategori="rfc5737_test_ip", desen_tipi="presidio",
        regex_deseni=r"\b192\.0\.2\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_rfc5737_ip", oncelik=200, aktif_mi=True,
        aciklama="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
        dogrulayici_adi=None, kaynak_katman="katman2_presidio", entity_tipi="RFC5737_TEST_IP",
        guven_skoru=1.0, allow_list_mi=True, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="allow_rfc5737_198_51_100", kategori="rfc5737_test_ip", desen_tipi="presidio",
        regex_deseni=r"\b198\.51\.100\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_rfc5737_ip", oncelik=201, aktif_mi=True,
        aciklama="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
        dogrulayici_adi=None, kaynak_katman="katman2_presidio", entity_tipi="RFC5737_TEST_IP",
        guven_skoru=1.0, allow_list_mi=True, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="allow_rfc5737_203_0_113", kategori="rfc5737_test_ip", desen_tipi="presidio",
        regex_deseni=r"\b203\.0\.113\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_rfc5737_ip", oncelik=202, aktif_mi=True,
        aciklama="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
        dogrulayici_adi=None, kaynak_katman="katman2_presidio", entity_tipi="RFC5737_TEST_IP",
        guven_skoru=1.0, allow_list_mi=True, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_dotenv_reference", kategori="config_secret_reference", desen_tipi="presidio",
        regex_deseni=r"\.env\b", regex_bayraklari="i", yer_tutucu_on_eki="mask_config_secret_ref",
        oncelik=210, aktif_mi=True, aciklama=".env dosyasi referansi.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="CONFIG_SECRET_REFERENCE", guven_skoru=0.95,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_yaml_secrets_reference", kategori="config_secret_reference", desen_tipi="presidio",
        regex_deseni=r"\bsecrets?\.ya?ml\b", regex_bayraklari="i",
        yer_tutucu_on_eki="mask_config_secret_ref", oncelik=211, aktif_mi=True,
        aciklama="secrets.yaml / secret.yml dosyasi referansi.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="CONFIG_SECRET_REFERENCE", guven_skoru=0.95,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_properties_config_reference", kategori="config_secret_reference",
        desen_tipi="presidio", regex_deseni=r"\bconfig(?:uration)?\.properties\b", regex_bayraklari="i",
        yer_tutucu_on_eki="mask_config_secret_ref", oncelik=212, aktif_mi=True,
        aciklama="config.properties / configuration.properties referansi.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="CONFIG_SECRET_REFERENCE", guven_skoru=0.9,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_credentials_file_reference", kategori="config_secret_reference",
        desen_tipi="presidio", regex_deseni=r"\bcredentials?\.(?:json|xml|ini)\b", regex_bayraklari="i",
        yer_tutucu_on_eki="mask_config_secret_ref", oncelik=213, aktif_mi=True,
        aciklama="credentials.json/xml/ini dosyasi referansi.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="CONFIG_SECRET_REFERENCE", guven_skoru=0.95,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_internal_domain_name", kategori="internal_domain", desen_tipi="presidio",
        regex_deseni=r"\b[\w\-]+\.internal\b", regex_bayraklari="i",
        yer_tutucu_on_eki="mask_ic_domain", oncelik=220, aktif_mi=True,
        aciklama="Ic .internal domain/sunucu adi.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="IC_DOMAIN_ADI", guven_skoru=0.9,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="presidio_internal_prod_hostname", kategori="internal_hostname", desen_tipi="presidio",
        regex_deseni=r"\b[\w\-]+-prod-[\w\-]+\b", regex_bayraklari="i",
        yer_tutucu_on_eki="mask_ic_host", oncelik=221, aktif_mi=True,
        aciklama="Ic prod hostname konvansiyonu.", dogrulayici_adi=None,
        kaynak_katman="katman2_presidio", entity_tipi="IC_PROD_HOSTNAME", guven_skoru=0.9,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="private_key_block", kategori="secret", desen_tipi="regex",
        regex_deseni=r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----.*?-----END \1-----",
        regex_bayraklari="s", yer_tutucu_on_eki="mask_private_key", oncelik=5, aktif_mi=True,
        aciklama=(
            "PEM formatli ozel anahtar bloklari (RSA/EC/DSA/OPENSSH/ENCRYPTED/PGP) - BEGIN/END "
            "arasindaki TUM icerik tek placeholder ile degistirilir."
        ),
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="http_basic_auth_credentials", kategori="credential", desen_tipi="regex",
        regex_deseni=r"(?<=://)[^\s/@'\"]+:[^\s/@'\"]+@[^\s/'\"]+", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_basic_auth", oncelik=25, aktif_mi=True,
        aciklama="URL icine gomulu HTTP basic-auth kimlik bilgileri (kullanici:parola@host).",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="google_api_key", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\bAIza[0-9A-Za-z_-]{35}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_google_api_key", oncelik=11, aktif_mi=True,
        aciklama="Google API anahtari formati (AIza + 35 karakter).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="github_token", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_github_token", oncelik=12, aktif_mi=True,
        aciklama="GitHub token formati (ghp_/gho_/ghu_/ghs_/ghr_ onekli).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="slack_token", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\bxox[baprs]-[A-Za-z0-9-]{10,72}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_slack_token", oncelik=13, aktif_mi=True,
        aciklama="Slack token formati (xoxb-/xoxa-/xoxp-/xoxr-/xoxs- onekli).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="stripe_api_key", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,99}\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_stripe_key", oncelik=14, aktif_mi=True,
        aciklama="Stripe API anahtari formati (sk_/pk_/rk_ live/test onekli).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="jwt_token", kategori="secret", desen_tipi="regex",
        regex_deseni=r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", regex_bayraklari=None,
        yer_tutucu_on_eki="mask_jwt_token", oncelik=15, aktif_mi=True,
        aciklama="JWT (JSON Web Token) formati - base64url ile kodlanmis 3 parcali token.",
        dogrulayici_adi=None, kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85,
        allow_list_mi=False, desen_sifreli_mi=False,
    ),
    dict(
        kural_adi="uuid", kategori="uuid", desen_tipi="regex",
        regex_deseni=r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        regex_bayraklari="i", yer_tutucu_on_eki="mask_uuid", oncelik=45, aktif_mi=True,
        aciklama="UUID/GUID (8-4-4-4-12 onaltilik format).", dogrulayici_adi=None,
        kaynak_katman="katman1", entity_tipi=None, guven_skoru=0.85, allow_list_mi=False,
        desen_sifreli_mi=False,
    ),
]

SEED_HARIC_TUTMA_DESENLERI = [
    dict(desen_adi="dotenv_file", glob_deseni=".env", uygulanir="file", aktif_mi=True),
    dict(desen_adi="dotenv_variants", glob_deseni=".env.*", uygulanir="file", aktif_mi=True),
    dict(desen_adi="git_directory", glob_deseni=".git", uygulanir="directory", aktif_mi=True),
    dict(desen_adi="pem_key_file", glob_deseni="*.pem", uygulanir="file", aktif_mi=True),
    dict(desen_adi="private_key_file", glob_deseni="*.key", uygulanir="file", aktif_mi=True),
    dict(desen_adi="pkcs12_file", glob_deseni="*.p12", uygulanir="file", aktif_mi=True),
    dict(desen_adi="ssh_private_key", glob_deseni="id_rsa", uygulanir="file", aktif_mi=True),
]

# Katman 2 Presidio'nun kod/config dosya turlerinde calisacak yerlesik
# kategorileri - dogal-dil (PERSON/ORGANIZATION vb.) kategorileri BILEREK
# DISINDA (bu uzantilarda spaCy NER'in yanlis-pozitif orani yuksek).
_FILE_EXTENSIONS = [
    "c", "cc", "cfg", "conf", "cpp", "cs", "dart", "env", "go", "gradle", "groovy", "h", "hpp",
    "ini", "java", "js", "json", "jsx", "kt", "kts", "lua", "m", "mm", "php", "pl", "properties",
    "ps1", "py", "rb", "rs", "scala", "sh", "sql", "swift", "toml", "ts", "tsx", "xml", "yaml", "yml",
]
_ALLOWED_CATEGORIES = [
    "CREDIT_CARD", "CRYPTO", "DATE_TIME", "EMAIL_ADDRESS", "IBAN_CODE", "IP_ADDRESS",
    "PHONE_NUMBER", "URL",
]
SEED_FILE_CATEGORY_RESTRICTIONS = [
    dict(dosya_uzantisi=ext, izinli_kategori=cat, aktif_mi=True)
    for ext in _FILE_EXTENSIONS
    for cat in _ALLOWED_CATEGORIES
]


def upgrade() -> None:
    op.bulk_insert(filtre_kurallari_table, SEED_FILTRE_KURALLARI)
    op.bulk_insert(haric_tutma_desenleri_table, SEED_HARIC_TUTMA_DESENLERI)
    op.bulk_insert(dosya_tipi_kategori_kisitlamasi_table, SEED_FILE_CATEGORY_RESTRICTIONS)


def downgrade() -> None:
    op.execute(dosya_tipi_kategori_kisitlamasi_table.delete())
    op.execute(haric_tutma_desenleri_table.delete())
    op.execute(filtre_kurallari_table.delete())
