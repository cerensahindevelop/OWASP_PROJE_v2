# Çok dosyalı yüklemede Network Error incelemesi — 24.09.2026

## Son gözlem ve yükleme kuyruğu düzeltmesi

Kullanıcı son mesajında dosyalar **yüklenirken**, yükleme listesindeki bazı dosyaların `AxiosError: Network Error` ile kırmızı olduğunu açıkladı. Bu gözlem tarayıcı → Streamlit dosya yükleme aşamasına aittir; LLM taraması bu dosya satırı hatasını üretmez. Yerelde aynı mesajı üreten sınırsız istek mekanizması aşağıdaki ilk deneyde kanıtlandı. İntra tarayıcısındaki alt hata kodu hâlâ alınmadığından CORS/proxy gibi diğer aktarım nedenlerinin tümü dışlanmış değildir.

Uygulamaya dört eşzamanlı dosya PUT isteğiyle sınırlı bir tarayıcı kuyruğu eklendi. Yalnızca aynı origin üzerindeki Streamlit `/_stcore/upload_file/{session}/{file}` adreslerine giden asenkron XHR PUT istekleri kuyruğa girer. İptal, HTTP hata yanıtı, ağ hatası ve gönderim istisnası kuyruk yerini serbest bırakır. Diğer istekler sınırlandırılmaz; dosya içerikleri, XSRF başlıkları ve Axios'un hata/ilerleme davranışı korunur. Otomatik yeniden gönderim eklenmedi.

Kod uygulamanın sabit JavaScript kaynağıdır; kullanıcı içeriği JavaScript olarak çalıştırılmaz. Streamlit'in `st.html(..., unsafe_allow_javascript=True)` API'siyle yüklenir ve sayfa yeniden çalıştığında aynı kuyruk tekrar sarılmaz. Paket JS dosyaları ve offline wheel'ler değiştirilmez; bağımlılık gereksinimi test edilen en düşük sürüm olan `streamlit>=1.58` olarak güncellendi. Mevcut flash wheel sürümü 1.63.0 bu koşulu karşılar.

| Doğrulama | Sonuç |
| --- | --- |
| Streamlit 1.63, gerçek Axios ile aynı anda başlatılan 10.000 aktarım | 10.000 HTTP 204; 0 Network Error; gerçek gönderimde tepe 4; 9,00 sn |
| Streamlit 1.63, gerçek klasör seçicisi ve form gönderimi | 1.000/1.000 dosya Python'a ulaştı; 0 ağ hatası; yükleme 19,82 sn |
| Streamlit 1.58, gerçek klasör seçicisi ve form gönderimi | 500/500 dosya Python'a ulaştı; 0 ağ hatası; yükleme 6,16 sn |
| Chromium kuyruk regresyon testleri | 2 test geçti: iptal, ağ/HTTP hatası, gönderim istisnası, yeniden çalıştırma, diğer isteklere erişim |
| Streamlit 1.63, gerçek seçicide 5.000 dosya | Tamamlanmadı: 241,12 saniyede 1.264 HTTP 204; o ana kadar 0 ağ hatası. Arayüz çok yavaş; bu test başarılı sayılmadı. |

**Sınır:** Ağ isteği yığılması giderildi; binlerce satırlık Streamlit dosya listesinin performansı ayrı bir sınırlama olarak sürüyor. Çok büyük klasörlerde tek ZIP yükleme mevcut alternatif olmaya devam ediyor. 10.000 istek aktarım deneyi, 10.000 satırlık tam arayüz testi değildir.

Değişen üretim dosyaları: `app/webapp/app.py`, `app/webapp/upload_queue.py`, `app/webapp/assets/upload_queue.js`, `requirements.txt`. Tarayıcı regresyonları `tests/test_upload_queue_browser.py` içindedir. Kanıtlar `queue-*.json` ve deney betiğinde arşivlendi. Dört üretim dosyası flash paketine yedekli olarak aktarıldı ve SHA-256 ile doğrulandı; aktarım kaydı `queue-flash-update.json` içindedir. İntra uygulamasının yeniden başlatılması ve tarayıcı sayfasının yenilenmesi gerekir.

