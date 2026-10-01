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
6. **Geri alma.** Yol eşlemesi imzalı manifestte tutulur (bkz. açık soru S1). Geri alma testi
   dosya içerikleri kadar dosya ve dizin adlarını da kapsar (`diff -r`).
7. **Loglama.** Rapor ve loglara orijinal yollar yazılmaz.

**Kabul testleri:** `masking_service/tests/test_golden_path_acceptance.py`

| Test | Koşul | Bugün |
|---|---|---|
| `test_content_masked_terms_never_appear_in_output_paths` | 1, 2 | xfail |
| `test_path_masks_preserve_naming_style` | 4 | xfail |
| `test_java_public_class_matches_file_name` | 5 | xfail |
| `test_java_package_matches_directory` | 4, 5 | xfail |
| `test_masked_java_project_compiles` | 5 | xfail |
| `test_report_and_logs_do_not_contain_original_paths` | 7 | xfail |
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

  Hepsinin maskeli yola geçmesi gerekir. Not: maskeli yol (`prep.masked_rel`) bugün de var;
  yalnızca sözlük terimlerini gizliyor, bu yüzden bu değişiklik Faz 3'ten önce de yapılabilir
  (bkz. S3).

## Açık sorular

- **S1 — Manifestte ne tutulacak?** `integrity_manifest.py` bilinçli olarak orijinal yol veya
  değer içermiyor ve manifest çıktı paketiyle birlikte dışarı çıkıyor. Orijinal yolu manifeste
  yazmak, maskelenmiş terimleri paketin içinde sızdırır.

  Öneri: manifest her dosya için maskeli yolu, yolda kullanılan placeholder token'larını ve
  orijinal göreli yolun job anahtarlı HMAC özetini imzalı tutsun. Orijinal değerler bugünkü gibi
  yalnızca DB'deki eşlemede kalsın. Geri alma, DB'deki eşlemeyle çözdüğü yolu HMAC ile
  doğrular; eşleşmezse fail-closed durur. Onayınız gerekiyor.

- **S2 — DB'deki kaynak yol.** `AuditLog.file_path` ve `AuditWarning.file_path` kaynak yolu
  tutuyor; inceleme ekranı bu yolla çalışıyor ve `AuditWarning.output_path` maskeli yolu ayrıca
  saklıyor. Kural 7 ("loglara orijinal yol yazılmaz") uygulama loglarını ve raporu mu kapsıyor,
  yoksa DB'deki denetim kaydını da mı? DB'deki düz metin saklama bilinçli bir tasarım kararı
  (değişmez kural 6) olduğu için, aksi belirtilmedikçe DB'ye dokunulmayacak.

- **S3 — Loglama düzeltmesinin zamanı.** Log ve rapordaki yolları maskeli yola çevirmek Faz 3'e
  bağlı değil. Faz 2 ile birlikte (bayraksız, davranış değiştirmeyen bir düzeltme olarak)
  yapılmasını öneriyorum.
