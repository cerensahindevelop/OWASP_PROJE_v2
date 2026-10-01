# Sicil kuralı kategori uyuşmazlığı — düzeltme raporu (1 Ekim 2026)

Dal: `claude/sicil-kategori-duzeltme` (`main` = `93ecb3f`'den). Faz 2b'den ve PR #12'den
bağımsız.

## 1. Sorun

| Yer | Sicil kuralı |
|---|---|
| Alembic seed verisi (`9f21a6b8e4c3`) | `kural_adi = kategori = 'personnel_no'` |
| Export (`exporter.export_project`) | değeri `runtime_params['sicil_no']` ile verir |
| Kural motoru (`rule_engine.build_pattern`) | `runtime_params.get(rule.category)` → `None` → kural hiç çalışmaz |

**Sonuç.** Alembic ile kurulan her DB'de (üretim yolu) kullanıcının sicil değeri içerikte, dosya
yolunda ve rapor başlığında maskelenmiyordu. Kullanıcı gerçek DB'de doğruladı: etkileniyor.

## 2. Yapılanlar

| Commit | İçerik |
|---|---|
| `d869a7d` | **Tek kaynak.** `app/services/runtime_params.py` (`RuntimeParam`, `build_runtime_params`). Export, yeniden denetim, kural motoru, yol maskeleme, rapor ve arayüz etiketleri bunu kullanır. Kural yönetimi, kategorisi bir runtime parametresi olmayan parametrik kuralı reddeder. `seed_data.py`'nin yanlış "tek doğruluk kaynağı" iddiası düzeltildi. |
| `c7dce25` | **Migrasyon `f1c3a5e7b9d2` ve testleri.** |
| `72acbfe` + sadeleştirme | **Altın küme sicil senaryosu:** `docs/ekip/P-GOLDEN-0001/notlar.md`. Sicil hem dizin adında hem içerikte geçiyor. |
| `696370a` | **Geçmiş etki raporu** (`scripts/sicil_etki_raporu.py`) ve preflight `runtime_rules` aşaması. |
| `2203663` | **Intranet kontrol listesi:** migrasyonun dağıtımdaki yeri ve kontrolleri. |

### Migrasyon güvenceleri

- **Yalnızca seed kaydı.** Düzeltilen kayıt, seed imzasına birebir uymalı: ad, kategori,
  `parametric`, boş regex/bayrak/doğrulayıcı/entity, önek `mask_personel_no`, `katman1`,
  allow-list değil, seed açıklaması. Kullanıcının açıklamasını ya da önekini değiştirdiği bir kayda
  dokunulmaz; migrasyon uyarı yazar ve preflight bunu FAIL olarak gösterir.
- **`aktif_mi` ve `oncelik` imzaya dahil değil ve değişmez.** Operasyonel ayar oldukları için bu
  kararı ben verdim. Kullanıcı kuralı pasif yaptıysa pasif kalır; preflight o durumda
  `aktif_kural_yok` FAIL'i verir.
- **Kimlik korunur.** Kayıt id'si, yer tutucu öneki ve sayaç değişmez. Mevcut eşlemeler
  (`kural_id`) ve önceki çıktıların geri alınması etkilenmez.
- **İdempotent.** Düzeltilmiş, elle kurulmuş ya da zaten `sicil_no` adlı kural içeren DB'de hiçbir
  şey değişmez.
- **downgrade** yalnızca seed açıklamasını taşıyan kaydı geri çevirir; elle kurulmuş `sicil_no`
  kurallarına dokunmaz.
- **Testler** gerçek alembic komutlarıyla yapıldı:
  - taze kurulum
  - ikinci çalıştırma (`stamp` + `upgrade`)
  - downgrade → upgrade turu
  - kullanıcının değiştirdiği kayıt, elle kurulmuş DB, `sicil_no` ad çakışması

## 3. Ölçüm (altın küme, artık 12 dosya)

| | Migrasyon olmadan | Migrasyonla |
|---|---|---|
| stub: canary sızıntısı | **`sicil_kullanici (P-GOLDEN-0001/duz)`** | **0** (3/3) |
| off: canary sızıntısı | 3 eski canary + **`sicil_kullanici`** | 3 eski canary (LLM kapalıyken beklenen) |
| Sicil dizini çıktı yolunda | `docs/ekip/P-GOLDEN-0001/` | `docs/ekip/mask_personel_no_1/` |
| Kuyruk (stub) | %33 (4/12) | %33 (4/12) |
| Geri alma bayt farkı | 0 | 0 |
| Determinizm | – | 3/3, Jaccard 1,0 |

- 11'den 12 dosyaya çıkıldığı için kuyruk oranı %36'dan %33'e düştü. Bu bir davranış değişikliği
  değil, payda değişikliği.
- Testler migrasyon olmadan kırılıyor:
  - `test_runtime_params.py`: 3 test
  - `test_golden_path_acceptance.py::test_runtime_sicil_is_masked_in_output_paths_and_content`
- Tam takım: **1548 passed, 1 skipped, 5 xfailed** (Python 3.11.15).

## 4. Diğer kategorilerde benzer uyuşmazlık var mı?

Yöntem: `alembic upgrade head` ile taze bir DB kuruldu. Tüm `filtre_kurallari` kayıtları
`app/db/seed_data.py` ile alan alan karşılaştırıldı ve koddaki kategori/doğrulayıcı
karşılaştırmaları tarandı.

| Kontrol | Sonuç |
|---|---|
| Parametrik kural kategorileri ↔ runtime parametreleri | **Tek uyuşmazlık `personnel_no`** (düzeltildi). Kalıcı test ve preflight ekledim. |
| Kural adı/kategori: alembic ↔ `seed_data.py` | `personnel_no`/`sicil_no` dışında aynı |
| `generic_secret_assignment` regex'i | Farklı, ama sızıntı değil: `seed_data.py` eski sürümü tutuyor; DB'deki sonraki migrasyonların güncellediği hali doğru |
| `dogrulayici_adi` ↔ `validators.VALIDATORS` | Tutarlı (`secret_value`, `tc_kimlik_no`) |
| Dosya tipi kategori kısıtlamaları | Yalnızca Presidio tipleri (`EMAIL_ADDRESS`, `IP_ADDRESS`...); tutarlı |
| Kodda sabit kategori karşılaştırmaları | Yalnızca `sicil_no` (rule_engine, mapping_service) → `RuntimeParam`'a bağlandı |
| Kural yönetimi | Bilinmeyen kategorili parametrik kural ekleniyordu, hiç eşleşmiyordu → artık reddediliyor |

**Bulgu: `app/db/seed_data.py` ölü kod.** Hiçbir yerden import edilmiyor, alembic verisinin eski
bir kopyası. Bu uyuşmazlığa zemin hazırladı. Açıklamasını düzelttim ve parametrik adları sabitlere
bağladım. **Öneri:** dosyanın silinmesi. Sizin kararınız; bu PR'da silmedim.

## 5. Geçmiş etki

**Yalnızca DB'den kesin belirlenemez.** Düzeltme öncesi sicil hiç eşleşmediği için "sicil şu
dosyada geçti" diye bir kayıt (eşleme, AuditLog) oluşmadı.

Belirlenebilenler:
- Hangi export'larda sicil eşlemesi **var** → bunlar kesin etkilenmedi.
- Eşlemesi olmayanlar `olasi` olarak listelenir. Düzeltme öncesindeyse sicil kaynakta geçiyorsa
  sızdı; düzeltme sonrasındaysa sicil kaynakta yoktu.
- `--tara` ile kaynak ve hedef klasörler sunucuda hâlâ duruyorsa salt okunur taranır. Yalnızca
  dosya sayısı yazılır (`kaynakta=N ciktida=N`). Böylece kesinleşir.

`scripts/sicil_etki_raporu.py` DB'yi `mode=ro` açar. Yalnızca run kimliği, tarih, durum ve sayı
yazar; sicil değeri, proje adı ve yol yazmaz (testle doğrulandı).

## 6. Riskler ve açık noktalar

- **PR #12 ile çakışma.** `mapping_service.py`'deki sicil satırı PR #12'de başka bir fonksiyona
  taşındı. İkinci merge edilen PR'da küçük bir çakışma olur. Hangisi önce merge edilirse diğerine
  `main`'i merge edip çözebilirim.
- **Rapor başlığı.** PR #12'deki başlık maskelemesi, bu düzeltmeyle birlikte sicili `<gizlendi>`
  yerine `mask_personel_no_*` olarak gösterecek.
- **Yer tutucu öneki `mask_personel_no` kalıyor.** Geriye uyumluluk için; önek değişirse eski
  çıktıların sayaç ad alanı ayrışır.
- **Presidio yanlış pozitifleri.** Yeni altın küme dosyasında küçük harfli ifadeleri de kişi/kurum
  sanıyor. Bilinen Faz 4.2 konusu; sicil senaryosunu etkilemiyor.
