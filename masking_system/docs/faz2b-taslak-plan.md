# Faz 2b — TASLAK plan (kod yazılmadı)

**Durum:** TASLAK. Gerçek `failed_check` dağılımı intranetten gelince kesinleşecek (bkz.
`diagnostics/intranet-olcum-kontrol-listesi.md`, Adım 0–1b). Bu belge, kesinleştirmede
kullanılacak seçenekleri ve öncelik kurallarını kaydeder. Onay olmadan koda başlanmaz.

## 1. Bağlam

**Orijinal Faz 2 tanımından kalan işler** (`docs/devam-notu.md` bölüm 11):
- 2: `AUDIT_AS_CANDIDATE_SOURCE` bayrağı. Doğrulanmış denetim alıntıları dosya kapısı olmaz;
  `DetectionResult` olarak OverlapResolver → TokenBoundaryValidator → politika hattından geçer.
- 3: `mask_known_values` hepsi-ya-da-hiç olmaktan çıkar. Güvenli geçişler maskelenir, güvensiz
  geçişler değer düzeyinde inceleme kaydına düşer. Çok satırlı alıntılar satırlara bölünür.
- 4: Denetim, tespitin bilerek göz ardı ettiği bir değeri (düşük güven, generic, öğrenilmiş
  bastırma) yeniden işaretlerse bu bilgi kaydedilir ve politika bunu hesaba katar.

**Faz 2a'dan gelen bulgular** (`diagnostics/faz2a-20261001/RAPOR.md`):
- LLM09 düzeltmesinden sonra altın kümede kuyruk %9'dan %36'ya çıktı. Neden: denetim alıntıları
  identifier'a genişletilemiyor. Üç tür alıntı var:
  - çıplak kod identifier'ı: `PoseidonGatewayClient`
  - identifier parçası: `poseidon`
  - host parçası: `cnry-db01`
- `weak` yayılmasının kuyruğa etkisi 0.
- Faz E hatası yanlış kodla kaydediliyor. Yeniden denetim hata verirse dosya
  `llm_denetimi_tamamlanamadi` değil `llm_denetimi` koduyla kaydediliyor.
- Kısmi canary sızıntısı (birleştirme mantığı). Sözlük terimi `karayel`, LLM'in bulduğu tam host
  değerini yeniyor ve `cnry-db01` açık kalıyor. LLM kapalıyken bu çıktıya gidiyor.

**Önemli sınır.** Identifier parçası alıntıları (`poseidon` → `PoseidonGatewayClient`) sezgisel
bir kaynakla güvenli maskelenemez. Bunun doğru çözümü Faz 3'ün parça bazlı maskelemesi. Faz 2b bu
tür alıntılarda kuyruğu azaltmaz. Yalnızca kaydı dosya düzeyinden değer düzeyine indirir ve
incelemeyi kolaylaştırır.

## 2. İş kalemleri (taslak)

Her kalem ayrı commit; davranış değiştirenler bayrak arkasında, varsayılanları kapalı.

| # | Kalem | Bayrak | Kuyruğa beklenen etki |
|---|---|---|---|
| B1 | **Faz E hata kodu.** Yeniden denetim hata verirse `llm_denetimi_tamamlanamadi`. Raporlama düzeltmesi; çıktı değişmez. | yok (yalnızca kod/etiket) | yok; ölçüm doğruluğu artar |
| B2 | **Çok satırlı alıntıyı satırlara böl.** Her satır ayrı alıntı olarak `mask_known_values`'a girer; her satır birebir ve sınır doğrulamasından geçmeli. | `AUDIT_SPLIT_MULTILINE_QUOTES` | `cok_satirli_alinti` payı kadar düşer (altın kümede README) |
| B3 | **`mask_known_values` kısmi mod.** Güvenli geçişler maskelenir; güvensiz geçiş varsa dosya yine onaya gider (fail-closed), ama kayıt değer düzeyinde olur (hangi alıntı, hangi kontrol). | `MASK_KNOWN_VALUES_PARTIAL` | Doğrudan düşüş küçük; incelemeyi hızlandırır. B4 ile birlikte anlamlı. |
| B4 | **Denetim aday kaynağı.** Alıntılar `llm_audit` `DetectionResult` olarak tespitle aynı hattan geçer. Dosya kapısı kalkar; kalan riskli değer yine engeller. | `AUDIT_AS_CANDIDATE_SOURCE` | String/yorum içindeki alıntılarda ve tespitle çakışan alıntılarda düşüş |
| B5 | **Yeniden işaretlenen değerler.** Tespitin `dusuk`, generic ya da öğrenilmiş bastırmayla bıraktığı değeri denetim tekrar işaretlerse `denetim_tekrar_isaretledi` kaydı oluşur. Politika şeması aşağıda. | `AUDIT_REFLAG_POLICY` | `inceleme` ve `llm_denetimi` arasındaki döngüyü kırar |
| B6 | **Kısmi canary (birleştirme).** Kısa sözlük terimi, onu içeren daha uzun ve doğrulanmış bir değerin içindeyse uzun değer kazanır ya da her iki parça da maskelenir. | `MERGE_CONTAINED_FINDINGS` | Kuyruğa etkisi yok; sızıntıyı (LLM kapalıyken) kapatır |

