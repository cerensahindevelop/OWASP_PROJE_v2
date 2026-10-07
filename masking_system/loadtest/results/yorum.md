## Özet ve değerlendirme

**Kapsam.** 2026-10-06 16:43 – 2026-10-07 05:18 arasında 50 koşul × 3 tekrar = 150 tekrar gerçek sistemle çalıştırıldı (ana kampanya 18 koşul, worker kampanyası 32 koşul). Ayrıca temiz olmayan 4 tekrar 2026-10-07 sabahı yeniden koşuldu (bkz. "Sınırlar"). Tüm güvenlik ve audit aşamaları açıktı. `.env`, model ve Ollama ayarları değiştirilmedi. Worker testi değerleri yalnızca test backend süreçlerinin ortam değişkenleriyle verildi.

### Temel bulgular

1. **Darboğaz LLM sunucusunun tek slotu.** İşlem kapasitesi kullanıcı sayısından bağımsız olarak sabit kaldı: küçük projede **1,51–1,69 proje/dk** (1→10 kullanıcı), orta projede **~0,54 proje/dk**. Eklenen her kullanıcı yalnızca bekleme süresini doğrusal uzattı. Isınmış küçük projede uçtan uca p50 değerleri: 1 kullanıcı 39 sn, 4 kullanıcı 144 sn, 10 kullanıcı 332 sn.
   - Kanıt 1: Tek kullanıcılı küçük işin sunucu süresi 39,2 sn. Bunun **35,2 sn'si (%90) LLM HTTP isteklerinde** geçiyor (iş başına 12 istek: 6 detection + 6 audit). Seri LLM ile teorik tavan 60 / 35,5 ≈ **1,69 proje/dk**; ölçülen plato 1,63–1,69.
   - Kanıt 2: llama-server `/slots` örneklerinde slot, ölçüm penceresinin **%87–98'inde dolu**. Uygulamanın ölçülen eşzamanlı LLM isteği her zaman 1. Uygulama LLM kuyruğunda 10 kullanıcıda 40'a kadar istek bekledi (zaman çizelgesi grafikleri).
   - Kanıt 3: Maskeleme (iş başına 0,1–0,6 sn), sonlandırma (~1 sn), export (0,1–0,3 sn), yükleme (p95 < 1,4 sn) ve indirme (p95 < 0,3 sn) uçtan uca süreye göre ihmal edilebilir.
   - LLM süresinin çoğu **çıktı tokenı üretimi**: sunucu decode hızı ~98–106 token/s (TPOT ~10 ms), prefill ~1200–1450 token/s. Sunucunun toplam üretim hızı yükten bağımsız olarak ~90–107 token/s. Bu, tek akışlı decode hızına eşit; eşzamanlı istekler birleştirilmiyor (batching yok).

