"""Single source of truth for the initial filter_rules and exclude_patterns
datasets.

Imported by the Alembic seed migrations so the DB gets these rows on a
fresh install. Keeping the seed data in one module prevents drift between
runtime code and database migrations.
"""

# Ilk kurulumda (fresh install) filtre_kurallari tablosuna yuklenen cekirdek
# kural seti: parametrik kimlik kurallari (proje/sicil/branch) ve temel
# regex kurallari (secret/email/ip).
SEED_RULES = [
    dict(
        rule_name="project_name",
        category="project_name",
        pattern_type="parametric",
        regex_pattern=None,
        regex_flags=None,
        placeholder_prefix="mask_proje_adi",
        priority=1,
        is_active=True,
        description="Proje adi - calisma zamaninda saglanan literal deger, whole-word eslesir.",
    ),
    dict(
        rule_name="sicil_no",
        category="sicil_no",
        pattern_type="parametric",
        regex_pattern=None,
        regex_flags=None,
        placeholder_prefix="mask_personel_no",
        priority=2,
        is_active=True,
        description="sicil numarasi - calisma zamaninda saglanan literal deger.",
    ),
    dict(
        rule_name="branch_name",
        category="branch_name",
        pattern_type="parametric",
        regex_pattern=None,
        regex_flags=None,
        placeholder_prefix="mask_branch",
        priority=3,
        is_active=True,
        description="Git branch adi - calisma zamaninda saglanan literal deger.",
    ),
    dict(
        rule_name="aws_access_key",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\bAKIA[0-9A-Z]{16}\b",
        regex_flags=None,
        placeholder_prefix="mask_aws_key",
        priority=10,
        is_active=True,
        description="AWS access key ID formati.",
    ),
    dict(
        rule_name="generic_secret_assignment",
        category="secret",
        pattern_type="regex",
        # NOT: anahtar kelime (api_key/secret/token/password/passwd) bir
        # bilesik tanimlayicinin BASINDA, ORTASINDA ya da SONUNDA
        # (alt-cizgiyle ayrilmis herhangi bir segmentte) gecebilir - orn.
        # "aws_secret_access_key", "db_password", "stripe_api_secret_key".
        # "resecret_value"/"secretary" gibi alt-cizgisiz, anahtar kelimeyle
        # SADECE harf duzeyinde ortusen sozcukler bilerek reddedilir.
        #
        # Deger kismi ÜÇ ayri sekilde eslesir - bkz. 7ca8a252e94b migration'i:
        #   1) "..." : cift tirnakla baslar, ayni satirda cift tirnakla
        #      KAPANMASI SART (12+ ic karakter) - kapanmayan bir tirnak
        #      artik eslesmeyi satirin geri kalanina yaymaz.
        #   2) '...' : ayni, tek tirnak icin.
        #   3) tirnaksiz: 12+ karakter, ama bosluk/tirnak DISINDA parantez/
        #      suslu/kose parantez/virgul/noktali virgul/nokta da haric -
        #      boylece "secret_key = conf.get(section, key)" gibi bir KOD
        #      İFADESİ (fonksiyon cagrisi) asla "deger" sanilip
        #      yutulmaz - eskiden [^\s'"]{12,} HER SEYI (parantez dahil)
        #      yuttugu icin "secret_key = conf.get(section," gibi bir span
        #      TEK PLACEHOLDER'A donusuyor, acilis parantezi kapanisiyla
        #      esesiz kalip Python sozdizimini bozuyordu (gercek export'ta
        #      gozlemlendi: tokens.py -> "unmatched ')'").
        regex_pattern=(
            r"(?:(?<![A-Za-z0-9_])|(?<=_))(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
            r"(?:_[A-Za-z0-9]+)*\b['\"]?\s*[:=]\s*"
            r"(?:\"[^\"\n]{12,}\"|'[^'\n]{12,}'|[^\s'\"(){}\[\],;.]{12,})"
        ),
        regex_flags="i",
        placeholder_prefix="mask_secret",
        priority=20,
        is_active=True,
        description="api_key/secret/token/password/parola/sifre iceren (onekli/onekli-sonekli "
        "bilesik tanimlayicilar dahil, JSON/TOML tirnakli anahtar formu dahil) atama kalibi.",
    ),
    dict(
        rule_name="email_address",
        category="email",
        pattern_type="regex",
        regex_pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        regex_flags=None,
        placeholder_prefix="mask_email",
        priority=30,
        is_active=True,
        description="E-posta adresi.",
    ),
    dict(
        rule_name="ipv4_address",
        category="ip",
        pattern_type="regex",
        regex_pattern=r"\b(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\b",
        regex_flags=None,
        placeholder_prefix="mask_ip",
        priority=40,
        is_active=True,
        description="IPv4 adresi (0-255 oktet dogrulamali).",
    ),
]


