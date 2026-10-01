# Faz 2a raporu (1 Ekim 2026)

Dal: `claude/blissful-mccarthy-7qtl2x` (başlangıç `main` = `b669895`). Ölçümler altın kümede
(`tests/fixtures/golden`, 11 dosya), stub LLM ile, her biri 3 koşu. Ham çıktılar:
`once/` (başlangıç) ve `sonra/` (faz sonu).

## 1. Yapılanlar

| Commit | Madde | İçerik |
|---|---|---|
| `a5c92b5` | – | Başlangıç ölçümü. Faz 0 baseline'ıyla aynı (süreler hariç). |
| `cc606db` | a | LLM09: denetim alıntıları `kaynak_motor="llm_audit"`, güven `orta`. Identifier'a genişletme yok, sezgisel otorite, registry'de en fazla `weak`. İnceleme ekranında insanın onayladığı değerler `dictionary` olarak kalır. |
| `240c1d1` | – | Faz 3 yol kabul testlerine ön koşul eklendi: yol terimi geçen dosyalar çıktıda olmalı. Bkz. bölüm 4. |
| `38b1fe4` | b | Log ve raporda kaynak yol yerine `maskeli/yol#<12 hex>`. `dosya-kimligi` CLI komutu. Kök klasörler yol maskelemesinden geçiyor. Kural 7 testi xfail'den çıktı. |
| `2ecae39` | d/c | Ortak identifier ayrıştırıcı: `app/services/identifier_parts.py`. |
| `e400296` | c | `yol_icerik_uyusmazligi` ölçümü; yalnızca raporlanır, dosyayı engellemez. |
| `59b68dd` | d | `SCAN_GENERIC_COMPOUND_FILTER` (varsayılan `false`). |
| `49c5bcb` | e | `app/BUILD_STAMP.json`. Açılışta `build state=...` logu. Uyuşmazlıkta export 503 ile reddedilir. `/health` alanı eklendi. |
| `6a43e63` | e | Preflight'a damga ve saf AST imza kontrolü eklendi. Intranet kontrol listesi güncellendi. |

Testler:

| Python | Sonuç |
|---|---|
| 3.11.15 | 1533 passed, 1 skipped, 5 xfailed |
| 3.14.7 | 1533 passed, 1 skipped, 5 xfailed |
| Başlangıç (3.11) | 1459 passed, 1 skipped, 6 xfailed |

## 2. Metrikler: başlangıç → faz sonu

3 koşunun üçü de her satırda aynı sonucu verdi.

| Metrik | stub %0 önce | stub %0 sonra | stub %2 önce | stub %2 sonra | off önce | off sonra | stub %0 sonra, generic filtre açık |
|---|---|---|---|---|---|---|---|
| Hazır / taranan | 10/11 | 7/11 | 9/11 | 7/11 | 11/11 | 11/11 | 7/11 |
| Onay kuyruğu | %9 (1) | **%36 (4)** | %18 (2) | %36 (4) | %0 | %0 | %36 (4) |
| Nedene göre | llm_denetimi=1 | llm_denetimi=4 | llm_denetimi=2 | llm_denetimi=4 | – | – | llm_denetimi=4 |
| Yol/içerik uyuşmazlığı (ölçüm) | – | 1 dosya / 1 terim | – | 1 / 1 | – | 0 / 0 | **0 / 0** |
| Canary sızıntısı | 0 | 0 | 0 | 0 | 3 | 3 | 0 |
| `must_not_mask` ihlali | UserService | UserService | UserService | UserService | Teknik sorumlu, Veri merkezi | aynı | **0** |
| Geri alma bayt farkı | 0/10 | 0/7 | 0/9 | 0/7 | 0/11 | 0/11 | 0/7 |
| javac (hata, eksik java) | başarısız (2, 0) | başarısız (3, 2) | başarısız (4, 1) | başarısız (3, 2) | başarılı | başarılı | başarısız (2, 2) |
| Determinizm (Jaccard, aynı dosya) | 1,0, 10/10 | 1,0, 7/7 | 1,0 | 1,0 | 1,0 | 1,0 | 1,0, 7/7 |
| LLM isteği (dosya başına) | 28 (2,55) | 24 (2,18) | 29 (2,64) | 24 (2,18) | – | – | 24 (2,18) |