2. **GPU hesaplama/güç/sıcaklık açısından doymuyor.** Ortalama kullanım A5000 ~%30, 4500 Ada ~%22. Güç ortalamada 130 W / 230 W ve 68 W / 210 W. Termal throttling yok. VRAM yükle değişmiyor (model iki GPU'ya bölünmüş: ~12,9 GiB + ~17,5 GiB). GPU yüzdesi NVML'in örneklenmiş "en az bir kernel çalışıyor" oranıdır. Düşük değer, tek istekli (batch 1) decode'un GPU'yu doldurmadığını gösterir.
   - Ollama `OLLAMA_NUM_PARALLEL=4` ile ayarlı olduğu halde runner'ı `-np 1` ile başlatıyor.
   - Sunucu tarafında paralellik (np>1 veya vLLM continuous batching) kapasiteyi artırabilir. **Bu test edilmedi**; sunucu ayarına dokunulmadı. Bu nedenle kazanç miktarı ölçülmüş bir sonuç değil, doğrulanması gereken bir hipotezdir.

3. **10 eşzamanlı işte işler SQLite kilidi yüzünden düşüyor; yük altındaki tek teknik hata türü bu.** Her iş başlarken bağlam/run kaydını yazıp SQLite yazma kilidini alıyor. Kilit, spaCy kurulumu ve yol maskeleme bitip ilk `commit` yapılana kadar (~2–4 sn) tutuluyor. Böylece iş başlangıçları sıraya giriyor. 30 sn'lik `busy_timeout` aşılınca iş `OperationalError: database is locked` ile düşüyor ve kullanıcı 503 ("Sistemle bağlantı kurulamadı") görüyor. Hata konumu ölçümle doğrulandı: `get_or_create_context` INSERT'i 30,08 sn bekledi.
   - İş başlatma beklemesinin maksimumu: 1 kullanıcı 0,1 sn → 4 kullanıcı 7–12 sn → 8 kullanıcı 17–31 sn → 10 kullanıcı ~30 sn (sınır).
   - Düşen iş sayıları (aynı anda başlatılan 10 iş): eşzamanlı yükleme **30 işten 9**, karışık yük **30 işten 8**, sürekli yük **5 iş**, ısınmış sistem (1 sn arayla kademeli başlatma) **60 işten 2**.
   - 8 kullanıcıya kadar (worker testi, 96 tekrar) hiç iş düşmedi; ancak 8 kullanıcıda bekleme 31,2 sn'ye kadar çıktı, yani sınırda.
   - **Başarısız kapasite noktası: aynı anda başlayan ~10 iş.**

4. **Backend belleği iş başına ~0,5–1 GB büyüyor.** spaCy (`en_core_web_lg`) modeli her işte yeniden yükleniyor (`build_orchestrator`, iş başına 1,3–2,7 sn). Tek süreçte backend RSS'i: 1 iş ~1,5 GB, 4 iş ~3–4,8 GB, 10 iş **~9,6 GB**. Sistem RAM'i en çok 28,0 GiB / 63 GiB oldu (karışık yük, 10 kullanıcı). Swap kullanımı (2,6–3,9 GB) testten önce de vardı ve hiçbir tekrar içinde artmadı. Bellek taşması yaşanmadı.

5. **LLM hatası yok.** 150 tekrarda LLM timeout 0, LLM HTTP hatası 0, güvenlik karantinası 0. Kayıt yalıtımı doğrulaması tüm tekrarlarda geçti: kullanıcıların run, değer eşlemesi, denetim kaydı ve çıktı paketleri birbirine karışmadı.

6. **Yükten bağımsız veri bulgusu.** Orta veri setindeki yoğun kişisel veri içeren SQL dosyası (`db/seed_9.sql`), max_tokens=2048 ile bölündükten sonra bile kesildiği için her orta işte `llm_tespit` nedeniyle bloklandı ("teknik blok", dosya çıktıya konmaz). Bu tek kullanıcıda da oluyor; eşzamanlılıkla ilgisi yok.

7. **Soğuk başlangıç.** Modelin sunucuda yüklenmesi 8,9 sn (maks. 10,2 sn), ilk LLM isteği ~12 sn sürdü. Tek kullanıcılı küçük işin uçtan uca süresi soğukta 52 sn, ısınmışta 39 sn (+~13 sn). Ollama varsayılan `keep_alive` süresi 5 dk'dır; 5 dakikadan uzun boşta kalınca ilk kullanıcı bu gecikmeyi tekrar yaşar.

### Worker ve concurrency testi sonucu

| ayar (backend / dosya / LLM) | 8 kullanıcı proje/dk | 8 kullanıcı uçtan uca p95 | 1 kullanıcı uçtan uca p50 | backend RSS maks (8 kullanıcı) | not |
|---|---|---|---|---|---|
| **1 / 8 / 1 (mevcut)** | 1,65 | 290,5 sn | 39,8 sn | 5,7 GB | referans |
| 2 / 8 / 1 | 1,69 | 284,4 sn | 38,9 sn | 5,9 GB | farklar tekrarlar arası oynamayla aynı büyüklükte |
| 4 / 8 / 1 | 1,69 | 283,3 sn | 39,5 sn | 6,5 GB (1 kullanıcıda bile 4,1 GB) | +%2,4 kapasite, +0,8 GB RAM |
| 1 / 8 / 2 | 1,67 | 288,2 sn | 38,5 sn | 5,7 GB | sunucu bekleme p95 0,4→11,7 sn; HTTP p95 11,7→15 sn |
| 1 / 8 / 4 | 1,69 | 286,3 sn | 38,4 sn | 5,7 GB | sunucu bekleme p95 →25 sn; HTTP p95 →28,9 sn |
| 1 / 1 / 1 | 1,63 | 297,1 sn | 43,2 sn | 5,5 GB | 1 kullanıcıda kapasite −%9 |
| 1 / 2 / 1 | 1,63 | 293,9 sn | 40,6 sn | 5,6 GB | |
| 1 / 4 / 1 | 1,65 | 289,1 sn | 40,5 sn | 5,6 GB | |

- **A) Backend worker.** `uvicorn --workers N` mevcut mimaride desteklenmiyor: iş durumu süreç belleğinde (`export_jobs.registry`) tutuluyor, yoklama başka sürece düşünce 404 alınır. Bu yüzden N ayrı süreç ayrı portlarda başlatıldı ve kullanıcılar süreçlere yapıştırıldı. LLM sınırı dosya kilidiyle host genelinde uygulandığı için süreç sayısı LLM eşzamanlılığını artırmadı. Kapasite artışı %2–3'te kaldı. 4 süreçte Presidio'nun GIL çekişmesi azaldı (dosya başına Presidio p50 5,1 sn → 0,65 sn), ancak bu süre LLM beklemesinin altında kaldığı için uçtan uca etkisi küçük oldu. Bedeli boşta bile +2,6 GB RAM. Not: worker tablosundaki "ölçülen eşzamanlı LLM (uygulama)" sütunu çok süreçli koşullarda 2–3 gösteriyor. Bu bir örnekleme etkisidir: süreçlerin saniyelik sayaçları aynı saniye diliminde toplanıyor ve bir süreçte biten, diğerinde başlayan istek iki kez sayılıyor. llama-server `/slots` ölçümü bu koşullarda da en fazla 1 çalışan istek gösterdi; host genelindeki sınır (1) korundu.
- **B) LLM concurrency.** Uygulama sınırını 2 veya 4'e çıkarmak kapasiteyi değiştirmedi; kuyruk uygulamadan Ollama'ya taşındı. Ollama içi bekleme p95 0,4 → 25 sn'ye, HTTP süresi p95 11,7 → 28,9 sn'ye çıktı. HTTP timeout (200 sn) kuyruktan sonra değil HTTP isteğiyle başladığı için, yüksek değer daha fazla kullanıcıda gereksiz timeout riski doğurur. Sunucu tek slotlu olduğu sürece 1'den büyük değer kazanç getirmiyor.
- **C) Dosya worker (`VLLM_FILE_BATCH_SIZE`).** 2+ kullanıcıda kapasite farkı yok (1,63–1,65). 1 kullanıcıda 1 değeri işi %9 yavaşlattı (43,2 sn vs 39,8 sn), çünkü LLM beklerken Presidio taraması sıralanıyor. 8 değeri bir işin tüm dosyalarını aynı anda LLM kuyruğuna koyuyor: istek başına uygulama kuyruğu p95 artıyor, ama iş süresi kısalıyor.
- 1–8 kullanıcı aralığındaki worker koşullarının hiçbirinde bellek taşması, süreç çökmesi, LLM timeout'u veya iş hatası olmadı. Kuyruk her koşulda boşaldı.