# SEED_TC_KIMLIK_NO_RULE ile ayni gerekce: SEED_RULES'tan bilerek ayri (bkz.
# yukarideki SEED_RULES dokstring'i) - sonradan (kurumsal kullanim gozden
# gecirmesinde bulunan bir kapsam bosluğu uzerine) eklenen bir kural, kendi
# migration'inda kendi adiyla eklenir. UUID/GUID icin ne bir DB kurali ne de
# Presidio'nun yerlesik kategorileri arasinda bir tespit vardi.
SEED_UUID_RULE = dict(
    rule_name="uuid",
    category="uuid",
    pattern_type="regex",
    # 8-4-4-4-12 hex format - surum/varyant nibble'lari kasitli olarak
    # DOGRULANMAZ (sadece 1-5 surumleri degil, nil UUID/ozel uretilmis
    # varyantlar da yakalansin diye) - tek parca, bolunmeden eslesir.
    regex_pattern=r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    regex_flags="i",
    validator_name=None,
    placeholder_prefix="mask_uuid",
    priority=45,
    is_active=True,
    description="UUID/GUID (8-4-4-4-12 onaltilik format).",
)


# SEED_RULES'un aksine bu "ilk kurulum" verisi degil - sisteme sonradan
# eklenen bir kural (bkz. app/services/validators.py). Bilerek ayri bir
# sabitte tutulur: SEED_RULES'i mutasyona ugratmak, onu import eden ESKI
# (zaten calismis) migration'larin anlamini geriye donuk degistirir. Yeni
# kurallar boylece kendi migration'larinda, kendi adiyla eklenir.
SEED_TC_KIMLIK_NO_RULE = dict(
    rule_name="tc_kimlik_no",
    category="tc_kimlik_no",
    pattern_type="regex",
    regex_pattern=r"\b[1-9][0-9]{10}\b",
    regex_flags=None,
    validator_name="tc_kimlik_no",
    placeholder_prefix="mask_tc_kimlik",
    priority=50,
    is_active=True,
    description="TC kimlik numarasi (11 hane, resmi checksum ile dogrulanir).",
)


# SEED_TC_KIMLIK_NO_RULE ile ayni gerekce: SEED_RULES'tan bilerek ayri.
# pattern_type='llm' - sabit bir regex'i olmayan, yerel LLM (bkz.
# app/services/llm_recognizer.py) ile taranan ilk kural. Sadece
# VLLM_ENABLED=true iken devreye girer (bkz. app/core/config.py);
# kapaliyken bu satirin var olmasi hicbir performans/davranis etkisi yaratmaz.
SEED_PERSON_NAME_LLM_RULE = dict(
    rule_name="person_name",
    category="person_name",
    pattern_type="llm",
    regex_pattern=None,
    regex_flags=None,
    validator_name=None,
    placeholder_prefix="mask_kisi_adi",
    priority=90,
    is_active=True,
    description="Kişi adı - metinde geçen gerçek, spesifik bir insanın ad soyadı; "
    "genel/ortak kelimeler, unvanlar veya şirket adları değil.",
)


