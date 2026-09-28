from pathlib import Path

# pydantic-settings: her ayar grubunu ortam degiskenlerinden (.env) okuyup
# dogrulayan (Field(...) zorunlu alanlar, model_validator capraz kontroller) taban sinif.
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/core/config.py -> masking_system/.env (repo kok dizinindeki TEK .env
# dosyasi - docker-compose.yml de ayni dosyayi okur, calisma dizini (cwd)
# neresi olursa olsun dogru bulunsun diye mutlak yol kullanilir).
_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"


# SQLite baglanti ayarlarini tutan sinif - DB_* on ekli ortam degiskenlerinden
# okunur. Tek sorumluluk: sadece veritabani baglantisiyla ilgilenir (SRP).
#
# Postgres'ten SQLite'a bilerek gecildi: intra deploy'da ayri bir DB
# sunucusu/servisi (Docker, native kurulum, ag/firewall/pg_hba.conf
# yapilandirmasi) kurmaya gerek kalmiyor - uygulama tek bir dosyaya
# yaziyor. Coklu-yazici (concurrent write) senaryosu yoktur: export/unmask
# DB'ye YAZAN kisimlar zaten sirali/tek-coroutine calisir (bkz.
# app/services/exporter.py Faz B/D dokstring'i), bu yuzden SQLite'in
# tek-yazarli kilit modeli bir kisitlama yaratmaz.
#
# `path` alaninin kod icinde VARSAYILAN DEGERI VAR (digre ayar gruplarindan
# farkli olarak) - bir dosya yolu, host/parola gibi ortam-ozel/hassas bir
# baglanti bilgisi degildir; makul bir varsayilanla baslamak deploy
# sürtünmesini azaltir, degistirmek isteyen DB_PATH ile override eder.
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=_ENV_FILE, extra="ignore")

    path: str = Field(
        "masking.db",
        description="SQLite veritabani dosyasinin yolu. Goreli verilirse repo kokune "
        "(.env ile ayni dizin) gore cozumlenir. .env dosyasinda DB_PATH ile degistirilebilir.",
    )

    # DB dosya yolunu, calisma dizininden bagimsiz mutlak bir yola cevirir.
    @property
    def resolved_path(self) -> Path:
        candidate = Path(self.path)
        if candidate.is_absolute():
            return candidate
        return (_ENV_FILE.parent / candidate).resolve()

    # SQLAlchemy'nin bekledigi tam baglanti URL'sini uretir.
    @property
    def url(self) -> str:
        return f"sqlite:///{self.resolved_path}"


# Sifreleme ayarlarini tutan sinif - SECURITY_* on ekli ortam degiskenlerinden okunur.
# encryption_key icin kod icinde VARSAYILAN DEGER YOKTUR: gercek hassas veri
# (IP, e-posta, secret vb.) bu anahtarla sifrelendigi icin .env'de tanimlanmasi
# zorunludur - eksikse uygulama baslarken acikca hata verir (sessiz/guvensiz
# bir varsayilanla calismaz).
class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECURITY_", env_file=_ENV_FILE, extra="ignore")

    encryption_key: str = Field(
        ...,
        description=(
            "Fernet key (cryptography.fernet.Fernet.generate_key() ile uretilir). "
            ".env dosyasinda SECURITY_ENCRYPTION_KEY olarak tanimlanmalidir."
        ),
    )