> **Kullanıcı açıklaması sonrası güncelleme:** Hata dosya seçerken değil, **Tara düğmesine basıldıktan sonra** oluşuyor; dosya sayısı değişken. Aşağıdaki ilk yükleme deneyi kullanıcının olayının kök nedenini kanıtlamaz. LLM olasılığı açık tutulmalıdır. İntra ortamından tam hata metni, hatanın gösterildiği bölüm ve geçen süre henüz alınmadı.

## Tarama aşamasının incelemesi

- Arayüz → backend: tarama tek HTTP isteğinin yanıtında tamamlanıyor. `WEB_API_REQUEST_TIMEOUT_SECONDS` varsayılanı 300 saniye. Özellikle yanıt verisi gelmeden geçen süre bu ayarı aşarsa ReadTimeout oluşabilir. Bu ayar mutlak toplam iş süresi sınırı değildir; HTTPX bağlantı/yazma/okuma/havuz aşamalarına uygulanır. Hata bütün sonuç isteğini etkiler ve backend işinin gerçekten durduğunu kanıtlamaz.
- Backend → LLM: `VLLM_TIMEOUT_SECONDS` varsayılanı 60 saniye. Tespit katmanı LLMRecognitionError için üç deneme yapıyor; ikincil denetimin de ayrı yeniden denemeleri var. Başarısızlıklar dosya bazında rapora ve karantinaya yansıyabilir. HTTP hata kodu, bağlantı hatası, yanıt zaman aşımı ve geçersiz JSON birbirinden ayrılmalıdır. Gerçek intra ayarları doğrulanmadı.
- Dosya sayısı tek başına süreyi belirlemiyor: dosya uzunlukları, parça sayısı, modelin yanıt süresi ve yeniden denemeler etkilidir. Eşzamanlılık semaforu mevcut export çalışması içinde oluşturuluyor; ayar açıklamasındaki sistem geneli ifadesi tek başına tüm kullanıcıların toplam LLM trafiğinin sınırlı olduğunu kanıtlamıyor.

### Kanıtlanan tanılama kusuru ve yapılan değişiklik

Eski arayüz bütün HTTPX RequestError türlerini `Backend API'sine bağlanılamadı` mesajına indiriyordu. LLM istemcisi de yalnızca `str(exc)` kullanıyordu; bu değer ReadTimeout için boş kalabiliyor. Dolayısıyla mevcut mesajlar kesin neden tespitini zorlaştırıyordu.

Yerel kaynakta HTTP hata tanılaması düzeltildi: bağlantı katmanı, istisna sınıfı, geçen süre, timeout ayarı ve varsa HTTP durum kodu korunuyor. Host, API anahtarı, istek/yanıt gövdesi ve ham istisna mesajı tanı ayrıntısına eklenmiyor. Backend zaman aşımında kullanıcının yeniden tarama başlatmadan önce işlem geçmişini kontrol etmesi öneriliyor. Süre ayarları, maskeleme kuralları, LLM yeniden deneme sayıları ve karantina davranışı değiştirilmedi.

- `masking_service/app/core/http_diagnostics.py`: içerik sızdırmayan ortak teknik ayrıntı.
- `masking_service/app/webapp/api_client.py` ve `common.py`: backend bağlantısı ile zaman aşımı ayrımı.
- `masking_service/app/services/llm_recognizer.py`: LLM bağlantı, HTTP hata kodu ve JSON hatası ayrımı.
- `masking_service/tests/test_http_diagnostics.py`: katman/tür korunması, boş istisna metni, hassas veri sızmaması, yeniden gönderim yapılmaması ve başarılı yanıtın korunması.

Yalıtılmış geçici veritabanında yeni testler ve ilgili mevcut LLM/denetim/arayüz testleri: **33 geçti**. Gerçek yerel HTTP sunucusunda geciktirilmiş yanıtla iki katmanda da ReadTimeout üretildi; sonuçlar `scan-timeout-results.json` dosyasında. Bu deney yalnızca tanılama düzeltmesini doğrular, intra olayının zaman aşımı olduğunu kanıtlamaz. Değişiklikler kullanıcının talimatıyla flash paketine aktarıldı; üç mevcut dosya yedeklendi, bir yeni modül eklendi ve dört dosya SHA-256 ile doğrulandı. Güncelleme kaydı `flash-update.json` içindedir. İntra kurulumuna aktarım ve çalışan servislerin yeniden başlatılması henüz doğrulanmadı.

