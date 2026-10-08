# Maskeleme Sistemi – Intra Kurulum ve Kullanım Kılavuzu

Bu kılavuz, bu flash bellekteki paketi **internetsiz kurum içi (intra) Windows
makinesine** kurmak, güncellemek ve kullanmak içindir. Adımları sırayla
uygulayın, hiçbirini atlamayın.

**Hangi bölüm size uygun?**

- Makinede daha önce hiç kurulmadıysa: **Bölüm A – İlk kurulum**
- Makinede eski bir sürüm varsa: **Bölüm B – Güncelleme**
- Kurulum bittiyse ve sadece kullanacaksanız: **Bölüm C – Günlük kullanım**

**Bu kılavuzdaki sabit değerler:**

- Kurulum klasörü: `C:\masking_system`
- Flash bellek sürücüsü: `E:`

Sizin klasörünüz ya da sürücü harfiniz farklıysa komutlarda bunları kendi
değerlerinizle değiştirin. Tüm komutlar **PowerShell** içindir ve hiçbiri
internete çıkmaz.

---

## Bölüm A – İlk kurulum

### A1. PowerShell'i açın

Başlat menüsüne `PowerShell` yazın ve **Windows PowerShell**'i açın. Bu bölüm
boyunca aynı pencereyi kullanın.

### A2. Python sürümünü kontrol edin

```powershell
python --version
```

Çıktı `Python 3.14.x` olmalı.

```powershell
python -c "import platform; print(platform.machine())"
```

Çıktı `AMD64` olmalı.

İkisinden biri farklıysa **devam etmeyin**. Paket yalnızca Python 3.14 ve
64 bit Windows için hazırlanmıştır.

### A3. Flash belleğin sürücü harfini bulun

```powershell
Get-Volume | Where-Object DriveType -eq Removable
```

`DriveLetter` sütunundaki harf flash belleğinizdir. `E` değilse sonraki
adımlarda `E:` yerine bu harfi yazın.

### A4. Hedef klasörün henüz olmadığını kontrol edin

```powershell
Test-Path C:\masking_system
```

Çıktı `False` olmalı. `True` ise makinede eski bir kurulum vardır; bu bölüm
yerine **Bölüm B – Güncelleme**'yi uygulayın.

### A5. Paketi flash bellekten yerel diske kopyalayın

Flash bellekteki `masking_system` klasörü adı değişmeden `C:\` altına kopyalanır
ve `C:\masking_system` olur:

```powershell
Copy-Item -Recurse "E:\masking_system" C:\
```

Uygulamayı **hiçbir zaman doğrudan flash bellekten çalıştırmayın**. Flash
bellekte veritabanı bozulabilir ve çalışma çok yavaşlar.

### A6. Kopyanın eksiksiz geldiğini kontrol edin

```powershell
cd C:\masking_system
Get-ChildItem -Force
```

Listede şunlar görünmeli: `masking_service`, `wheels`, `install_offline.ps1`,
`.env.example`, `manifest.json`, `requirements.lock`, `README.md`.

### A7. Bağımlılıkları kurun

```powershell
cd C:\masking_system
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1
```

Birkaç dakika sürer. Son satırda
`Offline dependencies and parsers are ready.` yazmalı. Kırmızı bir hata
görürseniz durun ve **Bölüm E – Sorun giderme**'ye bakın.

Python `python` komutuyla açılmıyorsa Python'un tam yolunu verin:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1 -Python "C:\Program Files\Python314\python.exe"
```

### A8. `.env` ayar dosyasını oluşturun

```powershell
cd C:\masking_system
Copy-Item .env.example .env
```

`.env` dosyası `masking_service` klasörünün **yanında**
(`C:\masking_system\.env`) olmalıdır.

### A9. Şifreleme anahtarını üretip `.env`'ye yazın

```powershell
cd C:\masking_system
$key = & "masking_service\.venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
(Get-Content .env) -replace '^SECURITY_ENCRYPTION_KEY=.*', "SECURITY_ENCRYPTION_KEY=$key" | Set-Content .env
```

### A10. Anahtarın yazıldığını kontrol edin

```powershell
Select-String -Path .env -Pattern "^SECURITY_ENCRYPTION_KEY="
```

