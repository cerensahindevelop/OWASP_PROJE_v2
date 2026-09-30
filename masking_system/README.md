# Maskeleme Sistemi

Kurumsal projelerdeki hassas verileri (IP, e-posta, kişi/proje adı, sicil no,
parola, API anahtarı, connection string, kurum terimleri vb.) otomatik
maskeleyip projeyi dışarı aktarılabilir hale getiren, gerektiğinde maskeli
çıktıyı birebir geri dönüştüren sistem. Veritabanı olarak SQLite kullanır;
ayrı bir veritabanı sunucusu gerekmez. Mimari ve güvenlik ayrıntıları `app/`
altındaki modül docstring'lerindedir.

**Çalışma modeli:** Proje **internetli ortamda geliştirilir**, **internetsiz
kurum içi (intra) ortamda çalıştırılır**. İki ortam aynı kodu kullanır;
yalnızca `.env` ayarları farklıdır. Intra'ya kurulum, internetli ortamda
hazırlanan bir offline paketle (wheel'ler + kaynak kod) yapılır; çalışma
sırasında hiçbir adım internete çıkmaz.

```
İnternetli geliştirme ortamı                 Intra (internetsiz) ortam
───────────────────────────                  ─────────────────────────
kod + test (Ollama ile LLM)   ── offline ──▶  kurulum (install_offline.ps1)
build_offline_bundle.py          paket        alembic upgrade head
                              (USB/flash)     start.ps1 (vLLM ile LLM)
```

## İçindekiler

1. [Nasıl çalışır](#1-nasıl-çalışır)
2. [İnternetli ortam: geliştirme kurulumu](#2-i̇nternetli-ortam-geliştirme-kurulumu)
3. [İnternetli ortam: intra için offline paket hazırlama](#3-i̇nternetli-ortam-intra-için-offline-paket-hazırlama)
4. [Intra ortam: ilk kurulum](#4-intra-ortam-ilk-kurulum)
5. [Intra ortam: güncelleme](#5-intra-ortam-güncelleme)
6. [Yapılandırma (`.env`)](#6-yapılandırma-env)
7. [Çalıştırma ve web arayüzü](#7-çalıştırma-ve-web-arayüzü)
8. [Komut satırı (CLI)](#8-komut-satırı-cli)
9. [Tarama davranışı ve kapsam](#9-tarama-davranışı-ve-kapsam)
10. [Sorun giderme](#10-sorun-giderme)
11. [Testler](#11-testler)

---

## 1. Nasıl çalışır

Her metin dosyası sırayla şu adımlardan geçer:

| Adım | Ne yapar |
|---|---|
| **Katman 1 – Sözlük/regex** | DB'deki kurallar (IP, e-posta, parola ataması, kurum terimleri, öğrenilmiş kararlar). Kesin ve belirlenimlidir. |
| **Katman 2 – Presidio/spaCy** | Kişi, kurum, konum gibi doğal dil varlıkları. |
| **Kodlanmış metin kontrolü** | Base64/hex/bayt dizisiyle gizlenmiş metni çözüp Katman 1–2 ile tarar; bulgu varsa dosyayı karantinaya alır. |
| **Katman 3 – LLM tespiti** | Kuruma özgü, serbest biçimli değerler (iç kod adları, servis adları). İsteğe bağlıdır (`VLLM_ENABLED`). |
| **Maskeleme** | Değerler `mask_<tür>_<n>` yer tutucularıyla değiştirilir; eşlemeler şifreli saklanır. |
| **LLM denetimi** | Maskelenmiş dosyada açık kalan somut bir değer var mı diye ikinci kez bakar. |
| **Doğrulama** | Geri dönüş (birebir aynı dosya), sözdizimi, dosyalar arası tutarlılık ve son güvenlik taraması. |
| **Yayın** | Geçen dosyalar imzalı bütünlük kaydıyla çıktıya yazılır; geçemeyenler karantinaya/onaya gider. |

Temel güvenlik ilkeleri:

- **Taranamadı ≠ temiz.** Bir katman hata verirse, LLM yanıtı eksik/bozuksa ya da
  doğrulama geçmezse dosya dışarı verilmez; karantinaya alınır.
- **Modele değil metne güvenilir.** LLM'in bildirdiği her değer metinde birebir
  aranır; metinde geçmeyen (uydurulmuş) değer atılır.
- **İçerik loglanmaz.** Log ve uyarılarda yalnızca yol, satır, tür ve sayılar yer alır.
- **Geri alma birebirdir.** Maskeli çıktı, eşlemelerle orijinal dosyanın bayt bayt
  aynısına geri dönüştürülür; bu her export'ta otomatik doğrulanır.

---

## 2. İnternetli ortam: geliştirme kurulumu

Geliştirme makinesinde de **Python 3.14** kullanın (intra paketi bu sürüm için
hazırlanır; aynı sürümde geliştirmek sürpriz yaşamamayı sağlar).

```powershell
cd <repo>\masking_system\masking_service
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
# spaCy İngilizce modeli (Presidio için)
.venv\Scripts\pip.exe install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
```

`.env` dosyasını `masking_service` klasörünün **bir üst klasöründe**
(`masking_system\.env`) oluşturun:

```powershell
cd ..
Copy-Item .env.example .env
$key = & "masking_service\.venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
(Get-Content .env) -replace '^SECURITY_ENCRYPTION_KEY=.*', "SECURITY_ENCRYPTION_KEY=$key" | Set-Content .env
```

LLM katmanını geliştirmede denemek için [Ollama](https://ollama.com) kullanın
(OpenAI uyumlu uç nokta sunar, kod değişikliği gerekmez):

```powershell
ollama pull qwen2.5:14b
ollama serve          # varsayılan olarak 11434 portunda çalışır
```

`.env` içinde:

```
VLLM_ENABLED=true
VLLM_PROFILE=ollama-dev
VLLM_HOST=http://localhost:11434
VLLM_MODEL=qwen2.5:14b
```

LLM'siz çalışmak için `VLLM_ENABLED=false` yeterlidir. Ardından:

```powershell
cd masking_service
.\start.ps1 --migrate      # ilk seferde veritabanını da oluşturur
```

Geliştirme makinesinin `.env` dosyası ve `masking.db` veritabanı **intra'ya
taşınmaz**; her ortamın kendi anahtarı ve veritabanı vardır.

---

## 3. İnternetli ortam: intra için offline paket hazırlama

Paket, internetli makinede hazırlanır ve USB/flash bellekle taşınır. Paket
kaynak kodu, Windows/Python 3.14 wheel'lerini, spaCy modelini, sürüm kilidini,
wheel özetlerini ve testleri içerir. **Gerçek `.env`, veritabanı ve yüklenmiş
kullanıcı dosyaları pakete girmez.**

1. spaCy modeli PyPI'da olmadığı için wheel'ini bir kez
   `masking_system\deployment\wheels\` klasörüne koyun:

   ```powershell
   New-Item -ItemType Directory -Force -Path ..\deployment\wheels | Out-Null
   Invoke-WebRequest https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl `
       -OutFile ..\deployment\wheels\en_core_web_lg-3.8.0-py3-none-any.whl
   ```

2. Paketi oluşturun (`masking_service` klasöründe):

   ```powershell
   .venv\Scripts\python.exe scripts\build_offline_bundle.py --output D:\masking-paket
   ```

   Betik, hedef platform (varsayılan `win_amd64`, Python `3.14`) için tüm
   bağımlılıkları indirir, eksik bağımlılık kalmadığını doğrular ve
   `requirements.lock` ile `manifest.json` (SHA-256 özetleri) yazar.

3. Oluşan klasör düzeni:

   ```
   D:\masking-paket\
     install_offline.ps1         <- intra kurulum betiği
     verify_validation_offline.py
     manifest.json, requirements.lock
     wheels\                     <- tüm bağımlılık wheel'leri (spaCy modeli dahil)
     source\
       masking_service\          <- kaynak kod, alembic, scripts, tests, start.ps1
       .env.example
       README.md                 <- bu dosya
     README.md                   <- kısa offline kurulum kılavuzu
   ```

4. Klasörün tamamını USB/flash belleğe kopyalayın.

---

## 4. Intra ortam: ilk kurulum

Hedef makine: **Windows x64, Python 3.14**. Tüm komutlar PowerShell'dir ve
hiçbiri internete çıkmaz.

### 4.1 Ön kontrol

```powershell
python --version                                        # "Python 3.14.x" olmalı
python -c "import platform; print(platform.machine())"  # "AMD64" olmalı
```

İkisinden biri farklıysa **devam etmeyin**; paket yalnızca bu sürüm/mimari için
hazırlanmıştır.

### 4.2 Paketi yerel diske kopyalayın

```powershell
New-Item -ItemType Directory -Force -Path C:\masking | Out-Null
Copy-Item -Recurse -Force "E:\masking-paket\*" C:\masking   # E: yerine kendi sürücünüz
cd C:\masking
```

Kurulumu ve çalıştırmayı **her zaman yerel diskten** yapın. exFAT/FAT32 biçimli
USB bellekler SQLite'ın dosya kilitlemesini düzgün desteklemeyebilir (bkz.
"database is locked").

### 4.3 Bağımlılıkları kurun

```powershell
.\install_offline.ps1
```

Betik sırasıyla:
- Python sürümünü/mimarisini ve wheel SHA-256 özetlerini doğrular;
- `source\masking_service\.venv` sanal ortamını oluşturur;
- bağımlılıkları `--no-index --require-hashes` ile kurar;
- `pip check` ve ağ erişimi engellenmiş parser testini çalıştırır.

PowerShell betik çalıştırma politikası engellerse kurumunuzun onaylı yöntemini
kullanın, örneğin:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1
```

<details>
<summary>Betik kullanılamıyorsa: elle kurulum</summary>

```powershell
cd C:\masking\source\masking_service
python -m venv .venv
.venv\Scripts\pip.exe install --no-index --find-links ..\..\wheels -r requirements-intranet.txt
.venv\Scripts\pip.exe check        # "No broken requirements found." dönmeli
```
</details>

### 4.4 `.env` dosyasını hazırlayın

`.env`, `masking_service` klasörünün **bir üst klasöründe** olmalıdır
(`C:\masking\source\.env`); `app/core/config.py` dosyayı orada arar.

```powershell
cd C:\masking\source
Copy-Item .env.example .env
$key = & "masking_service\.venv\Scripts\python.exe" -c `
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
(Get-Content .env) -replace '^SECURITY_ENCRYPTION_KEY=.*', "SECURITY_ENCRYPTION_KEY=$key" | Set-Content .env
Select-String -Path .env -Pattern "^SECURITY_ENCRYPTION_KEY="   # CHANGE_ME OLMAMALI
```

Ardından `.env` içinde intra LLM sunucusunu ayarlayın (LLM kullanılmayacaksa
`VLLM_ENABLED=false`):

```
VLLM_ENABLED=true
VLLM_PROFILE=vllm-intra
VLLM_HOST=http://<vllm-sunucusu>:8000
VLLM_MODEL=<vllm'in --served-model-name değeri>
```

> **Şifreleme anahtarını (`SECURITY_ENCRYPTION_KEY`) güvenle yedekleyin ve asla
> değiştirmeyin.** Anahtar değişirse o ana kadar maskelenmiş hiçbir çıktı geri
> dönüştürülemez.

### 4.5 Veritabanını oluşturun ve başlatın

```powershell
cd C:\masking\source\masking_service
.\start.ps1 --migrate
```

`--migrate`, veritabanı şemasını ve başlangıç kural setini uygular (dosya
yoksa oluşturur). Sonraki başlatmalarda yalnızca `.\start.ps1` yeterlidir.

---

## 5. Intra ortam: güncelleme

İnternetli ortamda yapılan değişiklikleri intra'ya taşımak için:

1. İnternetli makinede yeni paketi oluşturun (bölüm 3).
2. Intra'da uygulamayı durdurun (`Ctrl+C`).
3. **Veritabanını yedekleyin:** `masking.db` (ve varsa `masking.db-wal`) dosyasını
   kopyalayın.
4. Yeni paketi yerel diske çıkarın ve kaynak kodu güncelleyin. **Mevcut `.env`
   dosyasını koruyun**; üzerine yazmayın (şifreleme anahtarı ve `DB_PATH`
   değişmemeli).
5. Bağımlılıkları güncelleyin. Mevcut kaynak dizinine kurmak için:

   ```powershell
   .\install_offline.ps1 -ServicePath C:\masking\source\masking_service
   ```

   Mevcut `.venv` üzerine kurulur; yeni bir sanal ortam gerekmez.
6. Veritabanı şemasını ve kuralları güncelleyip başlatın:

   ```powershell
   cd C:\masking\source\masking_service
   .\start.ps1 --migrate
   ```

   Kurallar (ör. parola kuralı) veritabanında tutulduğu için bu adım
   atlanırsa yeni kural davranışı devreye girmez.
7. Büyük bir projeden önce iş yükünü kontrol edin:
   `.venv\Scripts\python.exe -m app.cli llm-is-yuku --kaynak <proje>`.

Kod güncellendikten sonra açık kalan eski backend süreci eski kodla çalışmaya
devam eder; her iki süreci de yeniden başlatın. Eski işlem raporları geçmişi
gösterir; yeni davranışı görmek için orijinal projeyi yeniden tarayın.

---

## 6. Yapılandırma (`.env`)

Şablon: `.env.example` (her ayarın açıklaması içindedir). En önemli ayarlar:

### İki ortam arasındaki farklar

| Ayar | İnternetli geliştirme | Intra |
|---|---|---|
| `VLLM_PROFILE` | `ollama-dev` | `vllm-intra` |
| `VLLM_HOST` | `http://localhost:11434` | `http://<vllm-sunucusu>:8000` |
| `VLLM_MODEL` | `qwen2.5:14b` (kurulu model) | vLLM'in `--served-model-name` değeri |
| `SECURITY_ENCRYPTION_KEY` | Ortama özel | Ortama özel, **asla değiştirilmez** |
| `DB_PATH` | Ortama özel | Ortama özel |

`VLLM_PROFILE`, yalnızca `.env`'de açıkça verilmemiş `VLLM_*` ayarlarını doldurur;
tek tek verdiğiniz her değer önceliklidir. Eşzamanlılık değerlerini kendi
donanımınızda `scripts\benchmark_llm.py` ile doğrulayın. vLLM'i
`--enable-prefix-caching` ile başlatmak ilk token gecikmesini düşürür.

### Temel ayarlar

| Ayar | Varsayılan | Açıklama |
|---|---|---|
| `SECURITY_ENCRYPTION_KEY` | — (zorunlu) | Orijinal değerleri şifreleyen Fernet anahtarı. |
| `DB_PATH` | `masking.db` | SQLite dosyası (`.env`'e göre göreli). DB ve yedekleri açık metin kopyalar da içerir; erişimi kısıtlayın. |
| `VLLM_ENABLED` | `false` | LLM katmanını açar/kapatır. |
| `VLLM_MAX_CONCURRENT_REQUESTS` | `1` | Aynı anda en fazla LLM isteği. Ollama için 1. |
| `VLLM_MAX_FILE_CHARS` | `6000` | LLM'e tek seferde giden parça boyutu. |
| `VLLM_MAX_TOKENS` | `1024` (şablonda `2048`) | Yanıt token sınırı; aşılırsa parça bölünüp yeniden taranır. |
| `VLLM_DISABLE_THINKING` | `false` | Qwen3 gibi thinking modlu modellerde `true`. |
| `VLLM_AUTO_MASK_MIN_CONFIDENCE` | `orta` | Bu güven ve üstündeki LLM bulguları onaysız maskelenir. |
| `VLLM_LOW_CONFIDENCE_ACTION` | `ignore` | Eşik altı bulgular: `ignore` (yalnız kayıt) / `review` (onay kuyruğu). |
| `VLLM_REDACT_KNOWN_FINDINGS` | `true` | Katman 1'in kesin bulguları LLM'e yer tutucuyla gider. |
| `VLLM_MIN_AUTO_MASK_CHARS` | `3` | Daha kısa LLM bulguları otomatik maskelenmez. |
| `VLLM_WARN_CHUNKS_PER_FILE` | `10` | Bu kadar ya da daha fazla LLM parçası üreten dosya için uyarı. |
| `VLLM_AUDIT_UNCHANGED_FILES` | `true` | `false`: hiç değişmeyen dosyalarda ikinci LLM denetimini atlar. |
| `SCAN_ENCODED_BLOB_MIN_CHARS` | `512` | Bu uzunluktaki gömülü ikili veri LLM/Presidio'ya gitmez. `0` = kapalı. |
| `SYNTAX_FAILURE_ACTION` | `block` | Maskeleme sözdizimini bozarsa: `block` / `warn`. |
| `VALIDATION_SQL_DIALECT` | boş | SQL doğrulama lehçesi (`tsql`, `postgres`, `oracle`, …). |
| `LOCKFILE_PUBLIC_REGISTRY_HOSTS` | npm, pypi, crates… | Kilit dosyalarında izinli public registry host'ları. |
| `LEGACY_TEXT_ENCODINGS` | `cp1254,iso-8859-9` | Kodlama tespiti başarısızsa denenecek eski kodlamalar. |
| `WEB_ALLOWED_ROOTS` | boş | Web arayüzünde "Klasör Yolu" modunun izinli kökleri; boşsa yalnız yükleme modu. |
| `WEB_API_HOST` / `WEB_API_PORT` | `127.0.0.1` / `8001` | Backend adresi. |
| `WEB_API_REQUEST_TIMEOUT_SECONDS` | `300` (şablonda `3600`) | Arayüzün backend isteklerinde bekleme süresi. |

---

## 7. Çalıştırma ve web arayüzü

### Tek komutla başlatma (önerilen)

```powershell
cd <kurulum>\masking_service
.\start.ps1
```

Linux/macOS: `.venv/bin/python start.py`. Betik `.env` ayarlarını ve veritabanını
kontrol eder, backend'i başlatıp `/health` yanıt verene kadar bekler, sonra web
arayüzünü açar (`http://localhost:8501`). `Ctrl+C` ikisini birlikte kapatır.
Seçenekler: `--migrate` (önce `alembic upgrade head`), `--ui-port 8502`.

<details>
<summary>Süreçleri elle başlatma (iki ayrı PowerShell penceresi)</summary>

Pencere 1 – backend:

```powershell
cd <kurulum>\masking_service
.venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001
```

`Uvicorn running on http://127.0.0.1:8001` satırını bekleyin.

Pencere 2 – web arayüzü:

```powershell
cd <kurulum>\masking_service
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Kontrol: `Invoke-RestMethod http://127.0.0.1:8001/health` → `status: ok`.
Durdurmak için her iki pencerede `Ctrl+C`.

`.venv\Scripts\python.exe` tam yolla çağrıldığında `Activate.ps1` gerekmez
(execution policy sorununu da atlar).
</details>

### Web arayüzü ekranları

Kimlik girişinden sonra (proje + sicil + branch) sol menüde:

- **Dışarı Çıkar** – proje klasörünü/dosyasını maskeleyip dışa aktarır. Arka
  plan işi olarak çalışır, işlenen/toplam dosya ilerlemesini gösterir.
- **Onay Bekleyenler** – düşük güvenli bulgular, güvenlik karantinası ve
  doğrulama hataları. Karantinadaki dosya için "Maskele", "Yanlış Alarm" ya da
  "Düzenle" kararı verilir; serbest bırakılan dosya son kontrollerden geçip
  çıktıdaki maskeli yola yazılır.
- **Geri Al** – maskeli çıktıyı `proje + sicil + branch` ile gerçek değerlere
  geri dönüştürür.
- **Geçmiş İşlemler** – önceki export/geri alma işlemleri ve bulgu özetleri.
- **Kurumsal Terim Sözlüğü** – kurum içi terim listesini (.xlsx) önizleme/onay
  sonrası kural olarak kaydeder (XML entity-expansion sertleştirmesiyle).

> Kimlik, parola içermeyen `proje + sicil + branch` üçlüsüdür ve backend yalnızca
> `127.0.0.1`'de dinler. Tek kullanıcılı masaüstü kullanımı için tasarlanmıştır;
> paylaşılan bir sunucuya taşınacaksa geri alma için yetkilendirme eklenmelidir.

---

## 8. Komut satırı (CLI)

Komutlar `masking_service` klasöründe `.venv\Scripts\python.exe -m app.cli ...`
biçiminde çalıştırılır (venv etkinse `python -m app.cli ...`).

```powershell
# Export öncesi LLM iş yükü tahmini (LLM'e istek göndermez)
python -m app.cli llm-is-yuku --kaynak .\proje --istek-suresi 12

# Maskele + dışarı aktar
python -m app.cli export --kaynak .\proje --hedef D:\proje-masked `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# Geri dönüştür
python -m app.cli unmask --kaynak D:\proje-masked --hedef .\proje-geri `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# Yeni filtre kuralı ekle (kod değişikliği/deploy gerekmez)
python -m app.cli kural-ekle --tip tc_kimlik_no --pattern '\b\d{11}\b' `
    --placeholder-format 'TC_TEST_{sayac}' --olusturan EMP-1001

# Kuralları listele / aktif-pasif et (silmez, geçmiş eşlemeler bozulmaz)
python -m app.cli kural-listele --sadece-aktif
python -m app.cli kural-aktif tc_kimlik_no
python -m app.cli kural-pasif tc_kimlik_no

# Raporlar
python -m app.cli rapor-son-islem --proje Poseidon
python -m app.cli rapor-gecmis --proje Poseidon --limit 20
python -m app.cli rapor-detay --run-id 7

# Export/geri alma sırasında durdurulduysa yarım kalan hedef yazımını kurtar
python -m app.cli recover-output --hedef D:\proje-masked
```

### İşlem numarası (JOB ID) ve geri alma

- Her export yeni bir işlemdir ve kendi **JOB ID**'sine sahiptir. Yer tutucu
  sayaçları her işlemde 1'den başlar; aynı değer bir işlemin tüm dosyalarında
  aynı yer tutucuyu alır. Eşlemeler `JOB ID + yer tutucu` ile seçilir.
- Geri alırken JOB ID, paketteki `.masking-integrity.json` dosyasından imzası
  doğrulanarak otomatik okunur. **Bu dosyayı paketle birlikte koruyun.**
- Tek dosya geri alırken ya da bu kayıt yoksa JOB ID'yi elle verin: arayüzde
  "Kaynak maskeleme işlem numarası (JOB ID)" alanı, API'de `job_id`, CLI'da
  `--job-id 123`. Kimlik eksikse sistem işlem tahmin etmez.
- Aynı `proje + sicil + branch` ile yeniden export, hedef klasörün önceki
  içeriğinin üzerine yazar.

### Kurumsal terimler

Terim yüklerken girilen **Başlık / Proje Adı** yalnızca uygulama içi gruplama
içindir; terimler çıktıda `mask_kurumsal_ifade_<n>` olarak maskelenir ve başlık
yer tutucuya girmez. Eski yer tutucu eşlemeleri geçmiş çıktıları geri alabilmek
için korunur; eski çıktıları düzeltmek için orijinal kaynaktan yeniden export edin.

---

## 9. Tarama davranışı ve kapsam

### Hangi dosyalar, nasıl taranır

| Dosya | Davranış |
|---|---|
| Desteklenen metin dosyaları | Tüm katmanlar; LLM açıksa tespit + maskeleme sonrası denetim. Boş içerik LLM'e gönderilmez. |
| `.git`, `.hg`, `.svn`, `node_modules`, `dist`, `build`, `target`, `obj`, `.vs`, `.gradle`, `bower_components`, venv/önbellek dizinleri | Tarama başında elenir, çıktıya alınmaz. |
| Kilit dosyaları (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `Pipfile.lock`, `*.lock`, `*.lockfile`, `packages.lock.json`, `project.assets.json`, `npm-shrinkwrap.json`, `go.sum`, `Package.resolved`) | **LLM'e gitmez**; yerel katmanlarla taranır. Temizse bayt bayt kopyalanır. Paket özetleri ve izinli public registry URL'leri bulgu sayılmaz; kalan bulgular yalnızca iç registry URL'lerindeyse URL'ler maskelenir, aksi halde dosya karantinaya gider. |
| Binary/Office/PDF/resim | Desteklenmeyen içerik olarak raporlanır; taranmaz, çıktıya alınmaz. İşlem "uyarılı tamamlandı" olur. |
| Arşivler (`.zip`, `.tar`, `.gz`, `.rar`, `.7z`) | Açılmaz; güvenlik karantinasına gider. |
| Java `.class` | Özel sabit havuzu işleyicisiyle taranır (aşağıya bakın). |

Karar dosya adına değil içeriğe dayanır: içinde metin olan bilinmeyen uzantılar
taranır; metin olarak tanınamayan içerik desteklenmeyen içerik sayılır. Boyut,
kodlama, sözdizimi ve geri dönüş hataları teknik doğrulama hatası olarak
engellenir.

### Parola / sır ataması kuralı

`generic_secret_assignment` (Katman 1, LLM'den bağımsız) şu anahtarların
değerini maskeler: `password`, `passwd`, `passphrase`, `pwd`, `pass`, `secret`,
`token`, `api_key`, `credential(s)`, `parola`, `şifre`/`sifre` (önekli/sonekli
biçimleri, JSON anahtarları ve connection string alanları dahil). Değer en az
6 karakter olmalıdır.

Parola sayılmayanlar:
- ortam değişkeni ve şablon referansları (`${DB_PASS}`, `$DB_PASS`, `%PASSWORD%`,
  `{{ vault.pw }}`);
- `null`, `changeme`, `******` gibi yer tutucular; `{token}`, `%s` biçim yer
  tutucuları;
- ölçü/ayar anahtarları (`token_count`, `password_min_length`, `token_url`,
  `password_file`);
- tırnaksız kod ifadeleri (`token = tokenizer`, `token: Optional`,
  `secret_key = s3_connection`).

Bilinen sınırlar: tırnaksız ve yalnızca harften oluşan parola (`pwd=sunshine`)
ile kodda parametre olarak geçen parola (`new NetworkCredential("sa", "…")`) bu
kuralla yakalanmaz, LLM katmanına kalır. Kural veritabanında tutulur;
güncellemeden sonra `alembic upgrade head` gerekir.

### Kodlanmış veri

- **Gömülü ikili veri LLM'e ve Presidio'ya gitmez.** Dosya türünden bağımsız,
  içeriğe göre tanınan biçimler:
  - satır satır base64/hex (`.resx`, PEM, MIME);
  - tırnaklı/birleştirilmiş base64 (C#/Java/JS sabitleri, `.ipynb` çıktıları);
  - base64url ve data URI (HTML/CSS/SVG);
  - bayt dizileri (`0x89, 0x50, …`, `byte[] {…}`, `\x89\x50…`).

  `SCAN_ENCODED_BLOB_MIN_CHARS` (512) ve üstü bloklar çözülüp sınıflandırılır.
  Gerçekten ikili veri olanlar LLM'e `mask_kodlanmis_ikili_veri_<n>` yer
  tutucusuyla, Presidio'ya boşluk olarak gider. Katman 1 bu blokları yine tarar
  ve çıktı metni değişmez. Log: `llm_input_encoded_blobs ... blobs=N hidden_chars=M`.
- **Kodlanmış metindeki sırlar karantinaya alınır.** Base64/base64url/hex ya da
  bayt dizisiyle kodlanmış ve çözüldüğünde okunabilir metin veren değerler
  çözülüp Katman 1–2 ile taranır; parola ataması ve `kullanıcı:parola` biçimi
  ayrıca aranır. Örnekler: `appsettings.json` içinde base64 connection string,
  `c2E6UGFzc3cwcmQ=` (= `sa:Passw0rd`). Bulgu varsa dosya **Güvenlik
  Karantinası**'na alınır ve çıktıya yazılmaz. Değer kodlanmış blok içinde
  maskelenmez; böylece geri alma birebir kalır. Gerekçede yalnızca satır,
  kodlama türü ve genel bulgu türü görünür. Değeri kaynakta kaldırıp yeniden
  tarayın ya da gerçekten hassas değilse "Yanlış Alarm" ile serbest bırakın.

### LLM katmanı

- **Onay politikası:** `VLLM_AUTO_MASK_MIN_CONFIDENCE` ve üstündeki bulgular onaysız
  maskelenir; altındakiler `VLLM_LOW_CONFIDENCE_ACTION`'a göre kayda yazılır ya da
  onaya gider. LLM'in serbest yazdığı bulgu türü sabit bir listeye eşlenir;
  böylece yer tutucu adına hassas bir terim girmez.
- **Yanıt bütünlüğü:** Yanıtın yapısı (JSON / `bulgular` listesi) bozuksa parça hata
  sayılır ve dosya karantinaya alınır. Yapı sağlam ama tek bir bulgu bozuksa, değer
  metinde birebir geçiyorsa bulgu `orta` güven ve `KURUMSAL_TANIMLAYICI` türüyle
  maskelenir; geçmiyorsa yalnızca o bulgu atılır. Kayıt:
  `llm_bulgu_semasi_bozuk onarilan=N atilan=M`.
- **Kesilen yanıt:** `max_tokens`'ta kesilen parça ikiye bölünüp yeniden taranır
  (en fazla 3 kez); yine kesilirse dosya karantinaya gider.
- **Maskeleme sonrası denetim:** Dosya yalnızca, maskelenmemiş somut bir değeri
  metinde birebir geçen bir alıntıyla gösterirse karantinaya alınır;
  doğrulanamayan "risk var" yanıtları dosyayı bekletmez.
- **Bilinen değerler modele gösterilmez:** Katman 1'in kesin bulguları LLM'e yer
  tutucuyla gider. Model bunları tekrar listelemez; çıktı token'ı ve kesilme
  azalır. Presidio bulguları gizlenmez.
- **Kelime ortası eşleşme yok:** LLM değeri yalnızca kelime/identifier sınırında
  eşlenir (`PoseidonGatewayClient` içindeki `Poseidon` eşlenir, `Alignment`
  içindeki `Ali` eşlenmez). `VLLM_MIN_AUTO_MASK_CHARS`'tan kısa değerler `dusuk`
  güvene iner.
- **Dosya bağlamı:** Sistem promptunun sonuna yalnızca dosya adı ve uzantısı
  eklenir; dizin yolu gönderilmez.
- **Eşzamanlılık:** Tespit ve denetim bir dosyanın parçalarını eş zamanlı gönderir.
  Bir sonraki dosya grubunun tespiti, mevcut grubun denetimiyle aynı anda yürür.
  Toplam istek `VLLM_MAX_CONCURRENT_REQUESTS` ile sınırlıdır. Export sırasında
  veritabanı yazma kilidi LLM çağrıları boyunca tutulmaz. Presidio/spaCy analizi
  ayrı bir thread'de çalışır.
- **Erken uyarı:** `VLLM_WARN_CHUNKS_PER_FILE` ya da daha fazla parçaya bölünen,
  veya gizlenemeyen kodlanmış-veri benzeri satır içeren dosya için işlem kaydına
  ve loga `llm_is_yuku_yuksek parca=… taninmayan_kodlanmis_satir=…` yazılır.
  Tanınmayan yeni bir dosya biçimi böylece ilk dosyada görünür.

### Tutarlılık, doğrulama ve yayın

- Tutarlılık adımı, aynı işlemin diğer dosyalarında maskelenen değerlerin açık
  kalan geçişlerini, açık geçiş kalmayana kadar en fazla 3 tur değiştirir. Sonra
  geri dönüş, sözdizimi ve son güvenlik taraması çalışır; hâlâ açık geçiş varsa
  dosya dışa aktarılmaz.
- Geri dönüş doğrulaması, geri çözülen içeriğin orijinalle birebir aynı olmasını
  esas alır. Kaynakta zaten bulunan ve yer tutucuya benzeyen bir sabit, içerik
  aynen geri elde ediliyorsa eksik eşleme sayılmaz. Gerçekten yeni üretilmiş bir
  yer tutucunun eşlemesi eksik/yanlışsa kontrol başarısız olur.
- Onaydan sonra serbest bırakılan dosyalar çıktıdaki maskeli yola yazılır,
  tutarlılık kontrolünden geçer ve imzalı bütünlük kaydına eklenir.
- Uygulama bir export sırasında kapanırsa işlem `recover-output` ile kapatılır.

### Java `.class` dosyaları

Hariç tutulmayan `.class` dosyaları özel sabit havuzu işleyicisiyle taranır.
String sabitleri, anotasyon metinleri ve kaynak dosya adındaki hassas değerler
maskelenir; `ConstantValue` alan adları (`PASSWORD` gibi) bağlam olarak korunur.
Çıktı yine ikili `.class` dosyasıdır. İndirmeden önce gerçek eşlemelerle yeniden
oluşturulan dosyanın bayt özeti orijinalle karşılaştırılır. Java/JDK veya internet
gerekmez; sınıf yüklenmez ya da çalıştırılmaz.

Kapsam yalnızca metin sabitleridir. Sayısal sabitler, çalışma anında üretilen
metinler ve şifreli veriler kapsam dışıdır. Sınıf/metot adı gibi yapısal alanları
değiştirmeyi gerektiren bulgular, bilinmeyen attribute'lar,
`SourceDebugExtension`, bozuk ya da desteklenmeyen sürüm açıklamalı hata verir.
Karantinadaki class dosyası karar sonrası orijinal projeden yeniden taranmalıdır.
Proje seviyesinde çalışma davranışı garanti edilmez. Referans:
[JVMS bölüm 4](https://docs.oracle.com/javase/specs/jvms/se25/html/jvms-4.html).

---

## 10. Sorun giderme

**"database is locked" (özellikle export sırasında)**
- Yalnızca **tek** backend süreci çalıştığından emin olun
  (`tasklist | findstr python`).
- Kurulum klasörünü antivirüs gerçek zamanlı taramasından ve OneDrive/kurumsal
  bulut senkronizasyonundan hariç tutun.
- `masking.db`'yi DB Browser gibi bir araçla açık bırakmayın.
- USB bellekten değil yerel diskten çalıştırın.
- Büyük kalmış bir `masking.db-wal` varsa tüm python süreçlerini düzgünce
  kapatıp yeniden başlatın; SQLite açılışta WAL'ı birleştirir.

**Backend ayakta ama istekler hata veriyor ("no such table")**
`/health` şemayı kontrol etmez. Migration atlanmıştır:
`.venv\Scripts\python.exe -m alembic upgrade head` (ya da `.\start.ps1 --migrate`).

**Kurulumda paket bulunamıyor / sürüm hatası**
Hedef makinenin Python sürümü/mimarisi paketin hazırlandığı hedeften
(3.14 / win_amd64) farklıdır. Doğru Python'u kurun ya da paketi o hedef için
yeniden hazırlayın (`build_offline_bundle.py --python-version ... --platform ...`).

**Logda "spaCy NLP modeli yüklenemedi"**
`en-core-web-lg` kurulu değil. Presidio'nun kişi/kurum/konum tespiti o çalışma
boyunca devre dışı kalır. Offline paketin `deployment\wheels` içindeki model
wheel'iyle hazırlandığını kontrol edin (bölüm 3, adım 1).

**Export çok yavaş**
- `python -m app.cli llm-is-yuku --kaynak <proje>` ile en ağır dosyaları görün.
- Logda `llm_is_yuku_yuksek` satırlarına bakın.
- Gereksiz ağır dosyaları (minified kütüphaneler vb.) hariç tutma kuralıyla ayırın.
- vLLM'de `VLLM_MAX_CONCURRENT_REQUESTS`'i benchmark ile artırın.

**LLM yanıtı kesiliyor (`finish_reason=length`)**
Hata mesajı nedeni söyler: thinking modu için `VLLM_DISABLE_THINKING=true`
(vLLM'de `--default-chat-template-kwargs '{"enable_thinking": false}'`); tekrar
döngüsü için `VLLM_PRESENCE_PENALTY=1.5`.

**Dosya "Kodlanmış … hassas veri" gerekçesiyle karantinada**
Base64/hex ile kodlanmış bir değerin içinde parola/IP/kurum terimi bulunmuştur
(bkz. bölüm 9). Değeri kaynakta kaldırıp yeniden tarayın ya da hassas değilse
"Yanlış Alarm" ile serbest bırakın.

---

## 11. Testler

Testler geçici, yeni migrate edilmiş bir veritabanında ve LLM kapalıyken
çalışır; proje veritabanına dokunmaz. İnternet gerekmez.

```powershell
cd <kurulum>\masking_service
.venv\Scripts\python.exe scripts\run_tests_isolated.py -q
```

Intra kurulumundan sonra bir kez çalıştırmanız önerilir. Ağ erişimi
engellenmiş parser testi `install_offline.ps1` tarafından ayrıca çalıştırılır.