### Önerilen ayar

**Mevcut ayar korunmalı: backend 1 süreç, `VLLM_FILE_BATCH_SIZE=8`, `VLLM_MAX_CONCURRENT_REQUESTS=1`.**

Gerekçe, ölçülmüş rakamlarla:
- Kapasite: test edilen 8 ayarın hepsi 8 kullanıcıda 1,63–1,69 proje/dk aralığında kaldı. En iyisi (4 süreç) mevcut ayardan yalnız %2,4 yüksek; bu fark tekrarlar arası oynamayla aynı büyüklükte.
- p95: 8 kullanıcıda 283–297 sn; mevcut ayar (290,5 sn) ortada.
- Bellek: 4 süreç boşta bile +2,6 GB, yükte +0,8 GB RSS ekliyor.
- Hata: hiçbir ayar SQLite başlangıç hatasını ya da LLM tavanını ortadan kaldırmıyor.
- LLM concurrency > 1 sunucu tek slotken HTTP p95'ini 2,5 kat artırıyor.
- `FILE_BATCH_SIZE=1` tek kullanıcıyı %9 yavaşlatıyor.

İsteğe bağlı: 2 backend süreci p95'i ~%2 iyileştirdi; ancak iş durumu süreç belleğinde tutulduğu için yapışkan yönlendirme olmadan kullanılamaz.

**Kapasiteyi gerçekten artıracak değişiklikler** bu testte değiştirilmedi, ayrıca ölçülmeleri gerekir:
1. **SQLite başlangıç kilidi.** Bağlam/run kaydından hemen sonra `commit` yapmak (spaCy kurulumu ve yol maskeleme kilit dışında), ya da uygulama tarafında eşzamanlı iş sayısını sınırlayıp fazlasını kuyrukta bekletmek. Bu, 10 kullanıcıdaki 503 hatalarını ortadan kaldırır.
2. **LLM sunucusu paralelliği.** Ollama'da np>1 veya vLLM ile continuous batching; GPU ~%30 kullanımda. Bu yapılırsa `VLLM_MAX_CONCURRENT_REQUESTS` sunucu slot sayısıyla birlikte yeniden ölçülmeli.
3. **spaCy modelini işler arasında paylaşmak.** İş başına ~1 GB RAM ve ~1,5 sn kazanç.
4. Ollama `keep_alive` süresinin uzatılması (soğuk başlangıçtaki +13 sn için).

### Sınırlar ve belirsizlikler

- **Gecikme sayıları veri setine bağlıdır.** LLM süresini çıktı token sayısı (bulgu yoğunluğu) belirliyor. Gerçek projelerde bulgu yoğunluğu farklıysa mutlak süreler değişir; darboğazın yeri değişmez.
- **Küçük örnek sayıları.** p95 için n<20 olan koşullar tablolarda `†` ile işaretli; bunların p95 değeri pratikte maksimumdur.
- **Temiz olmayan tekrarlar.** 2026-10-06 17:21'den sonra canlı arayüzden 6 maskeleme işi gönderildi ve 13 terim eklendi. Isınmış sistemin 4 kullanıcı (rep2, rep3) ve 10 kullanıcı (rep1, rep2) tekrarları bu sırada ~%5 yabancı LLM isteği aldı. Bu 4 tekrar `loadtest/results/_kirli_tekrarlar/` altına taşındı ve 2026-10-07 sabahı yeniden koşuldu.
- **Kural sayısı farkı.** Canlı DB'deki kural sayısı test sırasında 547'den 560'a çıktı. Erken tekrarlar 547, sonrakiler 560 kuralla çalıştı (her tekrar o anki DB'nin kopyasını kullandı). Sentetik veride bu terimlerin eşleşmesi beklenmez.
- **Ölçülemeyen metrikler.** TTFT ve KV-cache/preemption metrikleri ölçülemedi (bkz. bölüm 6).
