# LLM TypeError incelemesi — güncellenen teşhis, 28 Eylül 2026

**Son durum:** Yeni `phase=detection requests=0` kaydı, gösterilen dosyada hatanın HTTP çağrısı başlamadan önce oluştuğunu gösteriyor. Önceki yanıt ayrıştırma kusuru ayrı bir bulgudur; bu kaydın nedeni değildir. İntranetteki kesin kaynak satırı henüz elde edilmedi. İntranet sunucusuna bağlanılmadı. Kullanıcının dağıtım kaynağı olarak belirttiği flash proje güncellendi; ayrıntılar son bölümde.

1. **Kanıtın yorumu**

   Kullanıcı 252 dosyalık projede 128 dosya için `llm detector ... TypeError` ve karantina mesajı bildirdi. Sunucu/model kullanıcı tarafından vLLM, “qwen 3.8 27B” olarak belirtildi; model kimliği sunucudan doğrulanmadı.

   Son tespit kaydı: `phase=detection chunks=1 completed=0 requests=0 elapsed_seconds=0.000 status=error error_type=TypeError prompt_tokens=0 completion_tokens=0 usage_responses=0`.

   Yerel kodda `requests`, `LLMScanMetrics.request` içinde eşzamanlılık kapısı geçildikten sonra ve HTTP çağrısından önce artırılıyor. Sayaç sıfır olduğuna göre bu dosya için model yanıtı ayrıştırmasına ulaşılmamış. Gösterilen TypeError, modelin yanıt formatı, token tükenmesi veya HTTP timeout hatası olarak açıklanamaz. Bunun intranette aynı sayaç uygulamasının bulunduğu varsayımına dayandığı unutulmamalıdır.

   Önceki audit kaydı: `chunks=1 completed=1 requests=1 elapsed_seconds=38.759 status=ok error_type=none`, prompt token 1078, completion token 408. Audit ayrı bir çağrıdır; başarılı olması tespit aşamasının başarılı olduğunu göstermez.

2. **İncelenecek dar alan**

   `find_llm_detections` içindeki istek hazırlığı (`build_detection_request`, prompt yükleme, ek LLM talimatlarını birleştirme), `metrics.request` çağrı uyumu ve `_gate` eşzamanlılık kapısı.

   Kontrollü testler:

   - Eski parametre listesine sahip bir `build_detection_request` taklit edildiğinde tespit tam olarak `chunks=1 completed=0 requests=0 error_type=TypeError` üretirken audit başarıyla tamamlandı.
   - Prompt birleştirmesinde hata enjekte edildiğinde kuralsız tespit ve audit geçti; yalnızca DB talimatlarıyla tespit başarısız oldu.
   - Eşzamanlılık sınırı bilerek metin yapıldığında hem tespit hem audit kapıda başarısız oldu. Normal `VLLMSettings` bu alanı tamsayı olarak doğrular; bu test gerçek ayar kusuru kanıtı değildir.

   Bunlar tanılama kontrolleridir. Kısmi kod güncellemesi, çalışan eski backend veya ek talimat hatası henüz doğrulanmadı. Yalnızca dosya sayısından bir kapasite sorunu çıkarılamaz.

3. **İntranette çalıştırılacak kontrol**

   Önce yalnızca `masking_service/scripts/check_llm_preflight.py` dosyasını intranetteki aynı konuma kopyalayın. Böylece tanı alınmadan uygulamanın şüpheli kodu değiştirilmemiş olur. Backend'in kullandığı Python ve ortam değişkenleriyle, `masking_service` klasöründe çalıştırın:

   Windows PowerShell:

   ```powershell
   .venv\Scripts\python.exe scripts\check_llm_preflight.py
   ```

   Linux:

   ```bash
   .venv/bin/python scripts/check_llm_preflight.py
   ```

   Araç ağ erişimini engeller ve HTTP sınırında sentetik yanıt kullanır. Yalnızca sentetik metin tarar; aktif LLM kural açıklamalarını SQLite `mode=ro` ile okur. Kural içeriğini, promptu, API anahtarını ve istisna mesajını yazdırmaz. Python yolu/sürümü, yüklü modül yolları/SHA256 özetleri, fonksiyon parametreleri, ayar tipleri ve hata kaynak satırlarını gösterir.

   Beklenen aşamalar: `detection_without_rules`, `detection_with_db_rules`, `audit`.

   - İki tespit denemesi de `TypeError`, audit başarılı: `frames=` kaydındaki tespit hazırlama veya çağrı satırını inceleyin.
   - Yalnızca `detection_with_db_rules` başarısız: Ek talimat yolunu inceleyin.
   - Audit dahil bütün aşamalarda `_gate` hatası: Ortak ayar/çalışma zamanı yolunu inceleyin.
   - `FAIL stage=db_rules`: DB okuması tamamlanmamış; diğer aşamalar çalışsa da kurallı tarama doğrulanmış değildir.
   - `RESULT=OFFLINE_OK`: Yeni süreçte yerel hazırlık yolu çalışıyor. Bu, açık backend sürecinin aynı kodu kullandığını veya gerçek model yanıtlarının sağlıklı olduğunu kanıtlamaz. Backend'in Python/çalışma dizinini karşılaştırın, aynı kod sürümüyle yeniden başlatıp tek sorunlu dosyada tekrar kayıt alın.

   Çıktı paylaşılırken kurulum yolları gizlenebilir. Hata varsa özellikle `FAIL ... frames=...` satırı ve fonksiyon parametreleri gerekir.