`CHANGE_ME` değil, uzun ve rastgele bir değer görünmeli.

### A11. `.env` dosyasını güvenli bir yere yedekleyin

```powershell
Copy-Item C:\masking_system\.env "C:\masking_system-env-yedek.env"
```

Bu yedeği ayrıca kurumunuzun onayladığı güvenli bir yerde de saklayın.
**Şifreleme anahtarı kaybolursa ya da değişirse o ana kadar maskelenmiş hiçbir
çıktı geri dönüştürülemez.** Yeni anahtar üretmek bunu düzeltmez.

### A12. LLM ayarlarını yazın

`.env` dosyasını Not Defteri ile açın:

```powershell
notepad C:\masking_system\.env
```

Aşağıdaki satırları ayarlayın. Satır dosyada **zaten varsa değerini
değiştirin**, **yoksa dosyanın en altına ekleyin**:

```
VLLM_ENABLED=true
VLLM_HOST=http://<vllm-sunucusunun-adresi>:8000
VLLM_MODEL=<vllm sunucusundaki model adı (--served-model-name)>
VLLM_DISABLE_THINKING=true
VLLM_MAX_CONCURRENT_REQUESTS=4
VLLM_FILE_BATCH_SIZE=16
VLLM_MAX_TOKENS=2048
VLLM_TRANSIENT_RETRIES=2
```

- `VLLM_HOST` ve `VLLM_MODEL` değerlerini vLLM sunucusunu yöneten kişiden öğrenin.
- `VLLM_DISABLE_THINKING=true` **zorunludur**. Kapatılmazsa model yanıt
  bütçesini düşünmeye harcar, yanıtlar kesilir ve dosyalar karantinaya düşer.
- `VLLM_MAX_CONCURRENT_REQUESTS=4` bir başlangıç değeridir; A16'daki ölçümle
  kesinleşecek.
- `VLLM_REASONING_EFFORT` satırını yazmayın (vLLM bu ayarı reddedebilir).
- LLM kullanılmayacaksa yalnızca `VLLM_ENABLED=false` yazmanız yeterlidir.

Dosyayı kaydedip Not Defteri'ni kapatın.

### A13. LLM ayarlarını kontrol edin

```powershell
Select-String -Path C:\masking_system\.env -Pattern "^VLLM_"
```

A12'deki her ayar **bir kez** görünmeli ve `CHANGE_ME` hiçbir yerde kalmamalı.
Aynı ayar iki kez görünüyorsa Not Defteri'nde birini silin.

### A14. Veritabanını oluşturun

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
```

Hata vermeden bitmeli. Veritabanı dosyası
`C:\masking_system\masking.db` olarak oluşur.

### A15. Ön kontrolü çalıştırın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe scripts\check_llm_preflight.py
```

Çıktıda şunlar görünmeli:

- `PASS stage=build_stamp`
- `PASS stage=runtime_rules ...`
- en sonda `RESULT=OFFLINE_OK`

`FAIL` satırı ya da `RESULT=OFFLINE_FAILED` görürseniz devam etmeyin;
**Bölüm E**'ye bakın.

### A16. LLM eşzamanlılık ölçümünü çalıştırın

