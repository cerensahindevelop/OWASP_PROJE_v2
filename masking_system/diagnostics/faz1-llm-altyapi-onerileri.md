# Faz 1 — LLM altyapısı önerileri (1 Ekim 2026)

Bu bir öneri belgesidir. Kod değişmedi. `.env.example` dosyasına yalnızca **yorum satırı
halinde** bir öneri bloğu eklendi; şablonun etkin değerleri aynı kaldı. Üretimdeki `.env`'ye
dokunulmadı. Değerler bir başlangıç noktasıdır ve kurum içi GPU/model üzerinde bölüm 3'teki
ölçümle doğrulanmalıdır.

## 1. Özet

Not (7 Ekim 2026): `VLLM_PROFILE` kaldırıldı. Değerler `.env`'ye açıkça yazılır;
aşağıdaki "`vllm-intra` profili" sütunu, `.env.example`'daki kurum içi vLLM
bloğunun değerleridir.

| Ayar | Kod varsayılanı | `.env.example` (etkin) | `vllm-intra` profili | Öneri |
|---|---|---|---|---|
| `VLLM_MAX_CONCURRENT_REQUESTS` | 1 | (1) | 4 | **4'ten başla**, benchmark ile 1/2/4/8 arasından seç |
| `VLLM_FILE_BATCH_SIZE` | 8 | (8) | 16 | 16 |
| `VLLM_MAX_TOKENS` | 1024 | 2048 | 2048 | **2048** |
| `VLLM_TRANSIENT_RETRIES` | 1 | (1) | 2 | **2** |
| `VLLM_TIMEOUT_SECONDS` | 200 | (200) | (200) | ölçülen tek istek p99 × 3, en az 60 |
| `VLLM_PRESENCE_PENALTY` | 0 | (0) | (0) | **0**; yalnızca tekrar döngüsü görülürse ve ölçümle 1,5 |
| `VLLM_DISABLE_THINKING` | false | true | true | **true** (istemci) + sunucu bayrağı |
| `VLLM_MAX_FILE_CHARS` / overlap | 6000 / 500 | (6000 / 500) | 6000 / – | 6000 / 500; 8000 yalnızca ölçümle |
| `VLLM_AUDIT_UNCHANGED_FILES` | true | (true) | – | **true** (güvenlik; Faz 1'de değişmez) |
| vLLM `--enable-prefix-caching` | – | – | – | **açık** |
| vLLM `--max-model-len` | – | – | – | 8192 (parça ≤ 6000 karakter), 16384 (daha büyük parça) |

Parantezli değerler, `.env.example`'da yorum satırı olduğu için kod varsayılanından gelir.

## 2. Gerekçeler

**İş yükünün şekli (ölçülmüş).** `diagnostics/llm-performance-20260924/live-summary.json`
(Ollama, geliştirme makinesi): 21.046 karakterlik dosyada 8 istek, toplam 15.895 girdi ve
**184 çıktı token'ı**. İstek başına ~2000 girdi token'ına karşılık ~23 çıktı token'ı düşüyor
(tek bir denetim yanıtında 408 çıktı token'ı görülmüş). Sonuç:
- Süreyi neredeyse tamamen girdi işleme (prefill) belirliyor.
- En büyük kazançlar prefix caching ve eşzamanlılıktan gelir; `max_tokens`'ın hıza etkisi
  küçüktür.

Altın kümede dosya başına 2,4–2,55 LLM taraması yapılıyor (tespit + denetim + Faz E yeniden
denetimi). Eşzamanlılık 1 iken bu istekler tamamen sıralı işleniyor.

**Prefix caching.** Tespit sistem promptu 3.276, denetim promptu 2.471 karakter; bu yaklaşık
750–1000 token eder. Yani her isteğin girdisinin kabaca **%40–50'si sabit önek**.
- Prompt yapısı önbelleğe uygun: dosya bağlamı sistem promptunun sonuna ekleniyor
  (`llm_recognizer.with_file_context`).
- DB'den gelen kurum talimatları (`pattern_type='llm'` kuralları) bir koşu boyunca sabit
  kalıyor.
- Varsayım: kurum içi vLLM sürümü V1 motoruysa prefix caching zaten varsayılan olarak açıktır;
  bayrağı açıkça vermek zararsızdır. Doğrulama:
  `curl http://<vllm>:8000/metrics | findstr /i prefix_cache` (isabet oranı metrikleri).

**Eşzamanlılık.** vLLM sürekli batch'leme yapar; Ollama'daki `Parallel:1` kısıtı burada yoktur.
- İstemci kapısı (`llm_runtime._gate`) HTTP zaman aşımını kapı içinde başlatır. Ancak sunucu
  tarafındaki kuyrukta beklenen süre HTTP süresine dahildir. Eşzamanlılık sunucu kapasitesini
  aşarsa istekler zaman aşımına uğrar ve dosya `llm_tespit` / `llm_denetimi_tamamlanamadi`
  koduyla karantinaya düşer (kök neden 3).
- Sunucu tarafında `--max-num-seqs`, istemci eşzamanlılığı × backend worker sayısından küçük
  olmamalı.

**`max_tokens` = 2048.** Normal yanıtlar çok kısa olduğu için tavan nadiren kullanılır. Ama
yoğun bir parçada yanıt kesilirse (`finish_reason=length`) parça ikiye bölünüp yeniden taranır
(en fazla 3 derinlik); sınırda hâlâ kesikse dosya karantinaya gider. 512 bu yüzden risklidir.
- Varsayım: vLLM V1 KV önbelleğini `max_tokens` için baştan ayırmaz; yüksek tavanın kullanılmadığı
  sürece maliyeti yoktur.
- Kesilme oranı `failed_check_summary.py` çıktısında `yanit_kesildi` olarak görünür.

**Yeniden deneme = 2.** Yalnızca geçici hatalarda ve yalnızca başarısız parça için çalışır
(`llm_runtime.is_transient_llm_error`). Ayrıştırma, şema ve kesilme hatalarında çalışmaz.
- En kötü durumda bir parça (2+1) × timeout + 3 sn bekleme alır (200 sn ile ~10 dk).
- Bu süre boyunca eşzamanlılık slotu tutulur.
- Kalıcı hatada dosya yine fail-closed karantinaya gider; güvenlik etkisi yoktur.

**Zaman aşımı.** Sabit bir değer önermiyorum. Benchmark'taki tek istek sürelerinin p99'unun
yaklaşık 3 katı alınmalı. Fazla kısa tutulursa kök neden 3'ü büyütür; fazla uzun tutulursa
takılan istek slotu boşuna meşgul eder.

**`presence_penalty`: varsayılan 0.** Kodun kendi ipucu (`llm_recognizer._truncation_hint`),
Qwen'in thinking kapalı açgözlü çözümde döngüye girebildiği durumda 1,5 öneriyor. Ancak:
- Ceza, modelin o ana kadar **ürettiği** token'lara uygulanır.
- Bu görevde model değerleri birebir kopyalamak zorunda; aynı öneki paylaşan değerler (ör.
  `cnry-db01…` ve `cnry-gw01…`) ya da tekrar eden tip etiketleri cezalanır.
- Bozulan değer metinde doğrulanamaz ve **sessizce atılır** (recall kaybı). JSON şeması zorunlu
  anahtarları korur ama değerleri korumaz.

Bu yüzden yalnızca loglarda "donguye girdi" ipucu görülürse açılmalı, altın küme ölçümüyle önce
ve sonra karşılaştırılmalı. Varsayım: etki büyüklüğü modele bağlıdır; ölçülmeden
genellenemez.

**Thinking.** Açık kalırsa `<think>` çıktısı token bütçesini tüketir ve yanıt kesilir. Hem
istemci (`VLLM_DISABLE_THINKING=true`, istekte `chat_template_kwargs`) hem sunucu
(`--default-chat-template-kwargs '{"enable_thinking": false}'`) tarafında kapatılmalı.

**Parça boyutu.** 6000 karakter ve 500 overlap, parça başına ~%8 tekrar demek.
- 8000 karakter istek sayısını ~%25 azaltır ve dosya başına hata olasılığını düşürür (daha az
  parça).
- Buna karşılık parça başına prefill ve kesilme riski artar.
- Yalnızca bölüm 3.3'teki ölçümle değiştirilmeli.

**`max-model-len`.** Ölçülen oran ≈ 4,4 karakter/token. Prompt (~1000) + parça (~1400–2000;
Türkçe veya minified kodda daha fazla) + çıktı (2048) ≈ 4500–5500 token. Bu yüzden 8192
yeterli; daha büyük parça için 16384.

**Denetimi atlama (`VLLM_AUDIT_UNCHANGED_FILES=false`).** Büyük hız kazancı sağlar ama ikinci
bağımsız kontrolü kaldırır. Bu bir güvenlik politikası kararıdır ve Faz 1 kapsamında
önerilmiyor.

## 3. Ölçüm protokolü

Komutlar `masking_service` klasöründe çalıştırılır. Benchmark gerçek endpoint'e **yalnızca
sentetik metin** gönderir; DB'ye ve çıktıya yazmaz.

### 3.1 Sunucu kontrolü

```powershell
curl http://<vllm>:8000/v1/models
curl http://<vllm>:8000/metrics | findstr /i "prefix_cache num_requests_waiting"
```

### 3.2 Eşzamanlılık taraması

```powershell
.venv\Scripts\python.exe scripts\benchmark_llm.py --concurrency 1 2 4 8 --files 16 --chars 12000 --chunk-chars 6000 --max-tokens 2048 > bench-c.json 2> bench-c.log
Select-String -Path bench-c.log -Pattern "finish_reason='length'|status=timeout|status=error"
```

Seçim kuralı, şu koşulları sağlayan en büyük eşzamanlılık:
- `bench-c.json` içinde tüm dosyalar `status: ok`
- logda `finish_reason='length'` veya zaman aşımı yok
- en uzun `llm_request ... elapsed_seconds` < `VLLM_TIMEOUT_SECONDS` / 3
- bir önceki değere göre toplam `elapsed_seconds` en az %15 daha iyi (değilse kazanç azalıyor)

### 3.3 Parça boyutu (seçilen eşzamanlılıkla)

```powershell
.venv\Scripts\python.exe scripts\benchmark_llm.py --concurrency <secilen> --files 16 --chars 12000 --chunk-chars 8000 --max-tokens 2048 > bench-p8000.json 2> bench-p8000.log
```

### 3.4 Uçtan uca (gerçek model, altın küme)

Önce ve sonra karşılaştırması: kuyruk oranı, canary ve terim sızıntısı, dosya başına istek ve
süre.

```powershell
.venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-<tarih> --name once
# .env'de onerilen degerler uygulandiktan ve backend yeniden baslatildiktan sonra:
.venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-<tarih> --name sonra
```

`presence_penalty` denemesi yalnızca gerekirse ve geçici ortam değişkeniyle yapılır:

```powershell
$env:VLLM_PRESENCE_PENALTY="1.5"
.venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-<tarih> --name pp15
Remove-Item Env:VLLM_PRESENCE_PENALTY
```

Kabul ölçütü: canary sızıntısı 0 ve terim sızıntısı artmıyor. Aksi halde 0'da kalır.

### 3.5 Gerçek proje

Bkz. `golden-baseline-20261001/RAPOR.md` bölüm 4.3–4.4. `failed_check_summary.py` çıktısında
şu kodların payı Faz 1 ayarlarından sonra düşmeli:
- `llm_tespit`, `llm_denetimi_tamamlanamadi`
- hata sınıflarında `ReadTimeout`, `HTTPStatusError_5xx`, `yanit_kesildi`

### Benchmark sınırlamaları

- Sentetik ve tekrarlı metin kullanır; bulgu az olduğu için çıktı kısadır. Yoğun dosyalardaki
  kesilmeyi temsil etmez; bunun için 3.4 ve 3.5 gerekir.
- DB'deki kurum talimatlarını prompta eklemez.
- `--max-tokens` ortam değişkenini ezer. `VLLM_PRESENCE_PENALTY` ve `VLLM_DISABLE_THINKING`
  ortamdan okunur.
- Komutlar bu ortamda stub LLM'e karşı denendi (eşzamanlılık 1/2/4, 8 dosya); süreler gerçek
  modeli temsil etmez.

## 4. Riskler

- **Eşzamanlılığı artırmak** sunucu kapasitesini aşarsa kök neden 3'ü (zaman aşımı → karantina)
  büyütür. Bu yüzden önce ölçülmeli.
- **Kurum içi vLLM'i başka istemciler de kullanıyorsa** ölçümler o yükle birlikte
  yorumlanmalı.
- Bu ayarlar **kuyruk oranını yalnızca altyapı kaynaklı karantina** payında düşürür.
  `llm_denetimi` kaynaklı karantinayı Faz 2 hedefler.

## Ek A — TypeError notu (`diagnostics/llm-typeerror-20260928/RAPOR.md`)

**Kısa cevap.**
- Python 3.11 ↔ 3.14 farkıyla ilişkisi bulunamadı.
- Kütüphane sürümüyle ilişkisi olası değil.
- En olası neden intranette karışık sürümde (kısmen kopyalanmış ya da yeniden başlatılmamış)
  kod çalışması.
- Hangi commit'te düzeltildiği bu repo'dan belirlenemiyor.

**Kanıtlar:**
- **Python 3.14.** Tam test takımı (1454 test) Python **3.14.7** ve 3.11.15 üzerinde geçti.
  `scripts/check_llm_preflight.py` iki sürümde de `RESULT=OFFLINE_OK` verdi (DB talimatsız
  tespit, DB talimatlı tespit, denetim).
- **Hatanın konumu.** Hata `requests=0` ile, HTTP çağrısı başlamadan oluşmuş. Bu yolda yalnızca
  stdlib ve pydantic ayar nesnesine öznitelik erişimi var; 3.14'e özgü bir API kullanılmıyor.
- **Kütüphane.** `httpx` hiç çağrılmamış. Ayar türü hatası (ör. eşzamanlılığın metin olması)
  önceki raporda denendi; denetimi de bozduğu için gözlenen desene (yalnızca tespit hata veriyor,
  denetim başarılı) uymuyor.
- **Desen eşleşmesi.** Önceki rapor, eski parametre listeli bir `build_detection_request` ile
  gözlenen deseni **birebir** üretmişti.
- **Commit.** Olay, repo'nun ilk commit'inden (`68edc8e`, 28.09) önce yaşanmış. O commit zaten
  tanılama ekini (`detector_crash ... frames=`) ve yanıt ayrıştırma düzeltmesini (liste/nesne
  güven değeri) içeriyor. Kesin hata satırı intranetten hiç alınmadı.
- **Süren risk.** 28.09'dan sonra modüller arası LLM çağrı imzaları dört commit'te değişti:
  `feb39f6`, `52d369d`, `93b209e`, `3015498`. Kısmi kopyalama riski bu yüzden sürüyor. Bugün tek
  bir eski modülü güncel kodla karıştırmak dosya başına TypeError değil import hatası veriyor
  (denendi: `llm_recognizer`, `llm_runtime`, `detectors` için `ImportError`; preflight bunu
  yakalıyor).

**Ne gerekiyor:**
1. `app/` klasörünün tamamını tek commit'ten dağıtın ve backend'i yeniden başlatın.
2. `check_llm_preflight.py` çalıştırın. `OFFLINE_OK` dışındaki her sonuçta `FAIL ... frames=`
   satırını paylaşın.
3. Gerçek export'tan sonra `failed_check_summary.py` çıktısında `tespit_katmani` altında
   `TypeError` görünüyorsa sorun sürüyor demektir. Backend logundaki `detector_crash ... frames=`
   satırı dosya:satır:fonksiyon bilgisini verir (içerik içermez).

Windows + Python 3.14.3 bu ortamda test edilemedi. Varsayım: platform farkı bu yolda davranışı
değiştirmez.

## Ek B — `UserService` yanlış pozitifi

**Hangi katman?** LLM tespit katmanı (stub). Placeholder adı `mask_ic_servis_adi_1`, LLM tip
etiketi `IC_SERVIS_ADI`'den geliyor. Stub bunu bilerek "orta" güvenle döndürüyor
(`expected.json` → `stub_false_positives`). Gerçek Qwen'in bunu yapıp yapmadığı bilinmiyor;
ama aşağıdaki filtre açığı modelden bağımsız.

**Neden generic filtreden geçti?** Zincir:
1. `_llm_confidence_route`: `orta` ≥ eşik (`orta`) → `mask`.
2. `term_classifier.is_generic_code_token("UserService")` → `False`. Sebep: `classify_term`
   identifier'ı **bir bütün olarak** sözlüklere bakıyor. `user` ve `service` ayrı ayrı
   "suspicious" (genel kelime), ama `userservice` hiçbir listede yok, sonuç `ok`. Aynı açık
   `OrderController`, `CustomerRepository` gibi tamamen genel parçalardan oluşan her bileşik
   ad için geçerli (doğrulandı).
3. `TokenBoundaryValidator`: `class UserService {` ve `private final UserService userService;`
   içinde ad çıplak bir identifier; nokta veya parantezle devam etmediği için sezgisel kaynakta
   reddedilmiyor.
4. Sonuç: hem sınıf bildirimi hem `MusteriService` içindeki tür kullanımı maskeleniyor. Dosya
   adı aynı kaldığı için javac "public class ... should be declared in a file named ..."
   hatası veriyor. Stub bu değeri her dosyada döndürdüğü için dosyalar arası tutarlılık
   bozulmuyor.

**Çözüm yönü (kod değişikliği; onayınızı bekliyor).** Generic kontrolü, identifier'ı parçalarına
(camelCase / PascalCase / snake / kebab) ayırıp **tüm parçalar genel kelimeyse** generic sayacak
şekilde genişletmek. Örnekler:
- `UserService` → user + service → generic
- `PoseidonGatewayClient` → poseidon genel değil → generic değil

Bu, Faz 3'ün parça bazlı ayrıştırıcısıyla doğal olarak örtüşüyor. İki seçenek var:
- Faz 2'nin politika motoruna (LLM aday filtresi) bayrak arkasında eklemek
- Faz 3'te ayrıştırıcıyla birlikte yapmak

Risk: tamamen genel kelimelerden oluşan gerçek bir kurumsal ad (ör. bir iç ürünün adı
"DataBridge") maskelenmez. Bu durumda doğru yer sözlüktür (kesin bulgular bu filtreden
geçmez).
