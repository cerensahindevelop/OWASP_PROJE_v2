# Maskeleme Sistemi – Intra Kurulum Kılavuzu

Bu kılavuz, flash bellekteki paketi internetsiz Windows makinesine kurmak ve
kullanmak içindir. Kılavuzdaki hiçbir komut internete çıkmaz.

## Başlamadan önce

**Gerekenler**

- 64 bit Windows ve **Python 3.14**
- vLLM sunucusunun adresi ve model adı (sunucu yöneticisinden öğrenin)

**Kılavuzdaki sabit değerler**

- Kurulum klasörü: `C:\masking_system`
- Flash bellek: `E:`. Sizinki farklıysa komutlarda `E:` yerine kendi harfinizi
  yazın. Harfi öğrenmek için:
  `Get-Volume | Where-Object DriveType -eq Removable`

**Komutları nerede çalıştıracaksınız**

Başlat menüsüne `PowerShell` yazıp **Windows PowerShell**'i açın. Bir bölümü
bitirene kadar aynı pencereyi kullanın.

Her adımın altında **Beklenen** sonuç yazar. Farklı bir sonuç görürseniz
devam etmeyin, **Bölüm 6 – Sorun giderme**'ye bakın.

**Hangi bölümden başlamalısınız?**

| Durum | Bölüm |
|---|---|
| Makinede hiç kurulum yok | **1 – İlk kurulum** |
| Makinede eski sürüm var | **2 – Güncelleme** |
| Kurulum bitti, kullanacaksınız | **3 – Günlük kullanım** |

---

## 1. İlk kurulum

### 1.1 Python'u kontrol edin

```powershell
python -c "import sys, platform; print(sys.version.split()[0], platform.machine())"
```

**Beklenen:** `3.14.x AMD64` (x herhangi bir sayı olabilir). Farklıysa devam
etmeyin; paket yalnızca 64 bit Windows ve Python 3.14 içindir.

### 1.2 Paketi flash bellekten diske kopyalayın

```powershell
Test-Path C:\masking_system
```

**Beklenen:** `False`. `True` çıkarsa makinede eski bir kurulum var demektir;
**Bölüm 2**'ye geçin.

```powershell
Copy-Item -Recurse E:\masking_system C:\
```

Uygulamayı hiçbir zaman doğrudan flash bellekten çalıştırmayın. Veritabanı
bozulabilir, uygulama da çok yavaşlar.

### 1.3 Bağımlılıkları kurun

```powershell
cd C:\masking_system
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1
```

Birkaç dakika sürer.

**Beklenen son satır:** `Offline dependencies and parsers are ready.`

Hata `python` komutunun bulunamadığını söylüyorsa, komutun sonuna Python'un
tam yolunu ekleyin:
`-Python "C:\Program Files\Python314\python.exe"`

### 1.4 Ayar dosyasını ve şifreleme anahtarını oluşturun

Dört satırı sırayla çalıştırın:

```powershell
cd C:\masking_system
Copy-Item .env.example .env
$key = & "masking_service\.venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
(Get-Content .env) -replace '^SECURITY_ENCRYPTION_KEY=.*', "SECURITY_ENCRYPTION_KEY=$key" | Set-Content .env
```

Kontrol:

```powershell
Select-String -Path .env -Pattern "^SECURITY_ENCRYPTION_KEY="
```

**Beklenen:** `CHANGE_ME` değil, uzun ve rastgele bir değer.

### 1.5 Ayar dosyasını yedekleyin

```powershell
Copy-Item C:\masking_system\.env C:\masking_system-env-yedek.env
```

Bu yedeği kurumunuzun onayladığı güvenli bir yerde de saklayın.

> **Önemli:** Şifreleme anahtarı kaybolursa o ana kadar maskelenmiş hiçbir
> çıktı geri dönüştürülemez. Yeni anahtar üretmek bunu düzeltmez.

### 1.6 LLM ayarlarını yazın

```powershell
notepad C:\masking_system\.env
```

Aşağıdaki satırları ayarlayın. Satır dosyada varsa değerini değiştirin, yoksa
dosyanın sonuna ekleyin:

