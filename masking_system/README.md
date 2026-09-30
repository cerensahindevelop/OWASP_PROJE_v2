# Maskeleme Sistemi

Hassas kurumsal verileri (IP, e-posta, proje adı, sicil no, secret/API key vb.)
otomatik maskeleyen, ihtiyaç halinde geri dönüştürülebilir hale getiren sistem.
SQLite kullanır - ayrı bir DB sunucusu gerekmez. Detaylı mimari/güvenlik
notları için `app/` altındaki modül docstring'lerine bakın.

## Parola / sır ataması kuralı

`generic_secret_assignment` (sözlük/regex katmanı, LLM'den bağımsız) şu
anahtarların değerini maskeler: `password`, `passwd`, `passphrase`, `pwd`,
`pass`, `secret`, `token`, `api_key`, `credential(s)`, `parola`, `şifre`/`sifre`
(önekli/sonekli biçimleri, JSON anahtarları ve connection string alanları dahil).
Değer en az 6 karakter olmalıdır. Parola sayılmayanlar: ortam değişkeni ve şablon
referansları (`${DB_PASS}`, `$DB_PASS`, `%PASSWORD%`, `{{ vault.pw }}`),
`null`/`changeme`/`******` gibi yer tutucular, `{token}`/`%s` biçim yer tutucuları,
ölçü/ayar anahtarları (`token_count`, `password_min_length`, `token_url`,
`password_file`) ve tırnaksız kod ifadeleri (`token = tokenizer`,
`token: Optional`, `secret_key = s3_connection`). Bilinen sınırlar: tırnaksız ve
yalnızca harften oluşan parola (`pwd=sunshine`) ile kodda parametre olarak geçen
parola (`new NetworkCredential("sa", "…")`) bu kuralla yakalanmaz, LLM
katmanına kalır. Kural `alembic upgrade head` ile güncellenir.

## LLM tarama kapsamı

`VLLM_ENABLED=true` olduğunda, desteklenen tüm metin dosyaları sözlük/regex,
Presidio ve LLM ile taranır. İlk taramada bulgu çıkmayan metinler de maskeleme
sonrası LLM denetiminden geçer. Boş içerik için modele istek gönderilmez.

`.git` (worktree işaretçi dosyası dahil), `.hg`, `.svn`, bağımlılık/önbellek
ve derleme dizinleri tarama başında elenir. Desteklenmeyen binary/Office/PDF
ve arşiv içerikleri LLM'e gönderilmez ve çıktıya alınmaz. Lock dosyaları
bütün katmanlarla taranır; temizse aynen kopyalanır, hassas bulgu varsa
karantinaya alınır. Sözdizimi, geri dönüş ve tutarlılık kontrolleri sürer.

LLM bulgularının onay politikası: `VLLM_AUTO_MASK_MIN_CONFIDENCE` (varsayılan
`orta`) ve üstündeki bulgular onay beklemeden maskelenir; altındakiler
`VLLM_LOW_CONFIDENCE_ACTION=ignore` ile yalnızca işlem kaydına yazılır
(`review` ile onay kuyruğuna gider). Maskeleme sonrası LLM denetimi bir dosyayı
yalnızca maskelenmemiş somut bir değeri metinde birebir geçen bir alıntıyla
gösterirse karantinaya alır; doğrulanamayan "risk var" yanıtları dosyayı
bekletmez. LLM'in serbest yazdığı bulgu türü sabit bir listeye eşlenir, böylece
yer tutucu adına hassas bir terim girmez.

Modelin tespit yanıtının yapısı (JSON / `bulgular` listesi) bozuksa o metin
parçası hata sayılır ve dosya karantinaya alınır. Yapı sağlam ama tek bir bulgu
bozuksa (boş ya da yanlış türde `tip`, geçersiz `guven_seviyesi`, eksik
`gerekce`) parça düşürülmez: bulunan değer metinde birebir geçiyorsa bulgu `orta`
güven ve `KURUMSAL_TANIMLAYICI` türüyle maskelenir, geçmiyorsa ya da değer hiç
okunamıyorsa yalnızca o bulgu atılır. Onarılan/atılan sayıları işlem kaydına
değer yazılmadan düşer (`llm_bulgu_semasi_bozuk onarilan=N atilan=M`).

Tutarlılık adımı, aynı işlemin diğer dosyalarında maskelenen değerlerin açık
kalan geçişlerini, açık geçiş kalmayana kadar en fazla 3 tur değiştirir. Sonra
geri dönüş ve sözdizimi kontrolleri ile final güvenlik taraması çalışır; hâlâ
açık geçiş varsa dosya dışa aktarılmaz.

Onaydan sonra serbest bırakılan dosyalar çıktıdaki maskelenmiş yola yazılır,
aynı işlemin diğer dosyalarında maskelenen değerlere karşı tutarlılık
kontrolünden geçer ve imzalı bütünlük kaydına eklenir.

Karantinadaki bir dosya için "Yanlış Alarm" ya da "Maskele" seçildiğinde son
LLM denetimi veritabanı yazma kilidi tutulmadan çalışır; bu sırada diğer
export'lar beklemez. Dosya ve bütünlük kaydı ancak karar veritabanına
kaydedilirse kalır; kayıt başarısız olursa geri alınır. Doğrulamadan geçemeyen
bir karar kalıcı hale gelmez (yalnızca işlem kaydına yazılır). "Yanlış Alarm"
denetimin gösterdiği tüm değerleri kapsar. Son denetim aynı değeri daha kısa
alıntılasa da (ör. önce "Hakan Yılmaz", sonra "Hakan") bu değerin dosyadaki
tüm geçişleri kararın kapsamındaysa bastırılır. Kaynakta zaten bulunan bir
sözdizimi hatası (ör. yorumlu JSON) serbest bırakmayı engellemez. Kurumsal
terim karantinasında inceleyen kişi açık değeri görür; terim sözlükten sonradan
silinse bile değer export anındaki gerekçeden gösterilir. Bütünlük kaydı
(manifest) güncellemesi işletim sistemi dosya kilidiyle korunur; backend birden
fazla worker/süreçle çalıştırılabilir. Kilit dosyası çıktı klasörünün yanında
(`.<klasör>.masking-manifest.lock`) durur, indirilen çıktıya girmez; 30 saniyede
alınamazsa işlem hata verir ve tekrar denenebilir. İnceleme kararlarından
sonraki son denetim, karar işleminin içinde çalıştığı için yazma kilidini
tutmaya devam eder.

Export web arayüzünde arka plan işi olarak çalışır; ekran işlenen/toplam dosya
ilerlemesini gösterir. Export sırasında veritabanı yazma kilidi LLM
çağrıları boyunca tutulmaz; aynı anda başka projelerin export'ları ve onay
işlemleri beklemeden çalışabilir. Presidio/spaCy analizi olay döngüsünü
kilitlememek için ayrı bir thread'de (aynı işlemin dosyaları arasında sırayla)
çalışır; böylece eşzamanlı LLM isteklerinde sahte zaman aşımı oluşmaz.
Uygulama bir export sırasında kapanırsa
işlem `recover-output` ile kapatılır (bkz. 4. bölüm).

Yereldeki model ile intradaki Qwen modeli farklı olabilir. Her ortamın
`VLLM_HOST` ve `VLLM_MODEL` değerlerini kendi sunucusunun sunduğu adla ayarlayın.

### LLM verimliliği ve hız

- **Bilinen değerler modele gösterilmez.** Katman 1'in (sözlük/regex) kesin
  bulguları LLM'e `mask_<tür>_<n>` biçimli geçici yer tutucularla gider; model
  bunları tekrar listelemez, çıktı token'ı ve yanıt kesilmesi azalır. Çıktıdaki
  maskeleme her zaman orijinal metin üzerinden yapılır. Kapatmak için
  `VLLM_REDACT_KNOWN_FINDINGS=false`. Presidio bulguları gizlenmez.
- **Gömülü ikili veri LLM'e ve Presidio'ya gitmez.** Dosya türünden bağımsız,
  içeriğe göre tanınan biçimler: satır satır base64/hex (`.resx`, PEM, MIME),
  tırnaklı/birleştirilmiş base64 (C#/Java/JS sabitleri, `.ipynb` çıktıları),
  base64url, data URI (HTML/CSS/SVG), bayt dizileri (`0x89, 0x50, …`,
  `byte[] {…}`, `\x89\x50…`). `SCAN_ENCODED_BLOB_MIN_CHARS` (varsayılan 512) ve
  üstü uzunluktaki bloklar çözülerek sınıflandırılır; gerçekten ikili veri
  olanlar LLM'e `mask_kodlanmis_ikili_veri_<n>` yer tutucusuyla gider,
  Presidio'ya boşluk olarak girer. Okunabilir metne çözülen base64 (ör. base64
  ile gizlenmiş bir config) ve identifier/yol listeleri gönderilmeye devam eder.
  Katman 1 (sözlük/regex) blokları yine tarar, çıktı metni değişmez. Log satırı:
  `llm_input_encoded_blobs ... blobs=N hidden_chars=M`. Kapatmak için `0`.
- **Kodlanmış metindeki sırlar karantinaya alınır.** Base64/base64url/hex ya da
  bayt dizisiyle kodlanmış ve çözüldüğünde okunabilir metin veren değerler
  (ör. `appsettings.json` içinde base64 connection string, `c2E6UGFzc3cwcmQ=`
  = `sa:Passw0rd`) çözülüp sözlük/regex ve Presidio ile ayrıca taranır; parola
  ataması ve `kullanıcı:parola` biçimi ayrıca aranır. Bulgu varsa dosya
  **Güvenlik Karantinası**'na alınır, çıktıya yazılmaz; değer kodlanmış blok
  içinde maskelenmez (geri alma birebir aynı dosyayı üretmeye devam eder).
  Gerekçede yalnızca satır, kodlama türü ve genel bulgu türü görünür.
  İnceleme ekranında değeri kaynakta kaldırıp yeniden tarayın ya da gerçekten
  hassas değilse "Yanlış Alarm" ile serbest bırakın.
- **Erken uyarı.** Bir dosya `VLLM_WARN_CHUNKS_PER_FILE` (varsayılan 10) ya da
  daha fazla LLM parçasına bölünüyorsa veya gizlenemeyen kodlanmış-veri benzeri
  satırlar içeriyorsa işlem kaydına ve loga `llm_is_yuku_yuksek parca=…
  taninmayan_kodlanmis_satir=…` uyarısı yazılır (içerik yazılmaz). Böylece
  tanınmayan yeni bir dosya biçimi saatler sonra değil ilk dosyada görünür.
- **Export öncesi tahmin.** `python -m app.cli llm-is-yuku --kaynak <proje>
  [--istek-suresi 12]` LLM'e hiç istek göndermeden dosya başına tahmini istek
  sayısını, gizlenecek ikili veriyi ve uyarıları (tanınmayan kodlanmış veri,
  minified kod) listeler. Ağır ama gereksiz dosyalar hariç tutma kuralıyla
  ayrılabilir.
- **Üretilmiş dosyalar.** `obj/`, `.vs/`, `.gradle/`, `bower_components/`
  dizinleri taranmaz; `packages.lock.json`, `project.assets.json`,
  `npm-shrinkwrap.json`, `go.sum`, `Package.resolved`, `*.lockfile` kilit dosyası
  sayılır (LLM'e gitmez, yerel katmanlarla taranır).
- **Kelime ortası eşleşme yok.** LLM'in bildirdiği değer yalnızca kelime/identifier
  sınırında eşlenir (`PoseidonGatewayClient` içindeki `Poseidon` eşlenir,
  `Alignment` içindeki `Ali` eşlenmez). `VLLM_MIN_AUTO_MASK_CHARS` (varsayılan 3)
  altındaki değerler otomatik maskelenmez, `dusuk` güvenle
  `VLLM_LOW_CONFIDENCE_ACTION` kuralına düşer.
- **Dosya bağlamı.** Sistem promptunun sonuna yalnızca dosya adı ve uzantısı
  eklenir (dizin yolu gönderilmez).
- **Eşzamanlılık.** Tespit ve denetim adımları bir dosyanın parçalarını eş zamanlı
  gönderir; bir sonraki dosya grubunun tespiti, mevcut grubun denetimiyle aynı
  anda yürür. Toplam LLM isteği yine `VLLM_MAX_CONCURRENT_REQUESTS` ile sınırlıdır.
- **İsteğe bağlı denetim atlama.** `VLLM_AUDIT_UNCHANGED_FILES=false` iken hiçbir
  katmanın değiştirmediği ve LLM tespiti hatasız biten dosyalar ikinci LLM
  denetimine gönderilmez. Hız kazancı büyüktür ama ikinci bağımsız kontrol
  kalkar; varsayılan `true`.
- **Hazır profiller.** `VLLM_PROFILE=ollama-dev` ya da `vllm-intra`, açıkça
  verilmemiş `VLLM_*` ayarlarını doldurur (tek tek verilen değer her zaman
  önceliklidir). Eşzamanlılık değerlerini `scripts/benchmark_llm.py` ile doğrulayın.
- **vLLM prefix caching.** Sistem promptu her istekte aynı önekle başlar; vLLM'i
  `--enable-prefix-caching` ile başlatmak ilk token gecikmesini düşürür.

## 1. İnternetsiz (offline/intra) ortamda kurulum — adım adım (PowerShell)

Bu bölüm, hiçbir adımda internete çıkmadan, önceden hazırlanmış bir **offline
kurulum paketini** (flash bellek/USB ile taşınan; projenin gerçek klasör
yapısıyla birebir aynı — `masking_service\` doğrudan üst seviyede, yanında
ayrı bir `wheels\` klasörü) hedef Windows makinede sıfırdan, sadece standart
`pip` komutlarıyla kurmayı anlatır. Tüm komutlar PowerShell'dir.

Paket düzeni (`masking\` = flash bellekteki üst klasör):

```
masking\
  masking_service\        <- proje kaynak kodu (app, alembic, scripts, ...)
  wheels\                 <- tum bagimliliklarin Windows/Python 3.14 wheel'leri
  .env.example
  README.md                <- bu dosya
```

### 1) Ön kontrol: Python sürümü ve mimarisi

Hedef (internetsiz) makinede, sırayla çalıştırın:

```powershell
# Kurulu Python surumunu goster - "Python 3.14.x" DONMELI
python --version
```

```powershell
# CPU mimarisini goster - "AMD64" DONMELI
python -c "import platform; print(platform.machine())"
```

İkisinden biri farklıysa **devam etmeyin** — `wheels\` klasörü yalnızca bu
sürüm/mimari için hazırlandı, başka bir sürümde kurulum paket paket başarısız
olur.

### 2) Paketi flash bellekten yerel diske kopyala

```powershell
# Hedef klasoru olustur (zaten varsa hata vermez)
New-Item -ItemType Directory -Force -Path C:\masking | Out-Null
```

```powershell
# Paketin tamamini flash bellekten yerel diske kopyala
# "E:\" harfini kendi flash belleginizinkiyle degistirin (Get-Volume ile kontrol edebilirsiniz)
Copy-Item -Recurse -Force "E:\masking\*" C:\masking
```

```powershell
# Calisma dizinini yerel kopyaya gecir
cd C:\masking
```

```powershell
# Kopyanin eksiksiz geldigini dogrula - asagidaki 4 ogeyi gormelisiniz:
# masking_service, wheels, .env.example, README.md
Get-ChildItem
```

Kurulumu **her zaman yerel diskten** yapın, flash bellek/USB üzerinde
doğrudan çalıştırmayın — "Sorun giderme" bölümündeki "database is locked"
notuna bakın (exFAT/FAT32 SQLite'ın ihtiyaç duyduğu dosya kilitlemeyi düzgün
desteklemeyebilir).

### 3) Sanal ortamı oluştur ve bağımlılıkları kur

```powershell
# masking_service klasorune gec
cd masking_service
```

```powershell
# Bos bir sanal ortam (venv) olustur - disaridan hicbir paket getirmez
python -m venv .venv
```

```powershell
# venv'in gercekten olustugunu dogrula - "True" DONMELI
Test-Path .venv\Scripts\python.exe
```

```powershell
# Tum bagimliliklari internete cikmadan, sadece yanindaki wheels/ klasorunden kur
# --no-index: PyPI'a hic baglanma; --find-links: paketleri bu klasorden bul
.venv\Scripts\pip.exe install --no-index --find-links ..\wheels -r requirements-intranet.txt
```

```powershell
# Kurulan paketler arasinda surum celiskisi olmadigini dogrula - "No broken requirements found." DONMELI
.venv\Scripts\pip.exe check
```

```powershell
# Bir ust klasore geri don (4. adim icin gerekecek)
cd ..
```

PowerShell script çalıştırma politikası (`python -m venv` bir script değil,
gerçek bir program olduğu için genelde sorun çıkarmaz) engel olursa,
kurumunuzun onaylı yöntemiyle tek seferlik izin verin:

```powershell
powershell -ExecutionPolicy Bypass -Command "python -m venv masking_service\.venv"
```

Mevcut bir kurulumu (aynı makinede önceki bir sürümü) güncelliyorsanız, yeni
bir `.venv` açmayın — mevcut `masking_service\.venv` içindeyken sadece kurulum
komutunu tekrar çalıştırmanız yeterlidir (üzerine yazar, bozmaz):

```powershell
cd masking_service
.venv\Scripts\pip.exe install --no-index --find-links ..\wheels -r requirements-intranet.txt
cd ..
```

### 4) `.env` dosyasını hazırla

`.env` dosyası `masking_service\`'in İÇİNDE DEĞİL, onunla aynı seviyede
(`masking\.env`) olmalıdır — `app/core/config.py` dosyayı repo kökünde arar.

**Yeni kurulum** ise (mevcut bir kurulumu güncelliyorsanız bu adımı tamamen
atlayıp 5. adıma geçin — mevcut `.env`'i asla üzerine yazmayın):

```powershell
# Sablon dosyayi gercek .env olarak kopyala
Copy-Item .env.example .env
```

```powershell
# Yeni bir sifreleme anahtari uret ve degiskene ata
# (bu anahtar olmadan uygulama acilmaz, varsayilani yoktur)
$key = & "masking_service\.venv\Scripts\python.exe" -c `
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```powershell
# Uretilen anahtari .env icindeki SECURITY_ENCRYPTION_KEY satirina yaz
(Get-Content .env) -replace '^SECURITY_ENCRYPTION_KEY=.*', "SECURITY_ENCRYPTION_KEY=$key" |
    Set-Content .env
```

```powershell
# Anahtarin gercekten yazildigini dogrula - CHANGE_ME DEGIL, uzun rastgele bir deger gormelisiniz
Select-String -Path .env -Pattern "^SECURITY_ENCRYPTION_KEY="
```

**Mevcut bir kurulumu güncelliyorsanız** yukarıdaki 3 komutu çalıştırmayın —
mevcut `.env` dosyasındaki şifreleme anahtarını ve `DB_PATH`'i **koruyun,
üzerine yazmayın**; aksi halde o ana kadar maskelenmiş veriler geri
çözülemez hale gelir.

### 5) Veritabanını oluştur

```powershell
# masking_service klasorune gec
cd masking_service
```

```powershell
# Sema + baslangic kural setini veritabanina uygula (SQLite dosyasi yoksa olusturur)
.venv\Scripts\python.exe -m alembic upgrade head
```

```powershell
# Veritabani dosyasinin gercekten olustugunu dogrula
Test-Path ..\masking.db
```

```powershell
# Bir ust klasore geri don (2. bolumdeki adimlar icin gerekecek)
cd ..
```

## 2. Çalıştırma (backend + UI, iki ayrı süreç)

### Hızlı başlatma (tek komut)

```powershell
cd C:\masking\masking_service
.\start.ps1            # ilk kurulumda: .\start.ps1 --migrate
```

Linux/macOS: `.venv/bin/python start.py`. Betik `.env` ayarlarını ve veritabanını
kontrol eder, backend'i başlatıp `/health` yanıt verene kadar bekler, sonra web
arayüzünü açar. `Ctrl+C` ikisini birlikte kapatır. Aşağıdaki adımlar süreçleri
elle başlatmak isteyenler içindir.

Dosya ve proje dışa aktarımında, özel işleyicisi bulunmayan ve içeriği metin olarak tanınamayan dosyalar
**desteklenmeyen içerik** olarak raporlanır. Bu karar proje adına veya `.bin`
uzantısına özel değildir; içerik kontrolüne dayanır. Dosyalar taranmaz,
çıktıya kopyalanmaz ve onay/serbest bırakma kuyruğuna eklenmez. Sonuç
**uyarılı tamamlandı** olarak kalır; arayüz ve işlem kayıtları hangi dosyaların
eksik olduğunu gösterir. Metin içeren bilinmeyen uzantılar mevcut tarama
kurallarına tabidir. Boyut, kodlama, sözdizimi ve geri dönüş doğrulaması
hataları teknik doğrulama hatası olarak engellenmeye devam eder.

Kaynak kodu güncelledikten sonra backend ve UI süreçlerini yeniden başlatın;
açık kalan backend eski kodla çalışmaya devam edebilir. Eski işlem raporları
geçmişi gösterir; yeni davranışı görmek için orijinal projeyi yeniden tarayın.

Geri dönüş doğrulaması, geri çözülen içeriğin orijinal kaynakla birebir
aynı olmasını esas alır. Kaynakta zaten bulunan ve yer tutucuya benzeyen
bir sabit/sayı, içerik aynen geri elde ediliyorsa eksik eşleme hatası sayılmaz.
Gerçekten yeni üretilen bir yer tutucunun eşlemesi eksik veya yanlışsa kontrol
başarısız olur. Geri alma işleminde bu ayrım yalnızca imzalı bütünlük kaydıyla
hem dosyanın değişmediği hem de kaynak içeriğin aynen elde edildiği
doğrulandığında yapılır; bu kanıt yoksa çözülemeyen yer tutucular raporlanır.

### Java `.class` dosyaları

Hariç tutma kurallarına takılmayan `.class` dosyaları özel Java sabit havuzu
işleyicisiyle taranır. String sabitleri, anotasyon metinleri ve kaynak dosya
adındaki tespit edilen hassas değerler maskelenir. `ConstantValue` alan adları
(`PASSWORD` gibi) tespit bağlamı olarak korunur. Çıktı yine ikili `.class`
dosyasıdır; metin dökümü değildir. İndirmeden önce gerçek eşlemelerle geri
oluşturulan dosyanın bayt özeti orijinalle karşılaştırılır; geri almada da
imzalı bayt bütünlüğü doğrulanır. Java/JDK kurulumu veya internet gerekmez;
kullanıcının sınıfı yüklenmez ya da çalıştırılmaz.

Kapsam: metin sabitleri. Sayısal sabitler, bytecode ile çalışma anında üretilen
metinler ve şifreli veriler bu metin taramasına dahil değildir. Sınıf/metot
adları, descriptor gibi yapısal alanları değiştirmeyi gerektiren bulgular
çıktıyı engeller. Bilinmeyen/custom attribute, `SourceDebugExtension`, bozuk
class veya desteklenmeyen sürüm de açıklamalı hata verir. Karantinaya alınan
class dosyası, inceleme kararı sonrası orijinal projeden yeniden taranmalıdır.
Proje seviyesinde çalışma davranışı garanti edilmez; kapsam uyarısı raporda
gösterilir. Java 21 ile derlenmiş örnekte JVM doğrulaması ve birebir geri
dönüş test edilmiştir. Format referansı: [JVMS bölüm 4](https://docs.oracle.com/javase/specs/jvms/se25/html/jvms-4.html).

Venv `masking_service\.venv` altındadır; `.venv\Scripts\python.exe`'yi tam
yolla çağırdığınız sürece `Activate.ps1`'e gerek yoktur (execution policy
sorunu varsa bu yöntem onu tamamen bypass eder). Aşağıdaki adımlar için
**iki ayrı PowerShell penceresi** açık tutmanız gerekir.

### 1) Terminal 1 - backend'i başlat

```powershell
# masking_service klasorune gec
cd C:\masking\masking_service
```

```powershell
# FastAPI backend'i 127.0.0.1:8001'de baslat - bu terminal acik/calisir kalmali
.venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001
```

`Uvicorn running on http://127.0.0.1:8001` satırını görene kadar bekleyin,
bu terminali kapatmayın.

### 2) Terminal 2 - web arayüzünü başlat

Yeni bir PowerShell penceresi açın:

```powershell
# masking_service klasorune gec (yeni pencere, venv henuz aktif degil)
cd C:\masking\masking_service
```

```powershell
# Streamlit web arayuzunu 8501 portunda baslat - bu terminal de acik kalmali
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Streamlit otomatik olarak tarayıcıda `http://localhost:8501` adresini açar;
açmazsa adresi elle girin.

### 3) Terminal 3 - doğrulama

Üçüncü bir PowerShell penceresinde (backend/UI'ı durdurmadan):

```powershell
# Backend ayakta mi diye kontrol et - status: ok DONMELI
Invoke-RestMethod http://127.0.0.1:8001/health
```

Tarayıcıda Streamlit'in gösterdiği adresi açın. Kimlik girişinden sonra sol
menüdeki ekranlar:

- **Dışarı Çıkar** - proje klasörünü/dosyasını maskeleyip dışa aktarır (CLI
  `export` ile aynı işlem, web arayüzünden).
- **Onay Bekleyenler** - Katman 2/LLM'in düşük güvenle işaretlediği veya
  denetim (audit) uyarısı üretilen bulguları insan onayına sunar.
- **Geri Al** - maskelenmiş bir çıktıyı `proje + sicil + branch` üçlüsüyle
  gerçek değerlere geri dönüştürür (CLI `unmask` ile aynı işlem).
- **Geçmiş İşlemler** - `masking_runs` tablosundaki önceki export/unmask
  koşularını ve bulgu özetlerini listeler.
- **Kurumsal Terim Sözlüğü** - kurum içi terim listesini (.xlsx) toplu
  yükleyip önizleme/onay sonrası filtre kuralı olarak kaydeder (XML
  entity-expansion sertleştirmesiyle güvenli ayrıştırma).

### 4) Durdurma

```powershell
# Terminal 1 (backend) ve Terminal 2 (Streamlit) penceresinde ayri ayri:
# Ctrl+C tuslayin ve surecin tamamen kapandigini "Terminate batch job (Y/N)?"
# sorusuna Y ile onaylayin (sorulmazsa zaten kapanmis demektir)
```

Her iki süreç de kapandıktan sonra tekrar başlatmak için 1) ve 2) adımlarını
yineleyin; venv/veritabanı kurulumunu tekrar yapmanıza gerek yoktur.

## 3. Sorun giderme

**"database is locked" hatası (özellikle export sırasında):**
- Sadece **tek bir** `uvicorn` süreci çalıştığından emin olun (`tasklist | findstr python`
  ile kontrol edin; birden fazla varsa hepsini kapatıp tek seferde yeniden başlatın).
- Kurulum klasörünü (`masking_system`) antivirüs gerçek-zamanlı taramasından ve
  OneDrive/kurumsal bulut senkronizasyonundan **hariç tutun** — dosya kilitleme
  çakışmasının en sık nedeni budur.
- `masking.db` dosyasını DB Browser/SQLite viewer gibi bir araçla açık bırakmayın.
- Uygulamayı flash bellek/USB üzerinden değil, yerel diskten çalıştırın (exFAT/FAT32
  gibi taşınabilir dosya sistemleri SQLite WAL modunun ihtiyaç duyduğu kilitlemeyi
  düzgün desteklemeyebilir).
- Yarım kalmış bir önceki denemeden sonra `masking_service/masking.db-wal` dosyası
  büyük kaldıysa: tüm python süreçlerini düzgünce kapatıp tekrar başlatın, SQLite
  açılışta WAL'ı otomatik checkpoint'ler.

**Backend ayakta ama her istek 503/hata veriyor:**
`/health` endpoint'i veritabanı şemasını kontrol etmez (sadece bağlantıyı test eder),
bu yüzden migration'ları unutsanız bile "ok" döner. `python -m alembic upgrade head`
adımını atladıysanız gerçek endpoint'ler (kurallar, export, review vb.) "no such
table" hatasıyla başarısız olur — yukarıdaki "Veritabanını oluştur" adımını çalıştırın.

**Kurulum sırasında paket bulunamıyor / sürüm hatası:**
Hedef makinenin Python sürümü/mimarisi (`python --version`), wheelhouse'un hazırlandığı
hedeften (3.14 / win_amd64) farklıdır. Doğru sürümü kurup tekrar deneyin.

## 4. Temel kullanım (CLI)

Her yeni dışa aktarımın kendine ait bir **JOB ID**'si (işlem numarası) vardır.
IP, e-posta, secret ve diğer metinsel placeholder sayaçları her işlemde
ayrı ayrı 1'den başlar. Aynı proje tekrar dışa aktarıldığında da yeni bir
işlem oluşur. Aynı değer bir işlem içindeki tüm dosyalarda aynı placeholder'ı
kullanır; eşlemeler `JOB ID + placeholder` ile seçilir.

Geri alma sırasında JOB ID, paketin `.masking-integrity.json` dosyasından
imzası doğrulanarak otomatik okunur. Bu dosyayı paketle birlikte koruyun.
Tek dosya geri alırken veya paket kaydı eksikse **Geri Al** ekranındaki
"Kaynak maskeleme işlem numarası (JOB ID)" alanını, API'de `job_id` alanını
veya CLI'da `--job-id 123` seçeneğini kullanın. Kimlik eksikse sistem bir
işlem tahmin etmez. Eski sürüm çıktıları için geçmiş eşlemeler korunur.

Bu sürüme geçerken uygulamayı durdurun, veritabanını yedekleyin ve
`masking_service` klasöründe `.venv\Scripts\python.exe -m alembic upgrade head`
komutunu çalıştırıp uygulamayı yeniden başlatın (Linux: `.venv/bin/python`).

Kurumsal ifade yüklerken girilen **Başlık / Proje Adı**, yalnızca uygulama içindeki
gruplama için kullanılır. Bu ifadeler çıktıda `mask_kurumsal_ifade_<sayı>` olarak
maskelenir; başlık placeholder'a eklenmez. Önceden kayıtlı kurallar da yeni
dışa aktarımlarda bu genel adı kullanır. Eski placeholder eşlemeleri geçmiş
dosyaları geri alabilmek için korunur; önceden üretilmiş çıktıları düzeltmek
için orijinal kaynaktan yeniden dışa aktarım yapılmalıdır.

Aşağıdaki komutlar `masking_service` içinde, venv aktifken (`.venv\Scripts\Activate.ps1`)
ya da doğrudan `.venv\Scripts\python.exe -m app.cli ...` şeklinde çalıştırılır:

```powershell
# maskele + dışarı aktar
python -m app.cli export --kaynak .\proje --hedef D:\proje-masked `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# geri dönüştür
python -m app.cli unmask --kaynak D:\proje-masked --hedef .\proje-geri `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# yeni filtre kuralı ekle (kod değişikliği/deploy gerekmez)
python -m app.cli kural-ekle --tip tc_kimlik_no --pattern '\b\d{11}\b' `
    --placeholder-format 'TC_TEST_{sayac}' --olusturan EMP-1001

# kuralları listele / aktif-pasif et (silmez, geçmiş eşlemeler bozulmaz)
python -m app.cli kural-listele --sadece-aktif
python -m app.cli kural-aktif tc_kimlik_no
python -m app.cli kural-pasif tc_kimlik_no

# bir projenin en son ne zaman export/unmask edildiğini sorgula
python -m app.cli rapor-son-islem --proje Poseidon

# filtrelere uyan tüm export/unmask geçmişini listele
python -m app.cli rapor-gecmis --proje Poseidon --limit 20

# bir run_id'ye ait dosya bazlı audit log detayını göster
python -m app.cli rapor-detay --run-id 7

# uygulama export/unmask sırasında durdurulduysa yarım kalan hedef yazımını kurtar
python -m app.cli recover-output --hedef D:\proje-masked
```