## Sonuç ve kesinlik sınırı

Flash dağıtımındaki Streamlit 1.63.0 yükleme istemcisinin kullandığı Axios ve gerçek Streamlit yükleme endpoint'i ile **LLM çalıştırmadan** aynı `Network Error` yeniden üretildi. Tarayıcıda alttaki hata `net::ERR_INSUFFICIENT_RESOURCES` idi. Kontrolsüz sayıda eşzamanlı yükleme isteği kaynak sınırına takılıyor; dört eşzamanlı isteğe geçince aynı sayıdaki dosyada hata kalmadı.

Bu, yerelde kanıtlanan bir hata mekanizmasıdır. İntra ortamın tarayıcı kaydı elimizde olmadığı için kullanıcının olayının kesinlikle aynı koddan kaynaklandığı henüz doğrulanmış değildir. `Network Error` tek başına kök nedeni belirtmez; bağlantı kesilmesi, proxy veya CORS sorunları da bu genel mesajı oluşturabilir.

## Kontrollü deney

| Dosya/istek sayısı | İstemcide eşzamanlı başlatma sınırı | Başarılı HTTP 204 | Network Error | Süre |
| --- | --- | --- | --- | --- |
| 2.000 | 2.000 | 2.000 | 0 | 1,71 sn |
| 10.000 | 10.000 | 8.961 | 1.039 | 7,92 sn |
| 10.000 | 4 | 10.000 | 0 | 9,26 sn |

1.039 başarısız isteğin tamamında Chromium `net::ERR_INSUFFICIENT_RESOURCES`, Axios `ERR_NETWORK: Network Error` bildirdi. Dosyalar yalnızca 464 baytlık yapay metin içeriyordu; toplam dosya içeriği 4,64 MB idi. Bu deneyde büyük tek dosya, LLM yanıtı, FastAPI multipart sınırı veya intra proxy bulunmuyordu. Dosya sayısı için evrensel bir hata eşiği çıkarılamaz; tarayıcı sürümü, kaynaklar ve yanıt hızları sonucu etkiler.

Deney yöntemi: minimal Streamlit uygulamasında tek dosya ile gerçek oturum ve yükleme URL'si edinildi. Dağıtım paketinin kendi Axios modülü tarayıcıya import edildi. Gerçek `/_stcore/upload_file/{session_id}/{file_id}` endpoint'ine her dosya için ayrı kimlikle multipart PUT gönderildi. İlk senaryoda tüm istekler başlatıldı; kontrol senaryosunda dört worker kullanıldı. Kimlik doğrulama/XSRF kapatılmadı. Bu deney ağ aktarımını izole eder; 10.000 satırlı React arayüzünün tam uçtan uca performans testi değildir.

Yerel kurulu Streamlit 1.58.0'ın gerçek klasör seçicisiyle ayrı ön deneyde 100 ve 1.500 dosya hatasız yüklendi. 5.000 dosyalık denemede arayüz yanıt süresi aşımı oluştu; sonuç tamamlanamadığı için bu koşu Network Error kanıtı olarak kullanılmadı.

## Projedeki neden zinciri

1. `masking_service/app/webapp/export_page.py:351`: klasör yükleme `st.file_uploader(..., accept_multiple_files="directory")` kullanıyor.
2. Dağıtım Streamlit paketinin `streamlit/static/static/js/FileUploader.Dni9Wiwg.js` dosyasında elde edilen dosya URL'leri `forEach` ile upload callback'ine gönderiliyor. Bekleyen dosyaları sınırlayan bir kuyruk yok.
3. `streamlit/static/static/js/index.ByR4Z2EF.js` içindeki `FileUploadClient.uploadFile`, bekleyen istek sayısını form durumu için takip ediyor; bu sayaç eşzamanlılık sınırı uygulamıyor. Her dosya ayrı multipart PUT ile gönderiliyor.
4. `streamlit/static/static/js/axios.CpRZK4nx.js` içindeki XHR `onerror`, hatayı `ERR_NETWORK / Network Error` olarak iletiyor. Yükleyici bu istisna metnini dosya satırına yazıyor.
5. `masking_service/app/webapp/export_page.py:368` sonrasında **Taramayı Başlat** gönderimiyle `_run_export_upload` çağrılıyor. Ancak bu aşamada `api_client.export_upload` FastAPI `/export/upload` isteğini gönderiyor. LLM çağrıları backend işleme aşamasında gerçekleşiyor. Dosya seçimi sırasında dosya satırında çıkan bu hata, LLM çağrısının hata mesajı değildir.