```
VLLM_ENABLED=true
VLLM_HOST=http://<vllm-sunucu-adresi>:8000
VLLM_MODEL=<sunucudaki model adı>
VLLM_DISABLE_THINKING=true
VLLM_MAX_CONCURRENT_REQUESTS=4
VLLM_FILE_BATCH_SIZE=16
VLLM_MAX_TOKENS=2048
VLLM_TRANSIENT_RETRIES=2
```

- `VLLM_DISABLE_THINKING=true` **zorunludur.** Yoksa modelin yanıtları kesilir
  ve dosyalar karantinaya düşer.
- `VLLM_REASONING_EFFORT` satırını eklemeyin; vLLM bu ayarı reddedebilir.
- `VLLM_MAX_CONCURRENT_REQUESTS=4` bir başlangıç değeridir. Asıl değeri
  1.8'deki ölçümle belirleyeceksiniz.

Kaydedip Not Defteri'ni kapatın. Kontrol:

```powershell
Select-String -Path C:\masking_system\.env -Pattern "^VLLM_"
```

**Beklenen:** Yukarıdaki her satır **bir kez** görünür ve hiçbirinde
`CHANGE_ME` kalmaz. Bir satır iki kez görünüyorsa Not Defteri'nde birini
silin.

### 1.7 Veritabanını oluşturun ve ön kontrolü çalıştırın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe scripts\check_llm_preflight.py
```

**Beklenen:**

- İlk komut hatasız biter ve `C:\masking_system\masking.db` dosyası oluşur.
- İkinci komutun çıktısında `PASS stage=build_stamp` ve en sonda
  `RESULT=OFFLINE_OK` görünür. Hiçbir satır `FAIL` ile başlamaz.

Ön kontrol vLLM sunucusuna bağlanmaz; kodun ve ayarların doğru olduğunu
kontrol eder.

### 1.8 Hız ayarını yapın

**Bölüm 4**'teki ölçümü yapın ve bulduğunuz değeri `.env` dosyasına yazın.
Ölçüm vLLM sunucusuyla bağlantıyı da sınamış olur.

### 1.9 Uygulamayı başlatın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

**Beklenen:** Web arayüzü tarayıcıda açılır. Açılmazsa tarayıcıda
`http://localhost:8501` adresine gidin.

PowerShell penceresi açık kaldığı sürece uygulama çalışır. İlk kurulum
tamamlandı.

---

## 2. Güncelleme

Eski kurulumdaki iki dosya **asla kaybedilmemelidir:**

- `.env`: şifreleme anahtarını içerir.
- `masking.db`: kuralları, eşlemeleri ve geçmişi içerir.

Bu bölüm önce bu dosyaları yedekler, sonra yeni sürümü kurar ve dosyaları geri
koyar.

### 2.1 Uygulamayı durdurun

Uygulamanın çalıştığı pencerede `Ctrl+C`'ye basın. Sonra **yeni** bir
PowerShell penceresi açın ve bu bölüm bitene kadar kapatmayın.

```powershell
tasklist | findstr python
```

**Beklenen:** Hiçbir satır görünmez. Satır görünüyorsa uygulama pencerelerini
kapatıp komutu tekrarlayın.

### 2.2 Eski kurulumu yedekleyin

İlk satırdaki yol eski kurulumun klasörüdür, yani `.env` dosyasının bulunduğu
klasör. Eski kurulum başka bir yerdeyse (ör. `C:\masking\source`) bu yolu
değiştirin.

```powershell
$ESKI = "C:\masking_system"
$YEDEK = "C:\masking_system-yedek-$(Get-Date -Format yyyyMMdd-HHmm)"
New-Item -ItemType Directory $YEDEK | Out-Null
Copy-Item "$ESKI\.env" $YEDEK
Copy-Item "$ESKI\masking.db*" $YEDEK
if (Test-Path "$ESKI\masking_service\uploads_output") { Copy-Item -Recurse "$ESKI\masking_service\uploads_output" $YEDEK }
Get-ChildItem -Force $YEDEK
```

**Beklenen:** Listede en az `.env` ve `masking.db` görünür. **Görünmüyorsa
devam etmeyin.** Klasör yanlış yazılmıştır; ilk satırı düzeltip bloğu
tekrarlayın.