`VLLM_MAX_CONCURRENT_REQUESTS`, uygulamanın vLLM sunucusuna aynı anda kaç istek
gönderdiğini belirler. Değer yükseldikçe iş genelde hızlanır; bir noktadan sonra
sunucu yetişemez, hız artmaz ve hata başlar. Bu ölçüm o noktayı bulur. Yalnızca
yapay metin gönderir; veritabanına ve gerçek dosyalara dokunmaz.

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe scripts\benchmark_llm.py --concurrency 1 2 4 8 --files 16 --chars 12000 --max-tokens 2048 > ..\bench-c.json
```

Birkaç dakika sürer; 1, 2, 4 ve 8 değerleri sırayla denenir.

### A17. Ölçüm sonucunu okuyun

```powershell
(Get-Content -Raw C:\masking_system\bench-c.json | ConvertFrom-Json) | ForEach-Object { "{0} -> {1} sn, hatali dosya: {2}" -f $_.concurrency, $_.elapsed_seconds, @($_.files | Where-Object status -ne 'ok').Count }
```

Ekranda 4 satır görünür. Aşağıdaki sayılar yalnızca örnektir:

```
1 -> 120 sn, hatali dosya: 0
2 -> 65 sn, hatali dosya: 0
4 -> 38 sn, hatali dosya: 0
8 -> 35 sn, hatali dosya: 0
```

### A18. Eşzamanlılık değerini seçin

1. Hatalı dosya sayısı 0 olmayan satırları eleyin.
2. Kalanlar arasında, bir öncekine göre **belirgin şekilde (en az %15)
   hızlanan en büyük değeri** seçin.

Örnekte 4'ten 8'e geçmek süreyi yalnızca 38 sn'den 35 sn'ye indiriyor; doğru
seçim **4**'tür.

- vLLM sunucusu başka uygulamalarla da paylaşılıyorsa bir kademe düşük değer
  seçin.
- vLLM sunucusunun `--max-num-seqs` değeri seçtiğiniz sayıdan küçük olmamalıdır;
  bunu sunucu yöneticisine sorun.

### A19. Seçtiğiniz değeri `.env`'ye yazın

```powershell
notepad C:\masking_system\.env
```

`VLLM_MAX_CONCURRENT_REQUESTS=` satırını seçtiğiniz değerle değiştirin
(ör. `VLLM_MAX_CONCURRENT_REQUESTS=4`), kaydedip kapatın.

### A20. Uygulamayı başlatın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

Backend açılır, ardından web arayüzü tarayıcıda açılır. Açılmazsa tarayıcıda
`http://localhost:8501` adresine gidin. Bu PowerShell penceresi açık kaldığı
sürece uygulama çalışır.

İlk kurulum tamamlandı.

---

## Bölüm B – Güncelleme (eski sürümün yerine yenisini kurmak)

Eski kurulumdaki **`.env` (şifreleme anahtarı) ve `masking.db` (kurallar,
eşlemeler, geçmiş) asla kaybedilmemelidir.** Bu bölüm önce onları yedekler,
sonra eski klasörü siler ve yeni sürümü kurar.

### B1. Uygulamayı durdurun

Uygulamanın çalıştığı PowerShell penceresinde `Ctrl+C`'ye basın.

### B2. Çalışan Python kalmadığını kontrol edin

```powershell
tasklist | findstr python
```

Hiçbir satır görünmemeli. Satır görünüyorsa uygulama pencerelerini kapatıp
komutu tekrarlayın.

### B3. Eski kurulum klasörünü belirleyin

Yeni bir PowerShell penceresi açın ve bu bölüm boyunca kapatmayın. Eski
kurulumunuzun klasörünü yazın (`.env` dosyasının bulunduğu klasör). Eski
kurulum başka bir yerdeyse (ör. `C:\masking\source`) tırnak içine o yolu yazın:

```powershell
$ESKI = "C:\masking_system"
Test-Path "$ESKI\.env"
```

Çıktı `True` olmalı. `False` ise klasör yanlıştır; doğru klasörü yazıp tekrar
deneyin.

### B4. Veritabanının yerini kontrol edin

```powershell
Select-String -Path "$ESKI\.env" -Pattern "^DB_PATH="
```

`DB_PATH=masking.db` görünüyorsa veritabanı `$ESKI` klasöründedir. Başka bir
yol görünüyorsa B6'da `masking.db*` dosyalarını o yoldan kopyalayın.

### B5. Yedek klasörü oluşturun

```powershell
$YEDEK = "C:\masking_system-yedek-$(Get-Date -Format yyyyMMdd-HHmm)"
New-Item -ItemType Directory $YEDEK | Out-Null
```

### B6. `.env`, veritabanı ve web çıktılarını yedekleyin

```powershell
Copy-Item "$ESKI\.env" $YEDEK
Copy-Item "$ESKI\masking.db*" $YEDEK
if (Test-Path "$ESKI\masking_service\uploads_output") { Copy-Item -Recurse "$ESKI\masking_service\uploads_output" $YEDEK }
```

`uploads_output`, web arayüzünden yüklenen projelerin çıktılarıdır; yoksa
atlanır.

### B7. Yedeği kontrol edin

