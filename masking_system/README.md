# Maskeleme Sistemi

Hassas kurumsal verileri (IP, e-posta, proje adı, sicil no, secret/API key vb.)
otomatik maskeleyen, ihtiyaç halinde geri dönüştürülebilir hale getiren sistem.
SQLite kullanır - ayrı bir DB sunucusu gerekmez. Detaylı mimari/güvenlik
notları için `app/` altındaki modül docstring'lerine bakın.

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

Onaydan sonra serbest bırakılan dosyalar çıktıdaki maskelenmiş yola yazılır,
aynı işlemin diğer dosyalarında maskelenen değerlere karşı tutarlılık
kontrolünden geçer ve imzalı bütünlük kaydına eklenir.

Export web arayüzünde arka plan işi olarak çalışır; ekran işlenen/toplam dosya
ilerlemesini gösterir. Export sırasında veritabanı yazma kilidi LLM
çağrıları boyunca tutulmaz; aynı anda başka projelerin export'ları ve onay
işlemleri beklemeden çalışabilir. Uygulama bir export sırasında kapanırsa
işlem `recover-output` ile kapatılır (bkz. 4. bölüm).

Yereldeki model ile intradaki Qwen modeli farklı olabilir. Her ortamın
`VLLM_HOST` ve `VLLM_MODEL` değerlerini kendi sunucusunun sunduğu adla ayarlayın.

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