> `uploads_output`, web arayüzünden yüklenen projelerin çıktılarıdır; eski
> kurulumda yoksa atlanır. Eski `.env` dosyasında `DB_PATH=` başka bir yol
> gösteriyorsa, `masking.db*` dosyalarını o yoldan yedekleyip 2.4'te o yola geri
> koyun.

### 2.3 Eski klasörü silip yeni paketi kopyalayın

```powershell
cd C:\
Remove-Item -Recurse -Force $ESKI
Test-Path C:\masking_system
```

**Beklenen:** `False`. `True` çıkarsa `C:\masking_system` ayrıca vardır. İçinde
`.env` ya da `masking.db` varsa onları da yedekleyin, sonra klasörü silin.

```powershell
Copy-Item -Recurse E:\masking_system C:\
```

### 2.4 Yedeklenen dosyaları geri koyun

```powershell
Copy-Item "$YEDEK\.env" C:\masking_system
Copy-Item "$YEDEK\masking.db*" C:\masking_system
if (Test-Path "$YEDEK\uploads_output") { Copy-Item -Recurse "$YEDEK\uploads_output" C:\masking_system\masking_service }
```

### 2.5 Ayar dosyasını güncelleyin

Bu sürümde kaldırılan iki ayarı silin:

```powershell
cd C:\masking_system
(Get-Content .env) | Where-Object { $_ -notmatch '^\s*(VLLM_PROFILE|VALIDATION_SQL_DIALECT)=' } | Set-Content .env
Select-String -Path .env -Pattern "^VLLM_"
```

**Beklenen:** Aşağıdaki satırların her biri **bir kez** görünür:

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

Eksik satırları `notepad C:\masking_system\.env` ile dosyanın sonuna ekleyin.
Eski kurulumda yalnızca `VLLM_PROFILE` kullanıldıysa bu satırların hiçbiri
olmayabilir; hepsini ekleyin.

### 2.6 Bağımlılıkları kurun

```powershell
cd C:\masking_system
powershell -ExecutionPolicy Bypass -File .\install_offline.ps1
```

**Beklenen son satır:** `Offline dependencies and parsers are ready.`

### 2.7 Veritabanını yükseltin ve ön kontrolü çalıştırın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe scripts\check_llm_preflight.py
```

**Beklenen:**

- İlk komut hatasız biter. Çıktıda `DEGISTIRILMEDI` ya da
  `aktif 'sicil_no' parametrik kurali yok` uyarısı görürseniz durun ve
  yöneticinize bildirin.
- İkinci komutun çıktısında en sonda `RESULT=OFFLINE_OK` görünür.

### 2.8 Uygulamayı başlatın

vLLM sunucusu, modeli ya da GPU'su değiştiyse ya da hız ölçümünü hiç
yapmadıysanız, önce **Bölüm 4**'ü uygulayın.

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

**Yedek klasörünü silmeyin.** Bir sorun çıkıp eski sürüme dönmek gerekirse
yedekteki `masking.db` kullanılır; yeni kurulumdaki veritabanı 2.7'de yeni
sürüme yükseltilmiştir.

Güncelleme tamamlandı.

---

## 3. Günlük kullanım

**Başlatmak için:**

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe start.py
```

**Arayüzü açmak için:** tarayıcıda `http://localhost:8501`. Giriş ekranında
proje adı, sicil numarası ve branch bilgisini girin.

**Durdurmak için:** uygulamanın çalıştığı pencerede `Ctrl+C`.

**Sol menüdeki ekranlar:**

- **Dışarı Çıkar:** projeyi maskeleyip dışarı aktarır.
- **Onay Bekleyenler:** karantinadaki dosyalar. Her dosya için "Maskele",
  "Yanlış Alarm" ya da "Düzenle" kararı verilir.
- **Geri Al:** maskeli çıktıyı gerçek değerlere geri dönüştürür.
- **Geçmiş İşlemler:** önceki işlemler ve JOB ID'leri.
- **Kurumsal Terim Sözlüğü:** kurumsal terim listesini (.xlsx) yükler.