# vLLM LLM entegrasyonunu (bkz. app/services/llm_recognizer.py) yapilandiran
# ayarlar - VLLM_* on ekli ortam degiskenlerinden okunur. vLLM'in OpenAI-
# uyumlu sunucusuyla (`vllm serve <model> --port ...`) /v1/chat/completions
# uzerinden konusulur - bkz. llm_recognizer.call_vllm.
#
# `host`/`model` sadece VLLM_ENABLED=true iken zorunludur (kod varsayilani
# kapali; .env.example sablonu ise katmani acik getirir). Boylece LLM
# kapaliyken deploy ortami gereksiz baglanti bilgisi istemez; acikken ise
# hangi sunucu/modelin kullanilacagi tamamen .env tarafindan yonetilir.
#
# `enabled`/`timeout_seconds`/`max_file_chars` kimlik bilgisi DEGIL, davranis
# ayari oldugu icin makul varsayilanlari var. `enabled` ozellikle False
# varsayilanla gelir: bu katman strictly opt-in'dir - VLLM_HOST/MODEL dolu
# olsa bile enabled=false iken vLLM'e hic istek gitmez, mevcut
# regex/checksum pipeline'i hicbir degisiklik olmadan calismaya devam eder.
class VLLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VLLM_", env_file=_ENV_FILE, extra="ignore")

    enabled: bool = Field(
        False,
        description="LLM tabanli tespit katmanini acar/kapatir (kill-switch). Kapaliyken "
        "pipeline tamamen regex/parametric/checksum ile calisir, vLLM'e hic istek gitmez.",
    )
    host: str | None = Field(
        None,
        description="vLLM OpenAI-uyumlu sunucu adresi (orn. http://vllm.intra.local:8000). "
        "VLLM_ENABLED=true ise .env dosyasinda VLLM_HOST olarak tanimlanmalidir.",
    )
    model: str | None = Field(
        None,
        description="Kullanilacak model adi - `vllm serve` komutuna verilen model yolu/adi ya da "
        "--served-model-name ile aciklanan isimle BIREBIR aynı olmalidir. VLLM_ENABLED=true ise "
        ".env dosyasinda VLLM_MODEL olarak tanimlanmalidir.",
    )
    api_key: str | None = Field(
        None,
        description="vLLM sunucusu --api-key ile korunuyorsa buraya girilir (Authorization: Bearer "
        "basligi olarak gonderilir). Sunucu korumasizsa bos birakilabilir.",
    )
    timeout_seconds: float = Field(
        200.0,
        gt=0,
        description="Yerel kuyruktan sonra baslayan HTTP toplam sure tavani. Tek bir vLLM cagrisi icin zaman asimi (saniye). Buyuk modeller donanima gore "
        "tek dosya basina onlarca saniye ila birkac dakika surebilir - kendi ortaminizda olcup "
        ".env icinde VLLM_TIMEOUT_SECONDS ile ayarlayin.",
    )
    max_file_chars: int = Field(
        6_000,
        gt=0,
        description="LLM'e tek seferde gonderilecek parca icin karakter tavani. Buyuk dosyalar "
        "atlanmaz; bu sinira gore parcalara bolunur.",
    )
    chunk_overlap_chars: int = Field(500, ge=0, description="LLM overlap; en fazla chunk boyunun dortte biri.")
    max_tokens: int = Field(512, gt=0, description="Tespit/audit token tavani; kesilen yanit basarisizdir.")
    disable_thinking: bool = Field(
        False,
        description="Qwen3 gibi thinking modlu modellerde istege chat_template_kwargs="
        "{enable_thinking: false} ekler (vLLM). Acikken model max_tokens'i <think> ile "
        "tuketip yanit kesilebilir; kesilen yanit basarisiz sayilir.",
    )
    seed: int = Field(
        42,
        description="OpenAI-uyumlu istekteki seed degeri. Temperature=0 ile birlikte tekrar "
        "edilebilir LLM tespitleri icin kullanilir.",
    )
    max_concurrent_requests: int = Field(
        1, gt=0,
        description="Ayni event loop ve LLM endpoint'i icin ortak HTTP istek siniri. "
        "Ollama Parallel:1 icin 1; vLLM icin benchmark ile belirlenir. "
        "Ayri process/worker/CLI sinirlari toplanir; dagitik kota degildir.",
    )

    # LLM acikken (enabled=true) host/model bos ya da .env.example'daki
    # CHANGE_ME sablon degeriyle birakilmissa hata verir - aksi halde uygulama
    # baslar ama her dosya LLM hatasiyla karantinaya duser. Host sonundaki
    # "/" ve "/v1" temizlenir: call_vllm "/v1/chat/completions"i kendisi
    # ekler, "/v1" kalirsa istek ".../v1/v1/..."e gidip 404 alir.
    @model_validator(mode="after")
    def require_connection_when_enabled(self) -> "VLLMSettings":
        if self.host:
            host = self.host.strip().rstrip("/")
            if host.endswith("/v1"):
                host = host[: -len("/v1")].rstrip("/")
            self.host = host
        if self.enabled:
            if not self.host or not self.model:
                raise ValueError("VLLM_ENABLED=true iken VLLM_HOST ve VLLM_MODEL zorunludur")
            if "CHANGE_ME" in (self.host, self.model.strip()):
                raise ValueError("VLLM_HOST/VLLM_MODEL hala CHANGE_ME; gercek vLLM sunucusu ve model adini girin")
        return self