## Ayrı bulunan riskler

- `masking_service/app/core/config.py:168`: backend istek zaman aşımı varsayılanı 300 saniye. Uzun tarama bu sınırı aşarsa ayrı bir arayüz/backend zaman aşımı yaşanabilir; etkin intra değeri doğrulanmadı. `api_client.py:74` tüm `httpx.RequestError` türlerini genel bağlantı mesajına dönüştürüyor. Bu risk yukarıdaki Axios dosya yükleme hatasıyla karıştırılmamalı.
- Streamlit yüklenen dosyaları bellekte tutuyor; backend de yükleme içeriklerini okuyor. Büyük toplam veri ek bellek baskısı yaratabilir. Yerel kontrollü Network Error deneyinde dosyalar çok küçük olduğu için bu, gözlenen sonucun açıklaması değildir; intra RAM ölçümü yapılmadı.
- `.streamlit/config.toml` içindeki `maxUploadSize = 10240` dosya boyutu sınırıdır; eşzamanlı istek sayısını sınırlamaz. `maxMessageSize` da yükleme kuyruğu sağlamaz. Bu değerleri artırmak kanıtlanan sorunu çözmez.

## İntra ortamda kesin eşleştirme

1. F12 → Network ve Console açıkken hatayı yeniden oluşturun; Network'te `upload_file` filtreleyin.
2. Başarısız PUT için tarayıcının hata kodunu alın. `net::ERR_INSUFFICIENT_RESOURCES` görülürse yerelde yeniden üretilen mekanizmayla eşleşir.
3. Farklı hata/HTTP durumunda o kaydı incelemek gerekir: örneğin HTTP 413 boyut sınırına, 403 yetkilendirme/XSRF kontrolüne, bağlantı reseti ağ/proxy/sunucu kesintisine işaret edebilir. Kod tek başına bütün bu katmanların kök nedenini ispatlamaz.
4. Dosya sayısı, toplam boyut, tarayıcı ve gerçek intra Streamlit sürümünü kaydedin. Ham HAR paylaşmak gerekmez; URL'nin oturum kimliği ve dosya içeriği olmadan hata kodu yeterlidir.

## Düzeltme yönü

Kalıcı çözüm dosya yüklemelerini istemci tarafında sınırlı bir kuyruğa almak; örneğin en fazla dört eşzamanlı istek, yalnızca geçici ağ hatalarında sınırlı yeniden deneme ve tüm seçili dosyaların alındığını doğrulamaktır. Dört sınırı kontrollü aktarım deneyinde doğrulandı; üretim arayüzüne henüz uygulanmadı. LLM concurrency ayarını değiştirmek bu aşamadaki istekleri sınırlamaz.

Mevcut uygulamada geçici yol: klasörü tek ZIP yapıp **Dosya(lar) / .zip** seçeneğinden yüklemek. Proje ZIP açmayı destekliyor; tek yükleme çok sayıda eşzamanlı dosya isteğini ortadan kaldırır. ZIP boyutu, toplam bellek ve sonraki tarama süresi sınırları ayrıca geçerlidir.

İlk yükleme deneyi uygulama kodunu, kurulu paketleri veya flash içeriğini değiştirmedi. Kullanıcının sonraki açıklaması üzerine yapılan yerel tanılama değişiklikleri raporun başında ayrıca açıklanmıştır. Ham ölçümler `transport-results.json`, ön deney `widget-results.json`, ortam bilgisi `environment.json` içindedir. Kullanılan deney betikleri aynı klasörde arşivlendi; mutlak yollar test makinesine aittir, otomatik üretim başlangıcına dahil değildir.