**Çalışan bir taramayı durdurmak için:** Dışarı Çıkar ekranındaki
**⏹ Taramayı Durdur** düğmesine basın.

- Yarım kalan çıktı silinir, varsa önceki tamamlanmış çıktı korunur. Hemen
  yeniden tarama başlatabilirsiniz.
- Sayfayı yenileseniz de ilerleme çubuğu ve düğme geri gelir.
- Bir dosya o sırada LLM'den yanıt bekliyorsa durma birkaç saniye sürebilir.
- Son yayımlama adımı başladıysa tarama durmaz, tamamlanır.

**Uygulamanın çalıştığını kontrol etmek için:**

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

**Beklenen:** `status` değeri `ok`.

---

## 4. Hız ayarı (LLM eşzamanlılık ölçümü)

`VLLM_MAX_CONCURRENT_REQUESTS`, uygulamanın vLLM sunucusuna aynı anda kaç
istek gönderdiğidir. Değer büyüdükçe tarama hızlanır, ama bir noktadan sonra
sunucu yetişemez ve hatalar başlar. Bu ölçüm o noktayı bulur. Ölçüm yalnızca
yapay metin gönderir; gerçek dosyalara ve veritabanına dokunmaz.

### 4.1 Ölçümü çalıştırın

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe scripts\benchmark_llm.py --concurrency 1 2 4 8 --files 16 --chars 12000 --max-tokens 2048 > ..\bench-c.json
```

Birkaç dakika sürer.

### 4.2 Sonucu okuyun

```powershell
(Get-Content -Raw ..\bench-c.json | ConvertFrom-Json) | ForEach-Object { "{0} -> {1} sn, hatali dosya: {2}" -f $_.concurrency, $_.elapsed_seconds, @($_.files | Where-Object status -ne 'ok').Count }
```

Örnek çıktı (sayılar yalnızca örnektir):

```
1 -> 120 sn, hatali dosya: 0
2 -> 65 sn, hatali dosya: 0
4 -> 38 sn, hatali dosya: 0
8 -> 35 sn, hatali dosya: 0
```

### 4.3 Değeri seçin

1. Hatalı dosya sayısı 0 olmayan satırları eleyin.
2. Kalanlardan, bir önceki satıra göre **en az %15 hızlanan en büyük** değeri
   seçin.

Örnekte 4'ten 8'e geçmek süreyi yalnızca 38 sn'den 35 sn'ye indiriyor
(%8). Doğru seçim **4**.

- vLLM sunucusu başka uygulamalarla da paylaşılıyorsa bir kademe düşük değer
  seçin.
- Sunucunun `--max-num-seqs` ayarı seçtiğiniz değerden küçük olmamalıdır; bunu
  sunucu yöneticisine sorun.

### 4.4 Değeri ayar dosyasına yazın

```powershell
notepad C:\masking_system\.env
```

`VLLM_MAX_CONCURRENT_REQUESTS=` satırına seçtiğiniz değeri yazın, kaydedip
kapatın. Uygulama çalışıyorsa yeniden başlatın.

---

## 5. Komut satırından kullanım (isteğe bağlı)

Web arayüzü yerine komut satırını kullanmak isterseniz. Tüm komutlar bu
klasörde çalıştırılır:

```powershell
cd C:\masking_system\masking_service
```

| İş | Komut |
|---|---|
| Projeyi maskeleyip dışarı aktarmak | `.venv\Scripts\python.exe -m app.cli export --kaynak <proje-klasörü> --hedef <çıktı-klasörü> --proje <proje-adı> --sicil <sicil> --branch <branch>` |
| Maskeli çıktıyı geri dönüştürmek | `.venv\Scripts\python.exe -m app.cli unmask --kaynak <maskeli-klasör> --hedef <hedef-klasör> --sicil <sicil>` |
| Son işlemin raporu | `.venv\Scripts\python.exe -m app.cli rapor-son-islem --proje <proje-adı>` |
| Geçmiş işlemler | `.venv\Scripts\python.exe -m app.cli rapor-gecmis --proje <proje-adı> --limit 20` |
| Bir işlemin ayrıntısı | `.venv\Scripts\python.exe -m app.cli rapor-detay --run-id <numara>` |
| Kuralları listelemek | `.venv\Scripts\python.exe -m app.cli kural-listele --sadece-aktif` |
| Kuralı açmak | `.venv\Scripts\python.exe -m app.cli kural-aktif <kural-adı>` |
| Kuralı kapatmak | `.venv\Scripts\python.exe -m app.cli kural-pasif <kural-adı>` |
| Büyük projede iş yükü tahmini | `.venv\Scripts\python.exe -m app.cli llm-is-yuku --kaynak <proje-klasörü>` |

**Geri dönüştürme notu:** Proje adı, branch ve JOB ID maskeli klasördeki
`.masking-integrity.json` dosyasından okunur; **bu dosyayı silmeyin.** Dosya
yoksa komuta `--proje <proje-adı> --branch <branch> --job-id <numara>` ekleyin.

**Yeni kural eklemek:**

```powershell
.venv\Scripts\python.exe -m app.cli kural-ekle --tip <kural-adı> --pattern '<regex>' --placeholder-format 'mask_<kural-adı>_{sayac}'
```

Örnek: `--tip tc_kimlik_no --pattern '\b\d{11}\b' --placeholder-format 'mask_tc_kimlik_{sayac}'`.
Yer tutucu biçimi `_{sayac}` ile bitmelidir. Kurallar silinmez, yalnızca açılıp
kapatılır; böylece geçmiş eşlemeler bozulmaz.

**Yarıda kalan bir dışa aktarmayı kurtarmak:** Aynı proje ve branch için
yapılan bir sonraki tarama yarım kalan işlemi kendiliğinden kapatır ve önceki
çıktıyı geri yükler. Elle kurtarmak isterseniz uygulamayı durdurup şunu
çalıştırın:

```powershell
.venv\Scripts\python.exe -m app.cli recover-output --hedef <çıktı-klasörü> --application-stopped
```

---

## 6. Sorun giderme

### Kurulum betiği "This bundle requires Windows / Python 3.14" diyor

Makinedeki Python 3.14 ya da 64 bit değil. Doğru Python'u kurun ve 1.3'teki
gibi `-Python` ile tam yolunu verin.

### Betik çalıştırma izni hatası

Kurulum betiğini bu kılavuzdaki gibi
`powershell -ExecutionPolicy Bypass -File ...` ile çalıştırın. Uygulamayı her
zaman `.venv\Scripts\python.exe start.py` ile başlatın; bu komut izin
gerektirmez.

### Ön kontrolde `FAIL stage=settings reason=VLLM_ENABLED_false`

`.env` dosyasında `VLLM_ENABLED=false` yazıyor. Ön kontrol LLM açıkken
yapılır. LLM'i bilerek kapattıysanız bu satırı yok sayın.

### Ön kontrolde `FAIL stage=build_stamp`

`app` klasörü eksik ya da karışık kopyalanmış. Uygulamayı durdurun, klasörü
flash bellekten yeniden kopyalayın ve ön kontrolü tekrarlayın:

```powershell
Remove-Item -Recurse -Force C:\masking_system\masking_service\app
Copy-Item -Recurse E:\masking_system\masking_service\app C:\masking_system\masking_service
```

### Dışa aktarma "kod değişti" ya da 503 hatasıyla reddediliyor

Kod kopyalandıktan sonra uygulama yeniden başlatılmamış. Uygulamayı durdurup
yeniden başlatın.

### "no such table" hatası

Veritabanı yükseltilmemiş:

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe -m alembic upgrade head
```