# SEED_RULES'un aksine bu da "ilk kurulum" verisi degil - PEM ozel anahtar
# bloklari, URL-gomulu basic-auth kimlik bilgileri ve bilinen saglayici
# token formatlari (Google/GitHub/Slack/Stripe/JWT) icin sonradan eklenen
# kurallar. Ayni SEED_TC_KIMLIK_NO_RULE gerekcesiyle SEED_RULES'tan bilerek
# AYRI: kendi migration'larinda, kendi adlarıyla eklenirler.
SEED_ADDITIONAL_SECRET_FORMAT_RULES = [
    dict(
        rule_name="private_key_block",
        category="secret",
        pattern_type="regex",
        # PEM formatli TUM ozel anahtar bloklarini (RSA/EC/DSA/OPENSSH/
        # ENCRYPTED/PKCS8 generic, PGP dahil) TEK PARCA yakalar - backreference
        # (\1) BEGIN/END etiketlerinin ayni oldugunu garanti eder. "s" bayragi
        # (DOTALL) ile "." satir sonlarini da kapsar. CERTIFICATE/PUBLIC KEY
        # gibi hassas OLMAYAN bloklari BILEREK yakalamaz (paylasilmalari
        # gerekir, gizlenmemelidir).
        regex_pattern=r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----.*?-----END \1-----",
        regex_flags="s",
        validator_name=None,
        placeholder_prefix="mask_private_key",
        priority=5,
        is_active=True,
        description="PEM formatli ozel anahtar bloklari (RSA/EC/DSA/OPENSSH/ENCRYPTED/PGP) - "
        "BEGIN/END arasindaki TUM icerik tek placeholder ile degistirilir.",
    ),
    dict(
        rule_name="http_basic_auth_credentials",
        category="credential",
        pattern_type="regex",
        # scheme://user:password@host seklindeki URL-gomulu kimlik
        # bilgilerini yakalar. "://" hemen sonrasindan baslar (lookbehind),
        # ":" ve "@" ikisinin de bulunmasini sart kosar - boylece bare
        # "user@host" (parolasiz) ya da salt "scheme://host" YANLISLIKLA
        # eslesmez. Bu, email_address kuralindan ONCE calisir (oncelik 25 <
        # 30) - boylece "sifre@host" kismini bir e-posta gibi degil, TUM
        # "kullanici:sifre@host" parcasini tek blok olarak yakalar.
        regex_pattern=r"(?<=://)[^\s/@'\"]+:[^\s/@'\"]+@[^\s/'\"]+",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_basic_auth",
        priority=25,
        is_active=True,
        description="URL icine gomulu HTTP basic-auth kimlik bilgileri (kullanici:parola@host).",
    ),
    dict(
        rule_name="google_api_key",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\bAIza[0-9A-Za-z_-]{35}\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_google_api_key",
        priority=11,
        is_active=True,
        description="Google API anahtari formati (AIza + 35 karakter).",
    ),
    dict(
        rule_name="github_token",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_github_token",
        priority=12,
        is_active=True,
        description="GitHub token formati (ghp_/gho_/ghu_/ghs_/ghr_ onekli).",
    ),
    dict(
        rule_name="slack_token",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\bxox[baprs]-[A-Za-z0-9-]{10,72}\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_slack_token",
        priority=13,
        is_active=True,
        description="Slack token formati (xoxb-/xoxa-/xoxp-/xoxr-/xoxs- onekli).",
    ),
    dict(
        rule_name="stripe_api_key",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,99}\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_stripe_key",
        priority=14,
        is_active=True,
        description="Stripe API anahtari formati (sk_/pk_/rk_ live/test onekli).",
    ),
    dict(
        rule_name="jwt_token",
        category="secret",
        pattern_type="regex",
        regex_pattern=r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_jwt_token",
        priority=15,
        is_active=True,
        description="JWT (JSON Web Token) formati - base64url ile kodlanmis 3 parcali token.",
    ),
    dict(
        rule_name="unc_network_path",
        category="network_path",
        pattern_type="regex",
        regex_pattern=r"\\\\[\w.-]+(?:\\[\w.$ -]+)+",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_ag_yolu",
        priority=26,
        is_active=True,
        description="Windows UNC ag paylasim yolu (\\\\host\\pay\\alt-yol).",
    ),
    dict(
        rule_name="contextual_personnel_id",
        category="personel_kimlik_no",
        pattern_type="regex",
        regex_pattern=(
            r"\b(?:sicil(?:\s*(?:no|numaras[ıi]))?|personel\s*(?:no|numaras[ıi])|employee\s*id)"
            r"\s*[:\-]?\s*[0-9]{5,8}\b"
            r"(?:\s*(?:,|/|ve)\s*[0-9]{5,8}\b)*"
        ),
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_personel_kimlik",
        priority=52,
        is_active=True,
        description="Belge icinde 'sicil'/'sicil no'/'personel no'/'employee id' etiketiyle "
        "gecen (soneksiz/ayracsiz/coklu-numarali varyantlar dahil) personel kimlik numarasi "
        "(etiket sayiyla birlikte maskelenir).",
    ),
    dict(
        rule_name="xml_element_secret",
        category="secret",
        pattern_type="regex",
        # generic_secret_assignment'in XML/HTML eleman-govdesi karsiligi:
        # `<password>deger</password>` gibi anahtar-kelime tasiyan bir etiket
        # ciftinin ICERIGINI yakalar. `(?P<deger>...)` named group'u
        # SADECE ic degeri isaretler - find_matches_compiled() bu grubu
        # gordugunde eslesme span'ini ETIKETLERI DEGIL SADECE bu ic
        # gruba daraltir (bkz. rule_engine.py modul dokstring'i), boylece
        # placeholder degeri degistirir, `<password>`/`</password>`
        # etiketleri METINDE OLDUGU GIBI kalir - XML sozdizimi bozulmaz.
        # Etiket adi onek/sonek tasiyabilir (dbPassword, password_hash,
        # ns:password) ama anahtar kelimenin hemen ardindan kucuk harfle
        # DOGRUDAN devam eden bir sonek (orn. "passwordless") KASITLI
        # OLARAK reddedilir - bkz. generic_secret_assignment'teki ayni
        # yanlis-pozitif korumasi.
        regex_pattern=(
            r"<((?:[A-Za-z][\w.-]*:)?[\w.-]*(?:api[_-]?key|secret|token|password|passwd|parola|sifre|şifre)"
            r"(?![a-zçğıöşü])(?:[_.:-][\w.-]*)?)>"
            r"(?P<deger>[^<>&]{8,})</\1>"
        ),
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_secret",
        priority=21,
        is_active=True,
        description="XML/HTML eleman govdesinde gecen api_key/secret/token/password/parola/sifre "
        "degeri (orn. <password>deger</password>) - sadece ic deger degistirilir, etiketler korunur.",
    ),
]