```powershell
Get-ChildItem $YEDEK
```

En az `.env` ve `masking.db` görünmeli. **Görünmüyorsa devam etmeyin.**

### B8. Eski kurulum klasörünü silin

```powershell
cd C:\
Remove-Item -Recurse -Force $ESKI
```

### B9. Yeni paketi flash bellekten kopyalayın

Önce hedefte eski bir klasör kalmadığını kontrol edin:

```powershell
Test-Path C:\masking_system
```

Çıktı `False` olmalı. `True` ise B8'deki silme tamamlanmamıştır ya da eski
kurulum başka bir klasördeydi ve `C:\masking_system` ayrıca vardır; bu klasörde
`.env` ya da `masking.db` varsa onları da yedekleyip klasörü silin. Ardından
flash bellekteki klasörü adı değişmeden `C:\` altına kopyalayın:

```powershell
Copy-Item -Recurse "E:\masking_system" C:\
```

### B10. `.env`, veritabanı ve web çıktılarını geri koyun

```powershell
Copy-Item "$YEDEK\.env" C:\masking_system
Copy-Item "$YEDEK\masking.db*" C:\masking_system
if (Test-Path "$YEDEK\uploads_output") { Copy-Item -Recurse "$YEDEK\uploads_output" C:\masking_system\masking_service }
```

B4'te veritabanı başka bir yolda çıktıysa `masking.db*` dosyalarını o yola
geri koyun.

### B11. Artık kullanılmayan ayarları `.env`'den silin

`VLLM_PROFILE` ve `VALIDATION_SQL_DIALECT` ayarları bu sürümde kaldırıldı.

```powershell
cd C:\masking_system
(Get-Content .env) | Where-Object { $_ -notmatch '^\s*(VLLM_PROFILE|VALIDATION_SQL_DIALECT)=' } | Set-Content .env
```

### B12. LLM ayarlarını kontrol edin

```powershell
Select-String -Path C:\masking_system\.env -Pattern "^VLLM_"
```

Şu satırların **her biri bir kez** görünmeli:

```
VLLM_ENABLED=true
VLLM_HOST=...
VLLM_MODEL=...
VLLM_DISABLE_THINKING=true
VLLM_MAX_CONCURRENT_REQUESTS=...
VLLM_FILE_BATCH_SIZE=16
VLLM_MAX_TOKENS=2048
VLLM_TRANSIENT_RETRIES=2
```

Eksik olan varsa `notepad C:\masking_system\.env` ile en alta ekleyin.
Eskiden yalnızca `VLLM_PROFILE` satırına güvendiyseniz bu satırların hiçbiri
olmayabilir; hepsini ekleyin. **`VLLM_DISABLE_THINKING=true` eksikse dosyalar
karantinaya düşer.**

### B13. Bağımlılıkları kurun

```powershell
cd C:\masking_system
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1
```

Son satırda `Offline dependencies and parsers are ready.` yazmalı.

### B14. Veritabanını yeni sürüme yükseltin

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
```

Hata vermeden bitmeli. Sicil düzeltmesi ilk kez uygulanıyorsa çıktıda
`sicil_kategori_duzeltme duzeltilen=1` (ya da `=0`) satırı görünür. `DEGISTIRILMEDI`
ya da `aktif 'sicil_no' parametrik kurali yok` uyarısı görürseniz durun ve
yöneticinize bildirin.

