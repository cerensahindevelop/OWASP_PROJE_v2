# Faz 3 tasarım notu — proje düzeyinde terim sözlüğü ve parça bazlı maskeleme

**Durum:** TASLAK. Bu belge şu an yalnızca verilmiş kararları kaydeder. Tasarımın geri kalanı
(iki geçişli export, parça bazlı eşleştirme, TokenBoundaryValidator ile ilişki, Java/Spring
özel durumları, çakışma, geri dönüş) Faz 3 başında yazılacak ve onaya sunulacak.

## Karar kayıtları

### K1 — LLM kaynaklı terimler dosya yoluna da yansıtılır (1 Ekim 2026, ürün sahibi kararı)

**Bağlam.** Altın küme baseline'ı (`diagnostics/golden-baseline-20261001/RAPOR.md`, gözlem 4 ve 9):
- Sözlük terimleri bugün yolda maskeleniyor (`mapping_service.mask_relative_path`, yalnızca
  kurumsal terim ve runtime parametreleri).
- LLM kaynaklı terimler yolda hiç maskelenmiyor: `poseidon/` dizini ve
  `PoseidonGatewayClient.java` açık kalırken içerikte `class mask_proje_kod_adi_2` oluyor.

Sonuç hem sızıntı (yol, içerikten daha görünür bir kanal) hem de Java derleme hatası.

**Karar.** LLM kaynaklı terimlerin yola yansıması kabul edilebilir, hatta gerekli. Gerekçe: LLM
yeni metin üretmez, yalnızca mevcut bir değeri işaret eder; maskeyi kod üretir. Bu yüzden prompt
injection ile keyfi yol yazdırma riski yok.

**Koşullar:**
1. **Kaynak.** Yol maskelemesi dosya başına LLM bulgularından değil, Faz 3'teki donmuş proje
   terim sözlüğünden beslenir. İçerik ve yol aynı eşlemeyi kullanır.
2. **Yeni değişmez kural (kural 9).** Bir dosyanın içeriğinde maskelenen her terim, o dosyanın ve
   üst dizinlerinin yolunda da maskelenmiş olmalı. Sağlanamıyorsa dosya fail-closed engellenir.
   Tersi de geçerli: job sözlüğünde olmayan bir terim yolda maskelenmez.
3. **Faz 2'de yola yansıtma yok.** Proje sözlüğü henüz olmadığı için Faz 2 yalnızca içerik/yol
   uyuşmazlığını tespit eder ve ayrı bir `failed_check` koduyla raporlar (öneri:
   `yol_icerik_uyusmazligi`). Böylece sorunun yaygınlığı ölçülür. Bu kodun dosyayı engelleyip
   engellemeyeceği Faz 2 planında ayrıca onaya sunulacak; mevcut davranışı korumak için
   varsayılan yalnızca raporlamadır.
4. **Parça bazlı ve yazım stilini koruyan yol maskesi.** `PoseidonGatewayClient.java` →
   `MaskBGatewayClient.java`; `com/karayel/poseidon/` → `com/maskA/maskB/`. `package` bildirimiyle
   tutarlı olmalı.
5. **Yola özgü doğrulamalar:**
   - Java: public sınıf adı = dosya adı, `package` = dizin yapısı
   - Çakışma: iki farklı kaynak yol aynı maskeli yola düşmez
   - Büyük/küçük harf duyarsız dosya sistemlerinde çakışma (`MaskA` ↔ `maska`)
   - Yol uzunluğu ve geçersiz karakterler
   - Yola referans veren dosyalar: `pom.xml`, `build.gradle`, resource yolları, `import`'lar
6. **Geri alma.** Yol eşlemesi imzalı manifestte tutulur; manifestin içeriği K2'de tanımlıdır.
   Geri alma testi dosya içerikleri kadar dosya ve dizin adlarını da kapsar (`diff -r`).
7. **Loglama.** Rapor ve loglara orijinal yollar yazılmaz.
   - **Kapsam:** log dosyaları, export raporu ve çıktı paketine giren her şey.
   - **Kapsam dışı:** DB. `AuditLog`/`AuditWarning` ve inceleme ekranı orijinal yolla
     çalışmaya devam eder; DB'ye dokunulmaz.
   - **Uygulama:** Faz 2 içinde, bayraksız. Loglara orijinal yol yerine maskeli yol ile DB
     kaydıyla eşleştirilebilecek kısa bir dosya kimliği yazılır, böylece hata ayıklama
     zorlaşmaz. Bu düzeltmeden sonra `test_report_and_logs_do_not_contain_original_paths`
     xfail'den çıkar.

**Kabul testleri:** `masking_service/tests/test_golden_path_acceptance.py`