`off` modunda (LLM kapalı) canary sızıntısı ve ihlaller baseline'daki gibi duruyor. Faz 2a bu
modu hedeflemiyor.

### Madde (a)'nın onay kuyruğuna etkisi

| Satır | Kuyruk (stub %0) | Açıklama |
|---|---|---|
| (a) öncesi | %9 (1) | `README.md` (çok satırlı alıntı) |
| (a) sonrası, toplam | %36 (4) | +3 dosya: `MusteriService.java`, `PoseidonGatewayClient.java`, `application.properties` |
| Bunun içinden `llm_audit` → `weak` yayılmasının payı | **0** | Deney: `llm_audit` registry'ye hiç alınmasa da kuyruk %36 (4), çıktı aynı |
| Bunun içinden sınır yetkisinin kalkmasının payı | +3 dosya | Otomatik düzeltme `maskeleme` kontrolünde kalıyor |

Mekanizma şöyle:
- Denetim LLM'i şu değerleri alıntılıyor:
  - çıplak kod identifier'ları: `PoseidonGatewayClient`, `poseidonGatewayClient`
  - identifier parçaları: `poseidon`, `cnry-db01` (`cnry-db01.mask_kurumsal_ifade_N.intra` içinde)
- Eski kodda bu alıntılar Katman 1 yetkisi alıyordu. Doğrulayıcı tüm identifier'ı ya da çıplak
  kod ifadesini maskeliyordu.
- Artık tespit katmanındaki LLM bulgularıyla aynı kural geçerli: sezgisel kaynak çıplak kod
  identifier'ını maskeleyemez. Dosya sızdırılmıyor, fail-closed olarak onaya düşüyor.
- Canary sızıntısı 0'da kaldı.
- Gerçek projede de benzer bir artış beklenir. Bunu azaltmak Faz 2b'nin hedefi: denetimi aday
  kaynağına çevirmek ve `mask_known_values`'u hepsi-ya-da-hiç olmaktan çıkarmak.

Yöntem: yayılmasız deney, `consistency_masking.registry_authority`'nin `llm_audit` için `None`
döndürdüğü bir import kancasıyla `measure_golden.py --llm stub --runs 1` çalıştırılarak yapıldı.
Kod değişikliği yapılmadı.

### Diğer ölçüm notları

- **c.** Stub senaryosunda 1 uyuşmazlık var: `UserService.java`. Bunun nedeni generic yanlış
  pozitif (değer, dosya adının kendisi). `PoseidonGatewayClient.java` karantinada olduğu için
  ölçülmüyor; ölçüm yalnızca yayınlanan dosyaları kapsar.
- **d.** Bayrak açıkken:
  - `UserService` ihlali 1'den 0'a indi.
  - Uyuşmazlık 1'den 0'a indi.
  - javac hata sayısı 3'ten 2'ye düştü. Kalan hatalar karantinadaki iki Java dosyasına referanstan
    geliyor.
  - Canary sızıntısı 0'da kaldı.

## 3. Değişmez kuralların kontrolü

1. **Geri alma: korundu.** Tüm koşularda bayt farkı 0. `test_restore_recreates_file_and_directory_names_byte_for_byte` geçiyor.
2. **Fail-closed: korundu, güçlendi.**
   - (a) belirsiz durumda engelliyor.
   - (e) uyuşmazlıkta ve yeniden başlatılmamış backend'de export'u reddediyor. Okunamayan damga
     `mismatch` sayılıyor.
   - Kök maskeleme başarısız olursa `<gizlendi>` yazılıyor.
   - (c) ölçüm hatası export'u etkilemiyor. Ölçüm bir güvenlik kapısı değil.
3. **LLM çıktı sözleşmesi: korundu, güçlendi.** LLM çıktısı artık Katman 1 yetkisi almıyor.
4. **Determinizm: korundu.** 3/3, Jaccard 1,0. Dosya kimliği anahtarlı ve deterministik bir HMAC.
5. **Bayrak: uyuldu.**
   - (d) bayrak arkasında, varsayılanı kapalı.
   - (a) ve (b) karar gereği bayraksız.
   - (c) çıktıyı değiştirmiyor.
   - (e): damga yoksa davranış değişmiyor (bugünkü kurulumlar). Uyuşmazlıkta red, verdiğiniz
     "orta yol" kararı.