# Web arayuzune (Streamlit) ozgu ayarlar - WEB_* on ekli ortam
# degiskenlerinden okunur. Diger ayar gruplarinin aksine hicbir alani
# ZORUNLU degildir: bos birakilirsa web arayuzu, kaynak/hedef olarak
# serbest metin sunucu yolu kabul eden "Klasor Yolu" modunu KAPATIR (sadece
# yukleme modu calisir) - varsayilan olarak GUVENLI tarafta durmak icin
# bilincli tercih (bkz. app/webapp/path_guard.py).
class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEB_", env_file=_ENV_FILE, extra="ignore")

    allowed_roots: str = Field(
        "",
        description="Web arayuzunun 'Klasor Yolu' modunda kaynak/hedef olarak kabul edecegi "
        "izinli kok dizinlerin virgulle ayrilmis listesi (orn. "
        "'/data/projeler,/data/disa-aktarim'). Bos ise 'Klasor Yolu' modu tamamen kapalidir.",
    )

    # FastAPI backend surecinin adres/port ayarlari.
    api_host: str = Field(
        "127.0.0.1",
        description="FastAPI backend surecinin (uvicorn) dinleyecegi adres.",
    )
    api_port: int = Field(
        8001,
        description="FastAPI backend surecinin (uvicorn) dinleyecegi port. Streamlit'in "
        "varsayilan portuyla (8501) cakismasin diye 8001 secildi.",
    )
    api_base_url: str = Field(
        "http://127.0.0.1:8001",
        description="Streamlit'in FastAPI backend'e istek atarken kullanacagi taban adres.",
    )
    api_request_timeout_seconds: float = Field(
        300.0,
        description="Streamlit'in backend'e attigi isteklerde (ozellikle export/unmask - tek "
        "blocking cagriyla tamamlanir, canli ilerleme yoktur) beklenecek azami sure.",
    )

    # allowed_roots metnini (virgulle ayrilmis) mutlak Path listesine cevirir.
    @property
    def allowed_root_paths(self) -> list[Path]:
        return [Path(p.strip()).resolve() for p in self.allowed_roots.split(",") if p.strip()]


# Microsoft Presidio (NLP tabanli PII tespiti) entegrasyonunu yapilandiran
# ayarlar - PRESIDIO_* on ekli ortam degiskenlerinden okunur.
class PresidioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRESIDIO_", env_file=_ENV_FILE, extra="ignore")

    language: str = Field(
        "en",
        description="Presidio analiz dili. Intra deploy ortaminda PRESIDIO_LANGUAGE ile yonetilir.",
    )
    spacy_model: str | None = Field(
        "en_core_web_lg",
        description="Presidio/spaCy NLP modeli. Intra ortamda onceden kurulmus model adi veya path'i girilebilir.",
    )
    use_builtin_recognizers: bool = Field(
        True,
        description="Presidio'nun hazir taniticilarini kullanir. False ise sadece DB kaynakli pattern kurallari calisir.",
    )
    entropy_threshold: float = Field(
        3.5,
        description="PERSON/ORGANIZATION/DATE_TIME/LOCATION/NRP gibi dogal-dil kategorileri icin "
        "Shannon entropy (bit/karakter) esigi - bir bulgunun degeri bu esigin USTUNDEYSE (rastgele/"
        "yuksek-entropili gorunuyorsa) bu kategoriler o bulguya UYGULANMAZ. PRESIDIO_ENTROPY_THRESHOLD "
        "ile koda dokunmadan ayarlanabilir.",
    )
    max_analyzer_chars: int = Field(
        200_000,
        description="Presidio/spaCy analyzer'a tek seferde verilecek maksimum karakter sayisi. "
        "Buyuk dosyalar bu sinira gore parcalanir; spaCy E088 max_length hatasini ve ani RAM "
        "patlamalarini onlemek icin 1_000_000 varsayilaninin bilincli olarak altindadir.",
    )
    chunk_overlap_chars: int = Field(
        2_000,
        description="Presidio chunk'lari arasinda birakilacak karakter bindirmesi. Chunk sinirina "
        "denk gelen URL/e-posta/token gibi kisa bulgularin kacmasini azaltir.",
    )


# Alt ayar gruplarini bir araya getiren kok konfigurasyon sinifi. Tek bir duz
# (flat) ayar blobu yerine kompozisyon kullanilir: her grup (database,
# security) kendi sorumlulugunu tasir, yeni bir ayar grubu eklemek mevcut
# gruplara dokunmadan mumkun olur.
class ValidationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VALIDATION_", env_file=_ENV_FILE, extra="ignore")
    sql_dialect: str = Field(
        "", description="SQLGlot lehcesi: postgres, tsql, oracle, mysql vb.; bos ise ortak SQLGlot grameri."
    )


class Settings:
    # Her alt ayar grubunu kendi ortam degiskenlerinden okuyarak baslatir.
    def __init__(self) -> None:
        self.database = DatabaseSettings()
        self.security = SecuritySettings()
        self.vllm = VLLMSettings()
        self.presidio = PresidioSettings()
        self.web = WebSettings()
        self.validation = ValidationSettings()

    # Geriye donuk uyumluluk icin duz erisim: settings.database_url
    @property
    def database_url(self) -> str:
        return self.database.url

    # Geriye donuk uyumluluk icin duz erisim: settings.encryption_key
    @property
    def encryption_key(self) -> str:
        return self.security.encryption_key


# Tum uygulamanin kullandigi tekil (singleton) ayar nesnesi.
settings = Settings()