### B15. Ön kontrolü çalıştırın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe scripts\check_llm_preflight.py
```

En sonda `RESULT=OFFLINE_OK` görünmeli.

### B16. Eşzamanlılık ölçümünü yapın (gerekiyorsa)

Bu ölçümü daha önce hiç yapmadıysanız ya da vLLM sunucusu, modeli veya GPU'su
değiştiyse **A16, A17, A18 ve A19** adımlarını uygulayın. Değişen bir şey yoksa
bu adımı geçin.

### B17. Uygulamayı başlatın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

### B18. Yedeği saklayın

Yeni sürümle birkaç export'u sorunsuz yaptıktan sonra bile `$YEDEK` klasörünü
silmeyin. Bir sorun olursa eski duruma dönmek için gereklidir. Yeni sürümdeki
veritabanı B14'te yükseltildiği için eski sürüme dönülecekse yedekteki
`masking.db` kullanılmalıdır.

Güncelleme tamamlandı.

---

## Bölüm C – Günlük kullanım

### C1. Uygulamayı başlatmak için

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

### C2. Web arayüzünü açmak için

Tarayıcıda şu adrese gidin:

```
http://localhost:8501
```

Giriş ekranında proje adı, sicil numarası ve branch bilgisini girin. Sol
menüdeki ekranlar:

- **Dışarı Çıkar:** projeyi maskeleyip dışarı aktarır.
- **Onay Bekleyenler:** karantinadaki ve onay bekleyen dosyalar. Her dosya için
  "Maskele", "Yanlış Alarm" ya da "Düzenle" kararı verilir.
- **Geri Al:** maskeli çıktıyı gerçek değerlere geri dönüştürür.
- **Geçmiş İşlemler:** önceki işlemler ve JOB ID'leri.
- **Kurumsal Terim Sözlüğü:** kurum terim listesini (.xlsx) yükler.

### C3. Uygulamayı durdurmak için

Uygulamanın çalıştığı PowerShell penceresinde `Ctrl+C`'ye basın.

### C4. Backend'in çalıştığını kontrol etmek için

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

`status` değeri `ok` olmalı.

### C5. Büyük bir projeden önce iş yükünü tahmin etmek için

LLM'e istek göndermez; hangi dosyaların ağır olduğunu gösterir.

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m app.cli llm-is-yuku --kaynak <proje-klasörü>
```

---

## Bölüm D – Komut satırından kullanım (isteğe bağlı)

Web arayüzü yerine komut satırını kullanmak isterseniz. Tüm komutlar şu
klasörde çalıştırılır:

```powershell
cd C:\masking_system\masking_service
```

### D1. Bir projeyi maskeleyip dışarı aktarmak için

```powershell
.venv\Scripts\python.exe -m app.cli export --kaynak <proje-klasörü> --hedef <çıktı-klasörü> --proje <proje-adı> --sicil <sicil> --branch <branch>
```

### D2. Maskeli çıktıyı geri dönüştürmek için

```powershell
.venv\Scripts\python.exe -m app.cli unmask --kaynak <maskeli-klasör> --hedef <geri-dönüştürülmüş-klasör> --sicil <sicil>
```

Proje adı, branch ve JOB ID, maskeli klasördeki `.masking-integrity.json`
dosyasından okunur; **bu dosyayı silmeyin**. Dosya yoksa (eski paketler ya da
tek dosya) komuta `--proje <proje-adı> --branch <branch> --job-id <numara>`
ekleyin.

### D3. Son işlemin raporunu görmek için

```powershell
.venv\Scripts\python.exe -m app.cli rapor-son-islem --proje <proje-adı>
```

### D4. Geçmiş işlemleri listelemek için

```powershell
.venv\Scripts\python.exe -m app.cli rapor-gecmis --proje <proje-adı> --limit 20
```

### D5. Bir işlemin ayrıntısını görmek için

```powershell
.venv\Scripts\python.exe -m app.cli rapor-detay --run-id <numara>
```

### D6. Filtre kurallarını listelemek için

```powershell
.venv\Scripts\python.exe -m app.cli kural-listele --sadece-aktif
```

### D7. Yeni filtre kuralı eklemek için

```powershell
.venv\Scripts\python.exe -m app.cli kural-ekle --tip <kural-adı> --pattern '<regex>' --placeholder-format 'mask_<kural-adı>_{sayac}'
```

Örnek: `--tip tc_kimlik_no --pattern '\b\d{11}\b' --placeholder-format 'mask_tc_kimlik_{sayac}'`.
Yer tutucu biçimi `_{sayac}` ile bitmelidir.

### D8. Bir kuralı açmak ya da kapatmak için

```powershell
.venv\Scripts\python.exe -m app.cli kural-aktif <kural-adı>
.venv\Scripts\python.exe -m app.cli kural-pasif <kural-adı>
```

Kurallar silinmez, yalnızca açılıp kapatılır; geçmiş eşlemeler bozulmaz.

### D9. Yarıda kalan bir export'u kurtarmak için