| Test | Koşul | Bugün |
|---|---|---|
| `test_content_masked_terms_never_appear_in_output_paths` | 1, 2 | xfail |
| `test_path_masks_preserve_naming_style` | 4 | xfail |
| `test_java_public_class_matches_file_name` | 5 | xfail |
| `test_java_package_matches_directory` | 4, 5 | xfail |
| `test_masked_java_project_compiles` | 5 | xfail |
| `test_report_and_logs_do_not_contain_original_paths` | 7 | xfail (Faz 2'de kalkacak) |
| `test_restore_recreates_file_and_directory_names_byte_for_byte` | 6 | geçiyor (regresyon) |
| `test_output_paths_do_not_collide_even_case_insensitively` | 5 | geçiyor (regresyon) |
| `test_output_paths_are_portable` | 5 | geçiyor (regresyon) |
| `test_non_dictionary_path_parts_are_kept` | 2 (tersi) | geçiyor (regresyon) |
| `test_pom_group_id_points_to_existing_package_directory` | 5 | geçiyor (regresyon) |

xfail testleri `strict=True`: Faz 3 bir testi geçirdiğinde test takımı kırılır ve işaret
kaldırılır. Beklenen stil `expected.json` → `yol_beklentileri.stil` içindedir. Kesin placeholder
biçimi Faz 3 notunda belirlenecek; desenler buna göre güncellenebilir.

**Bilinen engeller (bugünkü kod):**
- `path_placeholders.py` yol token'larını `mask_<önek>_<n>` dilbilgisiyle çözüyor. `MaskB`,
  `maskA` gibi stil koruyan token'lar için yeni bir dilbilgisi gerekir; eski çıktıların geri
  alınabilmesi için eski dilbilgisi de korunmalı.
- `TokenBoundaryValidator` bugün tüm identifier'ı maskeliyor (`getTcKimlikNo` →
  `mask_kurumsal_ifade_7`). Parça bazlı maskeleme bu genişletmeyle birlikte tasarlanmalı.
- Orijinal yolu yazan yerler:
  - LLM logları: `llm_runtime.LLMScanMetrics`, `llm_request`/`llm_file ... file=...`
  - `detectors.DetectionOrchestrator.scan` içindeki `detector_crash ... file=...`
  - Export raporundaki sözdizimi doğrulama uyarıları

  Hepsinin maskeli yola geçmesi gerekir. Maskeli yol (`prep.masked_rel`) bugün de var ama
  yalnızca sözlük terimlerini gizliyor; LLM kaynaklı terimler Faz 3'e kadar maskeli yolda da açık
  kalır. Bu yüzden kural 7 testi Faz 2'den sonra da, yol maskelemesi Faz 3'te tamamlanana kadar,
  LLM kaynaklı terimler için geçemeyebilir. Faz 2 planında bu ayrım netleştirilecek.

### K2 — Manifestte yol eşlemesi: maskeli yol + placeholder + HMAC (1 Ekim 2026, ürün sahibi kararı)

**Bağlam.** K1/6 yol eşlemesinin imzalı manifestte tutulmasını istiyor. Ancak manifest
(`.masking-integrity.json`) çıktı paketiyle birlikte dışarı çıkıyor ve `integrity_manifest.py`
bilinçli olarak hiçbir orijinal yol veya değer içermiyor. Orijinal yolu manifeste yazmak,
maskelenmiş terimleri paketin içinde sızdırır.

**Karar.** Manifest her dosya için şunları imzalı olarak tutar:
- maskeli göreli yol,
- o yolda kullanılan placeholder token'ları,
- orijinal göreli yolun **job anahtarlı HMAC özeti**.

Kurallar:
- Orijinal değerler yalnızca DB'deki eşlemede kalır.
- **HMAC anahtarı hiçbir koşulda pakete, manifeste veya loglara girmez.** Manifestte yalnızca
  özet bulunur.
- Geri alma, DB eşlemesiyle çözdüğü orijinal yolun HMAC'ini yeniden hesaplar ve manifesttekiyle
  karşılaştırır. Tutmazsa **fail-closed** durur: hedefe yazmaz, nedeni raporlar.

**Uygulama notu (Faz 3 tasarımında ayrıntılanacak).** Bugünkü imza ve kaynak özeti
(`integrity_manifest._signature`, `source_tag`) zaten `core.crypto.hash_value` ile, sunucudaki
`SECURITY_ENCRYPTION_KEY` ve bağlam kimliğiyle HMAC-SHA256 olarak üretiliyor. Yol özeti de aynı
mekanizmayla, job kimliğini mesaja katarak üretilecek (örneğin
`hash_value(context_id, "path-v1:" + job_id + ":" + orijinal_yol)`). Bu yeni bir anahtar
dağıtımı gerektirmez; anahtar sunucudan hiç çıkmaz.

Manifest sürümü yükseltilir. Eski sürüm manifestli paketler bugünkü davranışla geri alınmaya
devam eder (geriye dönük uyumluluk).

**Kabul kriterleri (Faz 3'te test olarak eklenecek):**
- Manifestte hiçbir orijinal yol parçası ve anahtar materyali yok.
- Manifestteki yol özeti değiştirilmiş bir pakette geri alma fail-closed duruyor.
- DB'de eşleme değiştirilirse (yanlış orijinal) geri alma fail-closed duruyor.

## Çözülen sorular

- **S1** (manifestte ne tutulacak) → K2.
- **S2** (DB'deki kaynak yol) → kural 7 DB'yi kapsamaz; bkz. K1/7.
- **S3** (loglama düzeltmesinin zamanı) → Faz 2, bayraksız, kısa dosya kimliğiyle; bkz. K1/7.