# Katman 2 Presidio icin kurum-ici pattern kurallari: RFC 5737 test IP
# allow-list'i ve config/secret referansi, ic domain/hostname gibi
# kurum-ozel tespit desenleri (source_layer='katman2_presidio').
SEED_PRESIDIO_RULES = [
    dict(
        rule_name="allow_rfc5737_192_0_2",
        category="rfc5737_test_ip",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\b192\.0\.2\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_rfc5737_ip",
        entity_type="RFC5737_TEST_IP",
        confidence_score=1.0,
        is_allow_list=True,
        priority=200,
        is_active=True,
        description="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
    ),
    dict(
        rule_name="allow_rfc5737_198_51_100",
        category="rfc5737_test_ip",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\b198\.51\.100\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_rfc5737_ip",
        entity_type="RFC5737_TEST_IP",
        confidence_score=1.0,
        is_allow_list=True,
        priority=201,
        is_active=True,
        description="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
    ),
    dict(
        rule_name="allow_rfc5737_203_0_113",
        category="rfc5737_test_ip",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\b203\.0\.113\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b",
        regex_flags=None,
        validator_name=None,
        placeholder_prefix="mask_rfc5737_ip",
        entity_type="RFC5737_TEST_IP",
        confidence_score=1.0,
        is_allow_list=True,
        priority=202,
        is_active=True,
        description="RFC 5737 dokumantasyon/test IP blogu; Katman 2 bulgularini bastirir.",
    ),
    dict(
        rule_name="presidio_dotenv_reference",
        category="config_secret_reference",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\.env\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_config_secret_ref",
        entity_type="CONFIG_SECRET_REFERENCE",
        confidence_score=0.95,
        is_allow_list=False,
        priority=210,
        is_active=True,
        description=".env dosyasi referansi.",
    ),
    dict(
        rule_name="presidio_yaml_secrets_reference",
        category="config_secret_reference",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\bsecrets?\.ya?ml\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_config_secret_ref",
        entity_type="CONFIG_SECRET_REFERENCE",
        confidence_score=0.95,
        is_allow_list=False,
        priority=211,
        is_active=True,
        description="secrets.yaml / secret.yml dosyasi referansi.",
    ),
    dict(
        rule_name="presidio_properties_config_reference",
        category="config_secret_reference",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\bconfig(?:uration)?\.properties\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_config_secret_ref",
        entity_type="CONFIG_SECRET_REFERENCE",
        confidence_score=0.9,
        is_allow_list=False,
        priority=212,
        is_active=True,
        description="config.properties / configuration.properties referansi.",
    ),
    dict(
        rule_name="presidio_credentials_file_reference",
        category="config_secret_reference",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\bcredentials?\.(?:json|xml|ini)\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_config_secret_ref",
        entity_type="CONFIG_SECRET_REFERENCE",
        confidence_score=0.95,
        is_allow_list=False,
        priority=213,
        is_active=True,
        description="credentials.json/xml/ini dosyasi referansi.",
    ),
    dict(
        rule_name="presidio_internal_domain_name",
        category="internal_domain",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\b(?:[\w-]+\.)+(?:internal|local)\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_ic_domain",
        entity_type="IC_DOMAIN_ADI",
        confidence_score=0.9,
        is_allow_list=False,
        priority=220,
        is_active=True,
        description="Ic .internal/.local domain/sunucu adi (coklu alt-etiket destekli).",
    ),
    dict(
        rule_name="allow_xmlns_namespace_uri",
        category="xml_namespace_uri",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"""\bxmlns(?::[\w.-]+)?\s*=\s*(?:"https?://[^"]*"|'https?://[^']*')""",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_xml_ns",
        entity_type="XML_NAMESPACE_URI",
        confidence_score=1.0,
        is_allow_list=True,
        priority=203,
        is_active=True,
        description="XML namespace bildirimi (xmlns=\"http(s)://...\") - yapisal/genel bilinen "
        "sema URI'si, hassas veri degil; Katman 2 URL bulgusunu bastirir.",
    ),
    dict(
        rule_name="presidio_internal_prod_hostname",
        category="internal_hostname",
        source_layer="katman2_presidio",
        pattern_type="presidio",
        regex_pattern=r"\b[\w\-]+-prod-[\w\-]+\b",
        regex_flags="i",
        validator_name=None,
        placeholder_prefix="mask_ic_host",
        entity_type="IC_PROD_HOSTNAME",
        confidence_score=0.9,
        is_allow_list=False,
        priority=221,
        is_active=True,
        description="Ic prod hostname konvansiyonu.",
    ),
]