6. **DB: dokunulmadı.** Migrasyon yok. AuditLog/AuditWarning orijinal yolla çalışıyor.
7. **Testler: hiçbiri silinmedi.** Uyarlananlar bölüm 4'te; **onayınızı bekliyor**.
8. **Commit ve push: uyuldu.** Her adım ayrı commit. Push yalnızca bu dala.
9. **Kural 9: yalnızca ölçülüyor** (c).

## 4. Onayınızı bekleyen test değişiklikleri

Aşağıdaki testlerin hiçbiri silinmedi veya gevşetilmedi. Her biri onayladığınız kararla doğrudan
çelişen bir beklentiyi taşıyordu. Kontrolü koruyup yeni biçime uyarladım ve çoğuna ters yönde
("kaynak yol geçmiyor") bir assert ekledim.

| Test | Eski beklenti | Yeni beklenti | Karar |
|---|---|---|---|
| `test_export_report_formatter::test_quarantined_and_syntax_and_round_trip_failures_are_all_reported` | Raporda `broken.py dosyasinda ...` | `broken.py#<id> dosyasinda ...` | S3 |
| `test_llm_chunking_runtime::test_detection_and_audit_jobs_share_request_limit_and_log_usage` | Logda `file='audit.txt'` | Logda etiket var, kaynak yol yok | S3 |
| `test_llm_response_validation::test_unexpected_crash_logs_source_location_without_content_or_exception_text` | `detector_crash` logunda `test.txt` | Logda etiket var, kaynak yol yok | S3 |
| `test_llm_detector::test_vllm_failure_is_reported_as_error_not_raised` | Hata metninde `a.py` | `(dosya=mask/a.py#<id>)`, `dosya=a.py` yok | S3 |
| `test_api::test_health` | Yanıt tam olarak `{"status": "ok"}` | `status == "ok"` ve `build` alanı var | e (health'te görünsün) |
| `test_golden_path_acceptance` (4 Faz 3 testi) | – | Ön koşul eklendi: yol terimli dosyalar READY olmalı | Testleri güçlendirir |

Son satırın nedeni: (a)'dan sonra `PoseidonGatewayClient.java` karantinaya düştü. İki strict xfail
test boş kümeye karşı XPASS verdi. Ön koşul olmasaydı xfail işaretini kaldırmak gerekecekti ve
test bir Faz 3 kriterini yanlışlıkla "geçti" sayacaktı.

## 5. İncelemeniz için: genel parça sözlüğü (d)

`app/services/term_classifier.py`, `_GENERIC_PARTS_TR` ve `_GENERIC_PARTS_EN`. Karşılaştırma
Türkçe karakterleri ASCII'ye katlayarak yapılıyor (`kayıt` = `kayit`). Kural: bileşik ad en az iki
parçalı, boşluksuz olmalı ve **tüm** parçaları listede olmalı.

**Türkçe, teknik terimler** (yalın + iyelik ekli):
- servis(i), islem(i, leri), kayit / kaydi / kayitlari, sorgu(su), liste(si), bilgi(si, leri)
- istek / istegi, yanit(i), cevap / cevabi, hata(si), durum(u), tip(i), tur(u), kod(u)
- no, numara(si), ad(i), isim / ismi, yonetici(si), yonetim(i), kullanici(si), veri(si)
- tablo(su), alan(i), deger(i), parametre(si), ayar(i, lari), kural(i), rapor(u), dosya(si)
- klasor, dizin, yol(u), adres(i), baglanti(si), oturum(u), yetki(si), rol(u), grup / grubu
- sayfa(si), ekran(i), mesaj(i), bildirim(i), olay(i), gorev(i), kuyruk / kuyrugu, onbellek
- gecmis(i), tarih(i), zaman, sure(si), sayi(si), sayac, toplam, adet, miktar, tutar, oran(i)
- detay(i), ozet(i), sonuc(u), cikti(si), girdi(si), model(i), varlik, depo(su), denetleyici
- yardimci, arac(i), istemci(si), sunucu(su), arayuz(u), sinif(i), nesne(si), modul(u), paket(i)
- test(i), ornek / ornegi, sablon(u), tanim(i), aciklama(si), baslik / basligi, icerik / icerigi
- metin / metni, anahtar(i), kimlik / kimligi, sifre(si), giris, cikis, dogrulama(si)
- kontrol(u), yapilandirma, kategori(si)

**Türkçe, fiiller:**
- getir, kaydet, sil, guncelle, ekle, bul, al, ver, olustur, oku, yaz, gonder, dogrula, hesapla
- listele, ara, sorgula, sec, cevir, donustur, temizle, baslat, durdur, calistir, yukle, indir
- ata, onayla, reddet, kapat, ac, iptal
- isim-fiil biçimleri: getirme, kaydetme, silme, guncelleme, ekleme, bulma, olusturma, okuma,
  yazma, gonderme

**İngilizce:**
- controller, repository/repo, impl, manager, handler, factory, dto, dao, entity, util(s)
- helper(s), mapper, adapter, request, response, exception, error, builder, provider, base
- abstract, validator, converter, parser, reader, writer, listener, event, job, task, scheduler
- filter, interceptor, configuration, properties, settings, context, session, cache, queue
- message, notification, model, view, page, form, component, detail(s), summary, record, entry
- query, command, gateway, proxy, rest, endpoint, resource, spec, mock, stub, wrapper, processor
- engine, store, registry, facade, bean, vo, id, no, num, number, count, total, by, all, new, old
- info, list, map, set, get, is, has, to
- fiiller: find, save, add, remove, create, update, delete, fetch, load, read, write, send, check
  validate, convert, parse, build, init, handle, process, search, sync

**Ayrıca yeniden kullanılan mevcut listeler** (`_COMMON_WORDS`, `_CODE_KEYWORDS`). Bunlarda
`ana`, `deneme`, `sistem`, `ornek`, `production`, `image` gibi kelimeler de var. Bu listeler
bugün tek parçalı değerler için zaten kullanılıyor.

**Dikkat istediğim noktalar:**
- **Kısa fiil kökleri** (`al`, `ac`, `ara`, `ata`, `sec`, `ver`) kişi veya kod adı parçası
  olabilir. Örneğin `AtaServis` generic sayılır. Çıkarılmasını isterseniz söyleyin.
- `KaraKartalServisi` ve `MaviYildiz` bayrak açıkken de maskeleniyor
  (`test_codename_of_common_words_from_llm_stays_masked_with_flag_on`).

## 6. Riskler, varsayımlar ve açık sorular

1. **Kuyruk artışı (a).** Gerçek projede de görülmesi muhtemel. Önerim: bu dalı intranete dağıtıp
   Adım 0–1'i bununla ölçmek. Dağılımda `llm_denetimi` ve otomatik düzeltme başarısızlıklarında
   `maskeleme` payının artmasını bekliyorum.
2. **Rapor başlığındaki `Proje: <ad> | sicil | Branch`.** Bu değerler yol değil; kural 7'nin
   kapsamı dışında bıraktım. Ancak proje adı runtime parametresi olarak içerikte maskeleniyor.
   **Soru:** Başlıkta da maskelensin mi?
3. **`ExportValidationError` mesajları.** Yol çakışması ve yol maskeleme son kontrolü gibi
   hatalar export'u başlamadan durduruyor. Bu mesajlarda kaynak yol geçiyor.
   - Arayüze ve CLI ekranına gidiyorlar; loga yazılmıyorlar (`errors.py` yalnızca 500'ü loglar).
   - İnteraktif istisna saydım. **Soru:** Bunlar da etikete geçsin mi?
4. **Diğer interaktif yerler** (dokunmadım):
   - `rapor-gecmis` / `rapor-detay` CLI çıktıları (DB'den kaynak yolu basar)
   - Streamlit'teki dosya listeleri ve ilerleme göstergesi (`current_file`)
   - `measure_golden.py` JSON'u (yalnızca altın küme yolları)
5. **Damga kapsamı.** Yalnızca `app/` kapsanıyor; `scripts/` ve `alembic/` kapsam dışında.
   - Varsayım: damgada olmayan fazladan dosyalar davranışı değiştirmez. Bu yüzden yalnızca uyarı
     veriliyor. Eski sürümden kalmış bir modülü yeni kod import etmez.
6. **İmza kontrolünün kapsamı.** Yalnızca modül düzeyindeki fonksiyonlar kontrol ediliyor;
   sınıf metotları ve sınıf kurucuları kapsam dışında. Temiz `main` üzerinde 0 bulgu (347 çağrı),
   bu dalda 0 bulgu (364 çağrı).
7. **Yeniden başlatılmamış backend kontrolü.** Her export'ta `app/` yeniden özetleniyor. 108
   dosya için bu milisaniyeler sürüyor.
8. **Windows + Python 3.14.3 test edilemedi.** Linux'ta 3.14.7 ile test edildi.
   - CRLF normalizasyonu testle doğrulandı.
   - PowerShell komutları kontrol listesinde.

## 7. Sonraki faz için öneri

1. Bu dalı (damga ile) intranete dağıtıp Adım 0–1'i çalıştırmak. Preflight'ın yeni aşamaları ve
   `failed_check_summary.py`'nin uyuşmazlık sayısı ilk gerçek veriyi verir.
2. Gerçek dağılım beklentiyle uyuşursa Faz 2b'yi öne almak. (a) altın kümede kuyruğu %9'dan %36'ya
   çıkardı; geri kazanım için doğrudan araç Faz 2b. Denetim alıntılarının, tespit bulgularıyla
   aynı hattan (OverlapResolver → TokenBoundaryValidator → politika) geçmesi en büyük etkiyi
   verecek.

## 8. Onay sonrası değişiklikler (kullanıcı kararları)

| Commit | Karar | Değişiklik |
|---|---|---|
| `3b5c1a5` | 1 | `/health` normalde `{"status": "ok"}` (test aslına döndü), damga uyuşmazlığında `{"status": "degraded"}`. Export 503 mesajında modül adı ve commit yok; ayrıntı `export_refused build_mismatch commit=... files=...` log satırında ve preflight'ta. |
| `cd2c0e2` | 2 | 3 harf ve daha kısa Türkçe kökler generic listeden çıktı (18 giriş). Yeniden kullanılan `_COMMON_WORDS_TR`'deki kısa kökler (`ad`, `ana`, `kod`, `tip`, `yol`) de bileşik ad kuralında sayılmıyor. `no` İngilizce/kod anahtar kelimesi listelerinden gelmeye devam ediyor. |
| `23457e4` | 3 | Rapor başlığında proje, sicil ve branch yol maskelemesinden geçiyor. Maskeli hal ham değeri hâlâ içeriyorsa fail-closed `<gizlendi>` yazılıyor (ör. `main`). |
| `409a6b2` | 4 | Kaynak yollu export doğrulama hataları değişmedi. Senkron ve arka plan işinde mesajın operatöre ulaştığı ve hiçbir log kaydında olmadığı testle doğrulanıyor. |
| `28d97a2` | – | **Düzeltme:** kök klasör ve başlık maskelemesi yalnızca görüntüleme içindir ama `mask_relative_path` ile DB'ye eşleme yazıp sayaç tüketiyordu (`38b1fe4`'ten beri). Mapping sayısını kontrol eden 5 test bunu yakaladı. Yeni `mask_display_path` aynı yol kurallarını kullanıyor ama DB'ye yazmıyor; eşleşen kısım `<önek>_*` olarak gösteriliyor. |

Bu düzeltmeden sonra `sonra/` ölçümleri yeniden üretildi.
- Bölüm 2'deki metrikler değişmedi.
- Maskelenen değer sayısı stub'da 24, LLM kapalıyken 19, generic filtre açıkken 23. Önceki
  sürümde fazladan sayılan, ölçüm betiğinin geçici hedef klasörünün adındaki proje adıydı.
- LLM kapalıyken bu dalın çıktısı `main` ile bayt bayt aynı; eşlemeler de birebir aynı.

Test takımı (son): **1542 passed, 1 skipped, 5 xfailed** (Python 3.11.15).

**Bu sırada bulunan, kapsam dışı hata.** Alembic seed verisi sicil kuralını
`kategori="personnel_no"` ile oluşturuyor. `app/db/seed_data.py` ve export ise `sicil_no`
kullanıyor. Alembic ile kurulmuş bir DB'de sicil değeri içerikte, yolda ve başlıkta maskelenmez;
başlık bu yüzden `<gizlendi>` yazıyor. Düzeltme bir veri migrasyonu ve davranış değişikliği
gerektirdiği için ayrı bir görev olarak önerildi. Intranet kontrol listesi, gerçek DB'nin
etkilenip etkilenmediğini salt okunur bir sorguyla soruyor.