### "database is locked" hatası

1. Yalnızca bir uygulama penceresinin açık olduğunu kontrol edin:
   `tasklist | findstr python`
2. `masking.db` dosyasını başka bir programda (ör. DB Browser) açık
   bırakmayın.
3. `C:\masking_system` klasörünü antivirüs gerçek zamanlı taramasından ve
   OneDrive/bulut eşitlemesinden hariç tutun.
4. Uygulamanın flash bellekten değil `C:\masking_system`'den çalıştığından emin
   olun.

### Dosyalar "yanıt kesildi" (`finish_reason=length`) nedeniyle karantinada

`.env` dosyasında `VLLM_DISABLE_THINKING=true` satırının olduğunu kontrol edin:

```powershell
Select-String -Path C:\masking_system\.env -Pattern "^VLLM_DISABLE_THINKING="
```

Yoksa ekleyip uygulamayı yeniden başlatın. Sorun sürerse vLLM sunucusunun
`--default-chat-template-kwargs '{"enable_thinking": false}'` ile
başlatılmasını sunucu yöneticisinden isteyin.

### Dosyalar LLM zaman aşımı nedeniyle karantinada

`VLLM_MAX_CONCURRENT_REQUESTS` sunucu için fazla yüksek. Değeri bir kademe
düşürün (ör. 4 → 2) ya da Bölüm 4'teki ölçümü tekrarlayın. Ardından uygulamayı
yeniden başlatın.