# Bu dosya/klasor turleri iceriginde ne oldugu taranmadan dogrudan disari
# birakilir (hedefe hic kopyalanmaz) - .env gibi calisma zamani config
# dosyalarinin regex tabanli kurallari atlatip disari sizmasini onlemek icin
# (bkz. FAZ 5 guvenlik incelemesi: .env dosyasi generic_secret/aws_access_key
# kurallarinin hicbirine uymuyordu).
SEED_EXCLUDE_PATTERNS = [
    # dotenv_file: calisma zamani ortam degiskenleri - gercek sifre/anahtar icerir.
    dict(pattern_name="dotenv_file", glob_pattern=".env", applies_to="file", is_active=True),
    # dotenv_variants: .env.local, .env.production vb. varyantlar (.env.example
    # haric degildir, kullanici ihtiyaca gore bu deseni daraltabilir).
    dict(pattern_name="dotenv_variants", glob_pattern=".env.*", applies_to="file", is_active=True),
    # git_directory: Git internal dizini - commit gecmisi, olasi eski secret'lar icerebilir.
    dict(pattern_name="git_directory", glob_pattern=".git", applies_to="directory", is_active=True),
    # pem_key_file: PEM formatli sertifika/ozel anahtar dosyalari.
    dict(pattern_name="pem_key_file", glob_pattern="*.pem", applies_to="file", is_active=True),
    # private_key_file: genel ozel anahtar dosya uzantisi.
    dict(pattern_name="private_key_file", glob_pattern="*.key", applies_to="file", is_active=True),
    # pkcs12_file: PKCS#12 sertifika/anahtar deposu.
    dict(pattern_name="pkcs12_file", glob_pattern="*.p12", applies_to="file", is_active=True),
    # ssh_private_key: varsayilan isimli SSH ozel anahtari.
    dict(pattern_name="ssh_private_key", glob_pattern="id_rsa", applies_to="file", is_active=True),
]