Uygulama bir export sırasında kapandıysa:

```powershell
.venv\Scripts\python.exe -m app.cli recover-output --hedef <çıktı-klasörü>
```

---

## Bölüm E – Sorun giderme

### E1. `install_offline.ps1` "This bundle requires Windows / Python 3.14" diyorsa

Makinedeki Python 3.14 değildir ya da 64 bit değildir. Doğru Python'u kurun ve
A7'deki gibi `-Python` ile tam yolunu verin.

### E2. Betik çalıştırma izni hatası alıyorsanız

Kurulum betiğini bu kılavuzdaki gibi `powershell -ExecutionPolicy Bypass -File ...`
ile çalıştırın. Uygulamayı her zaman `.venv\Scripts\python.exe start.py` ile
başlatın; bu komut betik izni gerektirmez.

### E3. Ön kontrolde `FAIL stage=build_stamp` görüyorsanız

`app` klasörü eksik ya da karışık kopyalanmıştır. Uygulamayı durdurun, kurulum
klasöründeki `masking_service\app` klasörünü silin ve flash bellekten yeniden
kopyalayın:

```powershell
Remove-Item -Recurse -Force C:\masking_system\masking_service\app
Copy-Item -Recurse "E:\masking_system\masking_service\app" C:\masking_system\masking_service
```

Ardından ön kontrolü (A15) tekrar çalıştırın.

### E4. Export "kod değişti" ya da 503 hatasıyla reddediliyorsa

Kod kopyalandıktan sonra uygulama yeniden başlatılmamıştır. Uygulamayı
durdurup (C3) yeniden başlatın (C1).

### E5. "no such table" hatası alıyorsanız

Veritabanı yükseltilmemiştir:

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
```

### E6. "database is locked" hatası alıyorsanız

1. Yalnızca bir uygulama penceresinin açık olduğunu kontrol edin:

   ```powershell
   tasklist | findstr python
   ```

2. `masking.db` dosyasını başka bir programda (ör. DB Browser) açık bırakmayın.
3. Kurulum klasörünü antivirüs gerçek zamanlı taramasından ve OneDrive/bulut
   eşitlemesinden hariç tutun.
4. Uygulamanın flash bellekten değil `C:\masking_system` klasöründen çalıştığından emin olun.

### E7. Dosyalar "yanıt kesildi" (`finish_reason=length`) nedeniyle karantinaya düşüyorsa

```powershell
Select-String -Path C:\masking_system\.env -Pattern "^VLLM_DISABLE_THINKING="
```

`VLLM_DISABLE_THINKING=true` görünmeli. Görünmüyorsa `.env`'ye ekleyip
uygulamayı yeniden başlatın. Sorun sürerse vLLM sunucusunun
`--default-chat-template-kwargs '{"enable_thinking": false}'` ile başlatılmasını
sunucu yöneticisinden isteyin.

### E8. Dosyalar LLM zaman aşımı nedeniyle karantinaya düşüyorsa

`VLLM_MAX_CONCURRENT_REQUESTS` sunucu için fazla yüksektir. Değeri bir kademe
düşürün (ör. 4 → 2) ya da A16–A19'daki ölçümü tekrarlayın, ardından uygulamayı
yeniden başlatın.

### E9. Logda "spaCy NLP modeli yuklenemedi" görüyorsanız

Kişi/kurum adı tespiti çalışmıyor demektir. Bağımlılık kurulumunu (A7) tekrar
çalıştırın.

### E10. Dosya "Kodlanmış ... hassas veri" nedeniyle karantinadaysa

Dosyada base64/hex ile gizlenmiş bir parola, IP ya da kurum terimi bulunmuştur.
Değeri kaynakta kaldırıp projeyi yeniden tarayın ya da değer gerçekten hassas
değilse **Onay Bekleyenler** ekranında "Yanlış Alarm" ile serbest bırakın.

### E11. Export çok yavaşsa

1. En ağır dosyaları görün (C5).
2. Gereksiz ağır dosyaları (ör. sıkıştırılmış JavaScript kütüphaneleri) projeden
   çıkarın.
3. Eşzamanlılık ölçümünü (A16–A19) yapmadıysanız yapın.