4. **Yerel doğrulama sonucu**

   Araç yerel gerçek yapılandırmayla çalıştırıldı: Python 3.13.13, tamsayı eşzamanlılık ayarı, metin türünde 1 aktif LLM talimatı. Üç aşama da geçti, `RESULT=OFFLINE_OK`. Gerçek LLM'e istek gönderilmedi.

   Yeni araç ve mevcut tespit testleri birlikte: **63 passed**. Ağ istemcisinin oluşturulmaması, kuralların doğru kapsamda salt okunur alınması, eksik DB'nin oluşturulmaması, gizli içeriklerin çıktıya taşınmaması, kapalı LLM'in başarı sayılmaması ve sıfır istek hata ayrımı doğrulandı.

5. **Önceki bağımsız düzeltmenin durumu**

   Yanıttaki `guven_seviyesi` liste/nesne olduğunda eski ayrıştırıcıdaki küme üyeliği kontrolü `TypeError` üretiyordu. Bu bağımsız kusur yerelde düzeltildi: zorunlu alan türleri ve güven seviyesi doğrulanıyor; hatalı yanıt açıklamalı `LLMRecognitionError` üretiyor ve karantinada kalıyor. Önceki 134 test geçti. Bu test sonucu yeni sıfır istek vakasının çözüldüğü anlamına gelmez.

   `detectors.py` dosyasına eklenen güvenli `detector_crash ... frames=...` kaydı, beklenmeyen hatanın kod dosyasını/satırını/fonksiyonunu gösterir. `llm_runtime.py` içindeki istek loguna `error_stage=http|parse|none` eklendi; fakat `requests=0` hatası bu istek loguna ulaşmadan oluşabildiği için yalnızca bu alan yeterli değildir.

   Timeout, token sınırı, thinking veya eşzamanlılık ayarları kanıtsız olarak değiştirilmedi. Sonraki adım intranetteki çevrimdışı kontrol çıktısının alınmasıdır.


6. **Flash güncellemesi — kullanıcının talimatıyla tamamlandı**

   Hedef: `/media/aipc/4D72-8BF5/masking`. İnceleme sırasında flash'taki `llm_recognizer.py` zaten yerel düzeltilmiş dosyayla aynıydı; ekran görüntüsündeki SHA256 ve satır düzeniyle aynı değildi. Bu nedenle ekran görüntüsünün kesin TypeError ifadesi flash üzerinden geri elde edilemedi.

   LLM kaynakları aynı sürüme tamamlandı: `llm_recognizer.py`, `llm_runtime.py`, `detectors.py`, `audit_reviewer.py`, `core/config.py`. Preflight aracı, üç ilgili test dosyası ve bu rapor aktarıldı. `SOURCE_SHA256.json` içinde aktarılan dosya özetleri güncellendi ve kopyalar doğrulandı. `.env`, veritabanı ve wheels değiştirilmedi.

   Yedek: `.update_backups/before-llm-typeerror-20260928T084632Z.zip`.

   Doğrudan flash kaynakları kullanılarak Linux/Python 3.13.13 üzerinde sentetik ayarlar ve geçici DB ile preflight: **OFFLINE_OK**; kuralsız tespit, DB talimatlarıyla tespit ve audit geçti. İlgili testler: **114 passed** (bir mevcut defusedxml uyarısı). Windows/Python 3.14.3 ve gerçek vLLM bu ortamda test edilmedi.

   Sonraki adım: Güncel flash kaynaklarını intranete aktarın, backend/UI süreçlerini yeniden başlatın, preflight'ı yeniden çalıştırın ve önce sorunlu tek dosyayı tarayın. Bu doğrulamalar başarılıysa orijinal projeyi yeni işlem olarak tarayın. Eski raporlar değişmez.


7. **Dağıtım kapsamı düzeltmesi**

   Kullanıcının isteğiyle bu çalışma için flash’a eklenen `test_llm_response_validation.py` ve `test_llm_preflight.py` kaldırıldı; mevcut `test_exporter_failure_handling.py` güncelleme öncesi yedekten geri alındı. Önceden flash’ta bulunan diğer test dosyaları değiştirilmedi. Uygulama kaynakları ve `scripts/check_llm_preflight.py` tanılama aracı korunuyor. Regresyon testleri yerel geliştirme projesinde kalıyor.
