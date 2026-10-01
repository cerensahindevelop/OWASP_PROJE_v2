# Faz 0 baseline — altın küme ölçümü (1 Ekim 2026)

Bu klasördeki sayılar **gerçek modelin değil, boru hattının mekaniğinin** ölçüsüdür.
`off` modunda LLM kapalıdır. `stub` modunda LLM yanıtları `tests/fixtures/golden/expected.json`
içinde senaryo olarak yazılmıştır. Gerçek Qwen/vLLM baseline'ı intranette `--llm real` ile
alınmalıdır (bkz. bölüm 4). Fazlar arası karşılaştırma için geçerli olan: aynı senaryo ile
önce/sonra.

## 1. Nasıl üretildi

Ortam: Linux, Python 3.11.15, spaCy `en_core_web_lg`, geçici SQLite DB ve geçici anahtar
(üretim DB'si ve `.env` kullanılmadı). Her mod için aynı girdiyle 3 koşu.

```bash
cd masking_system/masking_service
python scripts/measure_golden.py --llm off  --runs 3 --out ../diagnostics/golden-baseline-20261001 --name off
python scripts/measure_golden.py --llm stub --runs 3 --out ../diagnostics/golden-baseline-20261001 --name stub-hata-0
python scripts/measure_golden.py --llm stub --runs 3 --stub-error-rate 0.02 --out ../diagnostics/golden-baseline-20261001 --name stub-hata-2
python scripts/measure_golden.py --llm stub --runs 3 --stub-error-rate 0.10 --out ../diagnostics/golden-baseline-20261001 --name stub-hata-10-ek
```

Ayrıntılı çıktılar: `<ad>.json` (dosya başına sonuç, LLM kullanımı, sızıntı yerleri) ve `<ad>.md`.

## 2. Baseline

Altın küme 10 metin dosyasından oluşur: 6 Java, 1 properties, 1 JSON, 1 SQL, 1 README.

| Metrik | off | stub %0 | stub %2 | stub %10 (ek) |
|---|---|---|---|---|
| Onay kuyruğu oranı | 0% (0/10) | 10% (1/10) | 10% (1/10) | 30% (3/10) |
| Çıktı dışında kalan oran | 0% | 10% | 10% | 30% |
| Nedene göre | – | llm_denetimi=1 | llm_denetimi=1 | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 |
| LLM isteği (dosya başına) | – | 24 (2,4) | 24 (2,4) | 25 (2,5) |
| Dosya başına tarama (tespit+denetim) | – | 2,4 | 2,4 | 2,3 |
| Dosya başına LLM sn p50 / p95 (stub) | – | 0,55 / 0,90 | 0,51 / 0,84 | 0,49 / 1,88 |
| Canary sızıntısı | **3 kısmi** (`cnry-db01`, `cnry-gw01`, `hakan.yilmaz`) | 0 | 0 | 0 |
| Terim sızıntısı (yayınlanan dosyalarda) | tckimlik 5, Poseidon 21, Ayşe 2, adres 1 | tckimlik 5 (`TC_KIMLIK_NO` 4, `tc-kimlik` 1) | aynı | aynı |
| Maskelenmemesi gerekenin maskelenmesi | `Teknik sorumlu`, `Veri merkezi` (Presidio PERSON) | `UserService` | `UserService` | `UserService` |
| Geri alma bayt farkı | 0/10 | 0/9 | 0/9 | 0/7 |
| javac (maskeli çıktı) | başarılı | **başarısız (2 hata)** | başarısız (2) | başarısız (4, 1 Java dosyası karantinada) |
| Determinizm (Jaccard min / aynı çıktı) | 1,0 / 10/10 | 1,0 / 9/9 | 1,0 / 9/9 | 1,0 / 7/7 |

Not: stub'da hata, parça metninin hash'ine bağlı ve deterministik. %2 koşusunda 72 isteğin hiçbiri
hataya denk gelmedi (beklenen ≈1,4; olasılık ≈%24). Sonuç %0 ile aynı çıktı. Kök neden 3'ün
mekanizmasını görmek için %10'luk ek koşu yapıldı. Stub süreleri gerçek model gecikmesini
yansıtmaz.

## 3. Gözlemler (sonraki fazlara girdi)

1. **Çok satırlı denetim alıntısı dosyayı karantinaya alıyor (kök neden 1).** README'deki iki
   satırlık adresi tespit kaçırıyor, denetim yakalıyor. Otomatik düzeltme
   `cok_satirli_alinti` ile reddediyor, dosya `llm_denetimi` ile onaya düşüyor. → Faz 2.3
2. **`dusuk` bulgu iki tur LLM'e mal oluyor (kök neden 2).** "Ayse Demir" tespitte `dusuk` olduğu
   için yok sayılıyor; denetim yeniden yakalıyor; Faz E düzeltip yeniden denetliyor. Dosya
   yayınlanıyor ama bir denetim turu fazla yapılıyor (dosya başına 2,4 tarama).
3. **Kısmi canary sızıntısı: birleştirme mantığı.** Sözlük terimi `karayel`, `cnry-db01.karayel.intra`
   içindeki alt aralığı kazanıyor (`_AUTHORITY_RANK`: kurumsal > sezgisel, uzunluktan önce).
   LLM'in tam host bulgusu çakışmada kaybediyor; host kısa adı açık kalıyor. LLM kapalıyken
   bu sızıntı çıktıya gidiyor. Stub'da denetim varyantı yakalayıp Faz E kapatıyor. Gerçek
   modelin bunu yakalayacağı garanti değil. → Faz 2 / Faz 3 tasarım notu
4. **Derleme kırılıyor: sınıf adı maskeleniyor, dosya adı maskelenmiyor.**
   `PoseidonGatewayClient.java` içinde `class mask_proje_kod_adi_2`. Yol maskelemesi yalnızca
   sözlük ve runtime terimlerini kapsıyor. Dosya adı aynı zamanda "Poseidon"u sızdırıyor. → Faz 3
5. **Tüm-identifier maskeleme Java semantiğini bozuyor ama derleniyor.**
   - `getTcKimlikNo` → `mask_kurumsal_ifade_7`, `tcKimlikNo` → `mask_kurumsal_ifade_6`: getter/setter
     ve JPA/Jackson özellik adı ilişkisi kayboluyor.
   - `@GetMapping("/{tcKimlikNo}")` → `"mask_kurumsal_ifade_9"`: string'in tamamı değişiyor,
     route şablonu bozuluyor.
   - `TC_KIMLIK_NO` ve `tc-kimlik` sözlük terimiyle eşleşmiyor ve açık kalıyor.

   → Faz 3'ün çözmesi gereken somut örnekler.
6. **Generic yanlış pozitifler.**
   - LLM tarafı (stub senaryosu): `UserService` maskeleniyor, sınıf adı değiştiği için javac kırılıyor.
   - Presidio tarafı (LLM kapalıyken de): `Teknik`, `Veri merkezi` gibi düz metin kelimelerini
     PERSON sayıyor. → Faz 4.2 (`PRESIDIO_MIN_SCORE`)
7. **Faz E'deki hata, kalıcı kodu yanıltıyor.** %10 koşusunda `MusteriService.java` için
   yeniden denetim hata verdi. Dosya `llm_denetimi_tamamlanamadi` değil `llm_denetimi` koduyla
   kaydedildi; gerçek neden yalnızca `auto_remediation=failed check=llm_denetimi_tamamlanamadi`
   AuditLog kaydında görünüyor. `scripts/failed_check_summary.py` bu kayıtları ayrıca sayar.
8. **Geri alma %100 ve determinizm 3/3** bütün modlarda sağlanıyor: yayınlanan her dosya bayt
   bayt geri alındı, çıktılar koşular arasında birebir aynı.

## 4. Intranette yapılacaklar

### 4.1 Dağıtım (önce)

TypeError olayından çıkan ders (bölüm 5): **seçili dosyaları değil, `app/` klasörünün tamamını
aynı commit'ten kopyalayın.** Faz 0 için ayrıca şunlar gerekli:

- `alembic/versions/e3a7c1f9d2b5_audit_warning_failed_check.py`
- `scripts/measure_golden.py`
- `scripts/failed_check_summary.py`
- `tests/fixtures/golden/` (yalnızca ölçüm için; testlerin geri kalanı gerekmez)

```powershell
cd C:\masking\masking_service
# 1) Veritabanini yedekleyin, sonra yeni kolonu ekleyin (yalnizca ekleme yapar)
Copy-Item ..\masking.db ..\masking.db.yedek-faz0
.venv\Scripts\python.exe -m alembic upgrade head
# 2) Backend ve arayuzu yeniden baslatin (eski kod bellekte kalmasin)
# 3) Kod/ayar yolu saglam mi? RESULT=OFFLINE_OK beklenir
.venv\Scripts\python.exe scripts\check_llm_preflight.py
```

### 4.2 Gerçek modelle altın küme baseline'ı

```powershell
.venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-<tarih> --name real
```

Gecici DB ve gecici anahtar kullanir; `.env`'den yalnizca `VLLM_*` ayarlari okunur. `javac` yoksa
derleme satiri "atlandi" olur. 3 kosu arasindaki Jaccard degeri modelin determinizmini gosterir
(stub'da 1,0).

### 4.3 3/4 sonucunu veren gerçek projede dağılım

Normal export'u web arayüzünden ya da CLI ile çalıştırın:

```powershell
.venv\Scripts\python.exe -m app.cli export --kaynak <proje> --hedef <cikti> --proje <ad> --sicil <sicil> --branch <branch>
```

Export raporu artık "Ciktiya alinmama nedenleri" ve "LLM kullanimi" satırlarını içerir. Ardından:

```powershell
.venv\Scripts\python.exe scripts\failed_check_summary.py --son --proje <ad>
```

Bu betik DB'yi salt okunur açar. Dosya yolu, değer veya gerekçe metni yazdırmaz; çıktısı
paylaşılabilir.

### 4.4 Sonuçları yorumlama

| Kod | Anlamı | Hangi faz |
|---|---|---|
| `tespit_katmani` + hata sınıfı `TypeError`/`AttributeError` | Kod veya sürüm uyumsuzluğu. Politika sorunu değil. | **Önce dağıtım** (4.1). Hiçbir faz bunu düzeltmez. |
| `llm_tespit` + `ReadTimeout`/`HTTPStatusError_5xx`/`zaman_asimi`/`yanit_kesildi` | Tespit isteği tamamlanamadı (kök neden 3) | Faz 1 (ayarlar), Faz 4.4 |
| `llm_denetimi_tamamlanamadi` | Denetim isteği tamamlanamadı (kök neden 3) | Faz 1, Faz 4.4 |
| `llm_denetimi` | Doğrulanmış denetim alıntısı otomatik düzeltilemedi (kök neden 1/2). "Otomatik düzeltme başarısızlık nedeni" satırına bakın: `cok_satirli_alinti`/`uzun_alinti`/`daraltma`/`maskeleme` → Faz 2. `llm_denetimi_tamamlanamadi` → aslında kök neden 3 (gözlem 7). | Faz 2 |
| `inceleme` | `VLLM_LOW_CONFIDENCE_ACTION=review` ise düşük güvenli bulgu | Faz 4.1 |
| `acik_terim` | Sözlük terimi son kontrolde açık kaldı (çoğunlukla varyant) | Faz 3 |
| `sozdizimi`, `geri_donus`, `tutarlilik` | Maskeleme kodu bozdu ya da tutarsız kaldı | Faz 3, Faz 4.2 |
| `kodlanmis_veri`, `lock_bulgu`, `arsiv`, `boyut`, `kodlama` | Bilinçli güvenlik veya kapsam kuralı. LLM ile ilgisiz. | – |
| `bilinmiyor` | Migrasyondan önceki çalışma | Yeniden export |

**Karar kuralı (öneri):**
- `tespit_katmani` + `llm_tespit` + `llm_denetimi_tamamlanamadi` toplamı karantinanın yarısından
  fazlaysa önce dağıtım ve Faz 1 gelir; Faz 2'nin etkisi bu gürültü altında ölçülemez.
- `llm_denetimi` baskınsa plan değişmeden Faz 2 ile devam edilir.
- `acik_terim`/`sozdizimi` baskınsa Faz 3 öne alınabilir.

## 5. TypeError notu (`diagnostics/llm-typeerror-20260928/RAPOR.md`)

**Kök neden Python 3.11 ↔ 3.14 farkı mı?** Kanıtlar buna işaret etmiyor:
- Tam test takımı (1454 test) Python **3.14.7** ve 3.11.15'te geçiyor.
- `scripts/check_llm_preflight.py` iki sürümde de `RESULT=OFFLINE_OK` veriyor (tespit, DB
  talimatlı tespit, denetim).
- Hata `requests=0` ile, yani HTTP çağrısından önce oluşmuş. Bu yolda yalnızca stdlib ve
  pydantic ayar nesnesine öznitelik erişimi var; 3.14'e özgü bir API kullanılmıyor.
- Windows + Python 3.14.3 bu ortamda test edilemedi. Varsayım: platform farkı bu yolda
  davranışı değiştirmez.

**Kütüphane sürümü mü?** Olası değil. `httpx` hiç çağrılmamış. Ayar türü hatası (ör. metin olarak
eşzamanlılık) önceki raporda denendi; denetimi de bozduğu için gözlenen desene (yalnızca tespit
hata veriyor, denetim başarılı) uymuyor.

**En olası neden:** intranette karışık sürümde kod (seçili dosyaların kopyalanması ya da yeniden
başlatılmamış backend). Önceki raporda eski parametre listeli bir `build_detection_request` ile
**birebir aynı desen** üretilmişti.

**Hangi commit'te düzeltildi?** Repo'dan belirlenemiyor. Olay, repo'nun ilk commit'inden
(`68edc8e`, 28.09) önce yaşanmış. O commit zaten tanılama eklerini (`detector_crash ... frames=`)
ve yanıt ayrıştırma düzeltmesini içeriyor. Kesin hata satırı intranetten hiç alınmadı. Ayrıca
o tarihten sonra modüller arası LLM çağrı imzaları dört commit'te değişti:
`feb39f6`, `52d369d`, `93b209e`, `3015498`. Bu yüzden kısmi kopyalama riski sürüyor. Bugün tek bir
eski modülü güncel kodla karıştırmak dosya başına TypeError değil import hatası veriyor
(denendi: `llm_recognizer`, `llm_runtime`, `detectors` için `ImportError`); preflight bunu
yakalar.

**Ne gerekiyor:**
1. `app/` klasörünün tamamını tek commit'ten dağıtın ve backend'i yeniden başlatın.
2. Preflight'ı çalıştırın; `OFFLINE_OK` dışındaki her sonuçta `FAIL ... frames=` satırını paylaşın.
3. Gerçek export'tan sonra `failed_check_summary.py` çıktısında `tespit_katmani` altında
   `TypeError` görünüyorsa sorun sürüyor demektir. Backend logundaki `detector_crash ... frames=`
   satırı dosya:satır:fonksiyon bilgisini verir (içerik içermez).

## 6. Sınırlamalar

- Stub senaryosu tarafımızdan yazıldı. Kuyruk oranı senaryonun yansımasıdır, model kalitesinin
  ölçüsü değildir.
- Altın küme küçük (10 dosya). Yüzdeler tek dosyayla 10 puan oynar.
- Stub denetimi açık kalan varyantları yakalıyor. Gerçek model kaçırabilir; bu durumda
  gözlem 3'teki kısmi sızıntılar çıktıya gider. Gerçek koşuda (4.2) canary satırı bu yüzden
  kritik.