**B5 politika şeması (karar sizin):**
- `dusuk` + denetim işareti → iki bağımsız sinyal sayılır, güvenle maskelenebiliyorsa maskelenir.
- Generic (Faz 2a/d) + denetim işareti → maskelenmez, kayda geçer. Generic kararı öncelikli.
- Öğrenilmiş "hassas değil" kararı + denetim işareti → maskelenmez. Karar insan onaylı; yalnızca
  sayılır ve raporda görünür.

## 3. Dağılıma göre öncelik

Intranet verisi gelince bu tabloya göre sıra belirlenecek. "Baskın", uyarılı dosyaların yaklaşık
%40'ı ya da fazlası anlamına geliyor. Birden fazla kod baskınsa en yüksek satır önce gelir.

| Baskın `failed_check` (ve alt kırılım) | Öncelik | Gerekçe |
|---|---|---|
| `tespit_katmani` + `TypeError` | **Faz 2b'ye başlanmaz.** Dağıtım sorunu; Adım 0'a dönülür. | Faz 2a'nın sürüm koruması bunu yakalamalıydı. Yakalamadıysa önce o incelenir. |
| `llm_tespit`, `llm_denetimi_tamamlanamadi` (+ `ReadTimeout`, `HTTPStatusError_5xx`, `yanit_kesildi`) | B1 → Faz 1 ayarları (Adım 2) → Faz 4.4 (küçük parçayla yeniden deneme) → sonra B2–B4 | Altyapı kaynaklı karantina; denetim mantığı değişikliği bunu azaltmaz |
| `llm_denetimi`, otomatik düzeltme nedeni ağırlıkla `cok_satirli_alinti` | **B2 → B3** → B4 | En ucuz ve en güvenli kazanç |
| `llm_denetimi`, otomatik düzeltme nedeni ağırlıkla `maskeleme` | B4 → B3; **Faz 3 öne çekilir** | Alıntılar çoğunlukla identifier parçasıysa Faz 2b az kazandırır (bkz. bölüm 1) |
| `llm_denetimi`, otomatik düzeltme nedeni ağırlıkla `daraltma` / `uzun_alinti` | B3 → B2 → B4 | Kısmi mod ve satır bölme daraltma/uzunluk sınırını hafifletir |
| `inceleme` (düşük/orta güven onay bekliyor) | B5 → Faz 4.1 / 4.2 değerlendirilir | Kuyruk politikadan geliyor, denetimden değil |
| `acik_terim`, `sozdizimi`, `tutarlilik` | **Faz 3 önce**, Faz 2b sonra | Kök neden tüm-identifier maskeleme / proje sözlüğü eksikliği |
| `yol_icerik_uyusmazligi` sayısı yüksek (yalnızca ölçüm) | Faz 3 aciliyeti artar | Kural 9 Faz 3'te zorunlu olunca bu dosyalar engellenecek |
| Baskın kod yok / karışık | B1 → B2 → B3 → B4 → B5 | Riski en düşükten en yükseğe |

B6 kuyruğu etkilemez ama bir sızıntıyı kapatır. Önerim: dağılımdan bağımsız olarak B1 ile
birlikte ilk gruba alınması. Karar sizin.

## 4. Değişmez kurallar açısından riskler

- **Fail-closed.** Hiçbir kalem, maskelenemeyen bir denetim bulgusu varken dosyayı otomatik
  yayınlamaz. Bu Faz 4.1'in (`MASK_AND_RELEASE_ON_DOUBT`) konusu. B3 ve B4'te dosya kapısı
  kalksa da "kalan riskli değer → onay" kuralı sürer. Yeniden denetim de çalışmaya devam eder.
- **LLM09.** B4'te alıntılar `llm_audit` (sezgisel) olarak kalır; Katman 1 yetkisi geri
  verilmez.
- **Determinizm.** Satır bölme ve kısmi mod sıralaması deterministik olmalı: alıntı sırası, sonra
  ofset.
- **Geri alma.** Her yeni yolda round-trip doğrulaması aynen sürer.

## 5. Kesinleştirmek için gereken veri

- `failed_check_summary.py` çıktısı: Adım 1 ve Adım 1b. Özellikle "Otomatik duzeltme
  basarisizlik nedeni" satırı (`maskeleme` / `cok_satirli_alinti` / `daraltma` / `uzun_alinti`
  dağılımı).
- `Yol/icerik uyusmazligi` sayıları.
- Hata sınıfları (`ReadTimeout`, `yanit_kesildi` vb.).
- Opsiyonel ama çok faydalı: `llm_denetimi` ile kuyruğa düşen dosyalarda alıntıların ne kadarının
  identifier parçası olduğu. Değer değil, yalnızca sayı. Bunu sayan salt okunur bir betik Faz 2b
  başında eklenebilir.

## 6. Kabul (orijinal Faz 2 tanımından)

- Geri alma %100.
- Canary sızıntısı 0.
- Onay kuyruğu oranı Faz 2a sonrasına göre düşmüş.
- Denetim kaynaklı karantina payı azalmış.
- Ölçüm: altın küme (stub %0 / %2, off) ve intranetteki aynı proje.