### Logda "spaCy NLP modeli yuklenemedi"

Kişi adı ve tarih tespiti çalışmıyor demektir. Bağımlılık kurulumunu (1.3)
tekrar çalıştırın.

### Dosya "Kodlanmış ... hassas veri" nedeniyle karantinada

Dosyada base64 ya da hex ile gizlenmiş bir parola, IP ya da kurumsal terim
bulunmuş. Değeri kaynaktan kaldırıp yeniden tarayın. Değer gerçekten hassas
değilse **Onay Bekleyenler** ekranında "Yanlış Alarm" ile serbest bırakın.

### Dışa aktarma çok yavaş

1. En ağır dosyaları görün: Bölüm 5'teki `llm-is-yuku` komutu. LLM'e istek
   göndermez.
2. Gereksiz ağır dosyaları (ör. sıkıştırılmış JavaScript kütüphaneleri)
   projeden çıkarın.
3. Hız ölçümünü (Bölüm 4) yapmadıysanız yapın.

### Sözlüğe eklenen bir terim çıktıda açık kalıyor

Teşhis betiği terimin sözlük kaydını ve dosyadaki geçişlerini kontrol eder.
Hiçbir şeyi değiştirmez. Çıktısında terim, dosya içeriği ya da dosya yolu
bulunmaz; çıktıyı geliştirici ekibe güvenle iletebilirsiniz.

```powershell
cd C:\masking_system\masking_service
.venv\Scripts\python.exe scripts\diagnose_term.py --term "terim" --file "C:\proje\config\app.env"
```

`--term` için terimi sözlükteki gibi yazın. `--file` için maskelenmemiş
**orijinal** projedeki dosyanın tam yolunu verin.

| Çıktıda | Anlamı ve çözümü |
|---|---|
| `sozluk_kaydi_sayisi=0` | Terim sözlükte yok. Kurumsal Terim Sözlüğü ekranından ekleyin. |
| `aktif=False` | Terim pasif (nedeni `pasif_nedeni` alanında). Terimi aktif yapın. |
| `kayitli_yazim_nfc=False` ya da `dosyadaki_ayrisik_isaret_sayisi` 0'dan büyük | Türkçe harfler farklı bir Unicode biçiminde kaydedilmiş. Ekranda aynı görünür ama eşleşmez. Genellikle Word, PDF ya da Excel'den kopyalanan terimlerde olur. Terimi elle yazarak yeniden ekleyin. |
| `dosya_haric_tutma_kuralina_uyuyor=True` | Dosya bir hariç tutma kuralına takılıyor, hiç taranmıyor. |
| `desenin_kacirdigi` 0'dan büyük | Terim dosyada geçiyor ama tarama deseni bu satırları yakalamıyor (`kacirilan_satirlar` hangi satırlar olduğunu gösterir). Çıktıyı geliştirici ekibe iletin. |

Terim ekledikten sonra uygulamayı yeniden başlatmanız gerekmez.

### "Bu proje/sicil/branch için zaten devam eden bir işlem var"

Aynı proje ve branch için başka bir tarama gerçekten çalışıyor.

- Tarama arayüzden başlatıldıysa ekranda **Öncekini durdur ve yeniden başlat**
  ya da **Devam eden taramayı izle** seçeneği çıkar.
- Tarama komut satırından başlatıldıysa o komutun bitmesini bekleyin ya da o
  pencerede `Ctrl+C`'ye basın.
