# Maskeleme Sistemi

Hassas kurumsal verileri (IP, e-posta, proje adı, sicil no, secret/API key vb.)
otomatik maskeleyen, ihtiyaç halinde geri dönüştürülebilir hale getiren sistem.
SQLite kullanır - ayrı bir DB sunucusu gerekmez. Detaylı mimari/güvenlik
notları için `app/` altındaki modül docstring'lerine bakın.

## Parola / sır ataması kuralı

`generic_secret_assignment` (sözlük/regex katmanı, LLM'den bağımsız) şu
anahtarların değerini maskeler: `password`, `passwd`, `passphrase`, `pwd`,
`pass`, `secret`, `token`, `api_key`, `credential(s)`, `parola`, `şifre`/`sifre`
(önekli/sonekli biçimleri, JSON anahtarları ve connection string alanları dahil).
Değer en az 6 karakter olmalıdır. Parola sayılmayanlar: ortam değişkeni ve şablon
referansları (`${DB_PASS}`, `$DB_PASS`, `%PASSWORD%`, `{{ vault.pw }}`),
`null`/`changeme`/`******` gibi yer tutucular, `{token}`/`%s` biçim yer tutucuları,
ölçü/ayar anahtarları (`token_count`, `password_min_length`, `token_url`,
`password_file`) ve tırnaksız kod ifadeleri (`token = tokenizer`,
`token: Optional`, `secret_key = s3_connection`). Bilinen sınırlar: tırnaksız ve
yalnızca harften oluşan parola (`pwd=sunshine`) ile kodda parametre olarak geçen
parola (`new NetworkCredential("sa", "…")`) bu kuralla yakalanmaz, LLM
katmanına kalır. Kural `alembic upgrade head` ile güncellenir.

## LLM tarama kapsamı

`VLLM_ENABLED=true` olduğunda, desteklenen tüm metin dosyaları sözlük/regex,
Presidio ve LLM ile taranır. İlk taramada bulgu çıkmayan metinler de maskeleme
sonrası LLM denetiminden geçer. Boş içerik için modele istek gönderilmez.

`.git` (worktree işaretçi dosyası dahil), `.hg`, `.svn`, bağımlılık/önbellek
ve derleme dizinleri tarama başında elenir. Desteklenmeyen binary/Office/PDF
ve arşiv içerikleri LLM'e gönderilmez ve çıktıya alınmaz. Lock dosyaları
bütün katmanlarla taranır; temizse aynen kopyalanır, hassas bulgu varsa
karantinaya alınır. Sözdizimi, geri dönüş ve tutarlılık kontrolleri sürer.

LLM bulgularının onay politikası: `VLLM_AUTO_MASK_MIN_CONFIDENCE` (varsayılan
`orta`) ve üstündeki bulgular onay beklemeden maskelenir; altındakiler
`VLLM_LOW_CONFIDENCE_ACTION=ignore` ile yalnızca işlem kaydına yazılır
(`review` ile onay kuyruğuna gider). Maskeleme sonrası LLM denetimi bir dosyayı
yalnızca maskelenmemiş somut bir değeri metinde birebir geçen bir alıntıyla
gösterirse karantinaya alır; doğrulanamayan "risk var" yanıtları dosyayı
bekletmez. LLM'in serbest yazdığı bulgu türü sabit bir listeye eşlenir, böylece
yer tutucu adına hassas bir terim girmez.

Tespit ve son denetim, sezgisel bulgular için aynı genel değer politikasını
kullanır. `SCAN_GENERIC_COMPOUND_FILTER=true` olduğunda `UserService` gibi
genel bileşik adlar son denetimde de elenir. Geniş kod alıntıları içindeki
literal değerler, yorumlar ve genel olmayan tanımlayıcılar ayrı değerlendirilir;
sözlük ve kesin kuralların kontrolleri sürer. Çelişkili bir denetim yanıtında
(`risk_var=false` ve doğrulanmış hassas bulgu) karar, metinde doğrulanmış
bulgular üzerinden riskli olarak normalize edilir ve çelişki sayısal olarak
loglanır. Bu durum ek model çağrısı veya teknik hata üretmez; normal otomatik
maskeleme/inceleme akışı işler. Bağlantı, JSON/şema ve kesilme hataları teknik
doğrulama hatası olarak dosyayı bloke etmeye devam eder.

Onay kuyruğu aynı dosyadaki aynı değer/varlık tipi için tek kayıt oluşturur.
Eski tekrar kayıtlarına verilen bir karar da aynı işlem ve dosyadaki eşdeğer
bekleyen kayıtları atomik olarak sonuçlandırır. Son dosya denetimi karar
grubundan sonra bir kez çalışır. Onay/red/maskeleme uçları FastAPI threadpool'unda
çalışır; DB, dosya/manifest yazma ve cevap hazırlama API event loop'unu bloke
etmez. Model HTTP çağrıları işlem boyunca kendi async döngüsünde yürür.

İnceleme ekranı açıldığında, güncel doğrulayıcının artık somut açık değer
bulamadığı eski LLM uyarıları `POST /audit-warnings/revalidate-pending` ile
arka planda yeniden değerlendirilir. Ek bir kullanıcı onayı veya suppression
kararı oluşturulmaz. Dosya yalnızca tutarlılık, açık terim, geri çözüm,
sözdizimi ve son AI kontrolleri geçerse çıktıya eklenir. Teknik hatalar,
tamamlanmamış export'lar, lock dosyaları ve Java bytecode bu akışa alınmaz.
Arayüz kontrol sürerken sonuçları 5 saniyede bir günceller, bitince periyodik
sorgulamayı durdurur; kontrol edilen kayıtlar ayrıca
gösterilir. Worker'lar arasında veritabanı tokeni aynı kaydın tekrar alınmasını
önler; süre aşımında eski worker dosyayı serbest bırakamaz. Eşzamanlı dosya
sayısı ortak kilit dizinini kullanan tüm worker'larda toplam en fazla 4'tür;
model çağrıları export ve kullanıcı kararlarıyla aynı LLM kotasını paylaşır.
Her otomatik kontrolün DB oturumu worker thread'inde açılıp kapatılır. İptalde
worker'ın rollback/kapanışı beklenmeden kayıt tokeni temizlenmez. Başarısız kontrollerin
aynı içerik/ayarlarla otomatik tekrarı 5 dakika bekler; geçerli denetim kaydı
yeniden kullanılır. Dosya başına otomatik kontrol bütçesi 60–600 saniyedir;
bütçe dolarsa dosya çıktıya alınmaz. Bu sürümün ek alanları için
`alembic upgrade head` gerekir.

Denetim kaydı anahtarı değiştiğinde eski kayıt serbest bırakma/yeniden doğrulama
sırasında yeniden kullanılamaz; bu yük ortak kota ve dosya sınırı ile dağıtılır.
Normal export önceki export'un denetim kaydını okumaz; anahtar değişimi ilk
export'a ayrıca tüm dosyalar için bir denetim turu eklemez.

Modelin tespit yanıtının yapısı (JSON / `bulgular` listesi) bozuksa o metin
parçası hata sayılır ve dosya karantinaya alınır. Yapı sağlam ama tek bir bulgu
bozuksa (boş ya da yanlış türde `tip`, geçersiz `guven_seviyesi`, eksik
`gerekce`) parça düşürülmez: bulunan değer metinde birebir geçiyorsa bulgu `orta`
güven ve `KURUMSAL_TANIMLAYICI` türüyle maskelenir, geçmiyorsa ya da değer hiç
okunamıyorsa yalnızca o bulgu atılır. Onarılan/atılan sayıları işlem kaydına
değer yazılmadan düşer (`llm_bulgu_semasi_bozuk onarilan=N atilan=M`).

Tutarlılık adımı, aynı işlemin diğer dosyalarında maskelenen değerlerin açık
kalan geçişlerini, açık geçiş kalmayana kadar en fazla 3 tur değiştirir. Sonra
geri dönüş ve sözdizimi kontrolleri ile final güvenlik taraması çalışır; hâlâ
açık geçiş varsa dosya dışa aktarılmaz.

Onaydan sonra serbest bırakılan dosyalar çıktıdaki maskelenmiş yola yazılır,
aynı işlemin diğer dosyalarında maskelenen değerlere karşı tutarlılık
kontrolünden geçer ve imzalı bütünlük kaydına eklenir.

Karantinadaki bir dosya için "Yanlış Alarm" ya da "Maskele" seçildiğinde ve
inceleme kararları (onay/ret/dosyayı maskele) tamamlandığında son LLM denetimi
veritabanı yazma kilidi tutulmadan çalışır; bu sırada diğer export'lar
beklemez. İnceleme kararları denetimden önce kaydedilir; onaylanan değer
maskelenemezse karar geri alınır. Dosya ve bütünlük kaydı ancak karar veritabanına
kaydedilirse kalır; kayıt başarısız olursa geri alınır. Doğrulamadan geçemeyen
bir karar kalıcı hale gelmez (yalnızca işlem kaydına yazılır). "Yanlış Alarm"
denetimin gösterdiği tüm değerleri kapsar. Son denetim aynı değeri daha kısa
alıntılasa da (ör. önce "Hakan Yılmaz", sonra "Hakan") bu değerin dosyadaki
tüm geçişleri kararın kapsamındaysa bastırılır. Kaynakta zaten bulunan bir
sözdizimi hatası (ör. yorumlu JSON) serbest bırakmayı engellemez. Kurumsal
terim karantinasında inceleyen kişi açık değeri görür; terim sözlükten sonradan
silinse bile değer export anındaki gerekçeden gösterilir. Bütünlük kaydı
(manifest) güncellemesi işletim sistemi dosya kilidiyle korunur; backend birden
fazla worker/süreçle çalıştırılabilir. Kilit dosyası çıktı klasörünün yanında
(`.<klasör>.masking-manifest.lock`) durur, indirilen çıktıya girmez; 30 saniyede
alınamazsa işlem hata verir ve tekrar denenebilir.

Aynı içerik, aynı karar: export sırasında bir dosyanın LLM denetim sonucu
karantina kaydına şifreli olarak yazılır. Dosya içeriği ve denetim ayarları
(model, prompt, parçalama, dosya adı) değişmeden serbest bırakılırsa model
yeniden sorulmaz, kayıtlı sonuç kullanılır; böylece aynı dosya bir denemede
geçip diğerinde takılmaz. İçerik değiştiyse (ör. "Maskele" ya da tutarlılık
maskelemesi) model yeniden çalışır ve başarısız denemenin sonucu da kaydedilir.
Modeli hiç çağırmadan üretilen "temiz" sonuçlar (LLM kapalı, denetim atlandı)
kaydedilmez. Kayıt `alembic upgrade head` ile eklenen `denetim_sonucu`
sütunundadır.

Export web arayüzünde arka plan işi olarak çalışır; ekran işlenen/toplam dosya
ilerlemesini gösterir. Export sırasında veritabanı yazma kilidi LLM
çağrıları boyunca tutulmaz; aynı anda başka projelerin export'ları ve onay
işlemleri beklemeden çalışabilir. Presidio/spaCy analizi olay döngüsünü
kilitlememek için ayrı bir thread'de (aynı işlemin dosyaları arasında sırayla)
çalışır; böylece eşzamanlı LLM isteklerinde sahte zaman aşımı oluşmaz.
Uygulama bir export sırasında kapanırsa
işlem `recover-output` ile kapatılır (bkz. 4. bölüm).

Arka plan export kuyruğu tek backend sürecinde varsayılan olarak en fazla
2 işi çalıştırır, 16 ek işi FIFO sırada bekletir. Arayüz bekleyen işler için
“Sıradasınız” gösterir. Kuyruk dolduğunda yeni istek HTTP 429 ve
`Retry-After: 30` alır; reddedilen yüklemenin geçici dosyaları temizlenir.
Sınırlar `EXPORT_JOBS_MAX_RUNNING` ve `EXPORT_JOBS_MAX_QUEUED` ile ayarlanır
ve backend yeniden başlatıldığında uygulanır. Bu sınırlar arka plan `/jobs`
uçlarına aittir; eski senkron uçları veya CLI işlerini kapsamaz. Kuyruk ve
ilerleme kaydı süreç belleğindedir; bu değişiklik çok-worker dağıtımı veya
yeniden başlatmada otomatik devam desteği sağlamaz.

İş başlangıcında kurtarma günlüğü oluşturulup çalışma kaydı kaydedilir;
pahalı detector/model kurulumu açık bir DB transaction'ı olmadan yapılır.
Kurulum hatasında çalışma `failed` olur, eski çıktı korunur. Hata durumu
DB'ye yazılamazsa günlük offline kurtarma için saklanır. Eksik çıktıların
dosya sayısı arayüzde ayrıca gösterilir.

Yereldeki model ile intradaki Qwen modeli farklı olabilir. Her ortamın
`VLLM_HOST` ve `VLLM_MODEL` değerlerini kendi sunucusunun sunduğu adla ayarlayın.

### LLM verimliliği ve hız

- **HTTP bağlantıları yeniden kullanılır.** Her export, tespit/denetim ve yeniden
  denemeler boyunca aynı `LLMHttpTransport` bağlantı havuzunu paylaşır. İstemci
  ilk gerçek istekte açılır; başarı, hata veya iptal sonrasında kapatılır.
  Tek başına çağrılan tespit/denetim işlemi kendi havuzunu yönetir. Ayrı
  thread/event loop'ların istemcileri paylaşılmaz. Havuz en fazla 100 açık,
  20 boşta bağlantı tutar; boşta bağlantı süresi 30 saniyedir. Model isteklerinin
  eşzamanlılığı endpoint başına ortak `VLLM_MAX_CONCURRENT_REQUESTS` sınırına tabidir;
  farklı event loop, thread, backend worker ve CLI işleri aynı OS dosya kilitlerini
  kullanır. Varsayılan kilit dizini aynı kullanıcının sistem geçici dizinidir;
  `VLLM_ADMISSION_DIR` ile değiştirilebilir. Tüm worker'larda aynı limit ve dizin
  gerekir. Ayrı container/host için OS kilitlerini destekleyen ortak mount veya
  model gateway kotası gerekir; bu mekanizma tek başına makineler arası bir
  kota servisi değildir. Kilit dosyaları servis çalışırken silinmemelidir.
  Başarı, hata, iptal veya process kapanışında kapasite geri bırakılır. Havuz
  dışında yapılan retry beklemesi model kotasını tutmaz; diğer kullanıcılar ilerler.
  sınırı da bağlantı sayısına ayrıca tavan koyar. HTTP toplam zaman aşımı,
  yeniden deneme ve karantina kuralları aynı kalır. `scripts/benchmark_llm.py`
  de her ölçüm grubunda bu yaşam döngüsünü kullanır. Gerçek hız kazancı model
  ve ağ gecikmesine bağlıdır; bu değişiklik modelin tespit doğruluğunu değiştirmez.
- **Yoğun veride kesilen yanıt daha küçük parçalarla yeniden taranır.**
  `finish_reason=length` yanıtında tespit ve son denetim aynı bölme yolunu
  kullanır; 200 karakterlik alt sınır, kısa ama çok bulgulu SQL dosyalarının
  da yeniden bölünmesini sağlar. En fazla 3 bölme seviyesi (başlangıç parçası
  başına en fazla 15 tarama denemesi; geçici bağlantı hatalarının yeniden
  denemeleri hariç) korunur. Bir alt parça bile tamamlanmazsa dosya
  bloke edilir; kesilmiş model yanıtı veya kısmi bulgular temiz sonuç sayılmaz.
- **Dağıtım imza kontrolü dekoratörleri tanır.** `llm_http_scope` importu doğrulanarak
  imzayı koruyan dekoratör olarak işlenir. Doğrudan kurulan veya bir kez yerel
  değişkene atanan servis nesnelerinin metotları da kontrol edilir. Bilinmeyen
  dekoratörler nedeniyle atlanan çağrı sayısı ayrıca raporlanır. Dinamik nesneler,
  kalıtımla gelen metotlar ve `*args/**kwargs` çağrıları kapsam dışındadır;
  `RESULT=OK` bütün dinamik çağrıların uyumlu olduğu anlamına gelmez.
- **Bilinen değerler modele gösterilmez.** Katman 1'in (sözlük/regex) kesin
  bulguları LLM'e `mask_<tür>_<n>` biçimli geçici yer tutucularla gider; model
  bunları tekrar listelemez, çıktı token'ı ve yanıt kesilmesi azalır. Çıktıdaki
  maskeleme her zaman orijinal metin üzerinden yapılır. Kapatmak için
  `VLLM_REDACT_KNOWN_FINDINGS=false`. Presidio bulguları gizlenmez.
- **Gömülü ikili veri LLM'e ve Presidio'ya gitmez.** Dosya türünden bağımsız,
  içeriğe göre tanınan biçimler: satır satır base64/hex (`.resx`, PEM, MIME),
  tırnaklı/birleştirilmiş base64 (C#/Java/JS sabitleri, `.ipynb` çıktıları),
  base64url, data URI (HTML/CSS/SVG), bayt dizileri (`0x89, 0x50, …`,
  `byte[] {…}`, `\x89\x50…`). `SCAN_ENCODED_BLOB_MIN_CHARS` (varsayılan 512) ve
  üstü uzunluktaki bloklar çözülerek sınıflandırılır; gerçekten ikili veri
  olanlar LLM'e `mask_kodlanmis_ikili_veri_<n>` yer tutucusuyla gider,
  Presidio'ya boşluk olarak girer. Okunabilir metne çözülen base64 (ör. base64
  ile gizlenmiş bir config) ve identifier/yol listeleri gönderilmeye devam eder.
  Katman 1 (sözlük/regex) blokları yine tarar, çıktı metni değişmez. Log satırı:
  `llm_input_encoded_blobs ... blobs=N hidden_chars=M`. Kapatmak için `0`.
- **Kodlanmış metindeki sırlar karantinaya alınır.** Base64/base64url/hex ya da
  bayt dizisiyle kodlanmış ve çözüldüğünde okunabilir metin veren değerler
  (ör. `appsettings.json` içinde base64 connection string, `c2E6UGFzc3cwcmQ=`
  = `sa:Passw0rd`) çözülüp sözlük/regex ve Presidio ile ayrıca taranır; parola
  ataması ve `kullanıcı:parola` biçimi ayrıca aranır. Bulgu varsa dosya
  **Güvenlik Karantinası**'na alınır, çıktıya yazılmaz; değer kodlanmış blok
  içinde maskelenmez (geri alma birebir aynı dosyayı üretmeye devam eder).
  Gerekçede yalnızca satır, kodlama türü ve genel bulgu türü görünür.
  İnceleme ekranında değeri kaynakta kaldırıp yeniden tarayın ya da gerçekten
  hassas değilse "Yanlış Alarm" ile serbest bırakın.
- **Erken uyarı.** Bir dosya `VLLM_WARN_CHUNKS_PER_FILE` (varsayılan 10) ya da
  daha fazla LLM parçasına bölünüyorsa veya gizlenemeyen kodlanmış-veri benzeri
  satırlar içeriyorsa işlem kaydına ve loga `llm_is_yuku_yuksek parca=…
  taninmayan_kodlanmis_satir=…` uyarısı yazılır (içerik yazılmaz). Böylece
  tanınmayan yeni bir dosya biçimi saatler sonra değil ilk dosyada görünür.
- **Export öncesi tahmin.** `python -m app.cli llm-is-yuku --kaynak <proje>
  [--istek-suresi 12]` LLM'e hiç istek göndermeden dosya başına tahmini istek
  sayısını, gizlenecek ikili veriyi ve uyarıları (tanınmayan kodlanmış veri,
  minified kod) listeler. Ağır ama gereksiz dosyalar hariç tutma kuralıyla
  ayrılabilir.
- **Üretilmiş dosyalar.** `obj/`, `.vs/`, `.gradle/`, `bower_components/`
  dizinleri taranmaz; `packages.lock.json`, `project.assets.json`,
  `npm-shrinkwrap.json`, `go.sum`, `Package.resolved`, `*.lockfile` kilit dosyası
  sayılır (LLM'e gitmez, yerel katmanlarla taranır).
- **Kelime ortası eşleşme yok.** LLM'in bildirdiği değer yalnızca kelime/identifier
  sınırında eşlenir (`PoseidonGatewayClient` içindeki `Poseidon` eşlenir,
  `Alignment` içindeki `Ali` eşlenmez). `VLLM_MIN_AUTO_MASK_CHARS` (varsayılan 3)
  altındaki değerler otomatik maskelenmez, `dusuk` güvenle
  `VLLM_LOW_CONFIDENCE_ACTION` kuralına düşer.
- **Dosya bağlamı.** Sistem promptunun sonuna yalnızca dosya adı ve uzantısı
  eklenir (dizin yolu gönderilmez).
- **Eşzamanlılık.** Tespit ve denetim adımları bir dosyanın parçalarını eş zamanlı
  gönderir; bir sonraki dosya grubunun tespiti, mevcut grubun denetimiyle aynı
  anda yürür. Toplam LLM isteği yine `VLLM_MAX_CONCURRENT_REQUESTS` ile sınırlıdır.
- **İsteğe bağlı denetim atlama.** `VLLM_AUDIT_UNCHANGED_FILES=false` iken hiçbir
  katmanın değiştirmediği ve LLM tespiti hatasız biten dosyalar ikinci LLM
  denetimine gönderilmez. Hız kazancı büyüktür ama ikinci bağımsız kontrol
  kalkar; varsayılan `true`.
- **Ortama göre ayarlar.** Ollama ve kurum içi vLLM için önerilen `VLLM_*`
  değerleri `.env.example`'da yazılıdır; `.env`'ye açıkça girin. Eşzamanlılık
  değerlerini `scripts/benchmark_llm.py` ile doğrulayın.
- **Ollama'da Qwen3.x thinking.** Ollama `chat_template_kwargs`'ı yok sayar;
  `qwen3.6:35b` gibi modellerde thinking'i yalnızca `VLLM_REASONING_EFFORT=none`
  kapatır. vLLM'de bu ayarı boş bırakıp `VLLM_DISABLE_THINKING=true` kullanın.
- **vLLM prefix caching.** Sistem promptu her istekte aynı önekle başlar; vLLM'i
  `--enable-prefix-caching` ile başlatmak ilk token gecikmesini düşürür.

## 1. İnternetsiz (offline/intra) ortamda kurulum

Intra makinede kurulum, güncelleme ve kullanım adımları paketle birlikte gelen
kılavuzdadır: [`masking_service/scripts/OFFLINE_INSTALL.md`](masking_service/scripts/OFFLINE_INSTALL.md)
(paketteki adı `README.md`).

Paketi internetli makinede hazırlamak için (`masking_service` klasöründe):

```bash
.venv/bin/python scripts/build_offline_bundle.py --output <hedef>/masking_system
```

Betik Windows/Python 3.14 paketlerini indirir, hiçbir gereksinimin kullanmadığı
paketleri ayıklar, `requirements.lock` ve `manifest.json`'u yazar ve yalnızca
intranette gereken dosyaları kopyalar. Oluşan `masking_system` klasörünün tamamı
flash belleğe kopyalanır. Geliştirme bağımlılıkları (testler dahil)
`requirements-dev.txt` ile kurulur ve pakete girmez.

## 2. Çalıştırma (backend + UI, iki ayrı süreç)

### Hızlı başlatma (tek komut)

```powershell
cd C:\masking_system\masking_service
.\start.ps1            # ilk kurulumda: .\start.ps1 --migrate
```

Linux/macOS: `.venv/bin/python start.py`. Betik `.env` ayarlarını ve veritabanını
kontrol eder, backend'i başlatıp `/health` yanıt verene kadar bekler, sonra web
arayüzünü açar. `Ctrl+C` ikisini birlikte kapatır. Aşağıdaki adımlar süreçleri
elle başlatmak isteyenler içindir.

Dosya ve proje dışa aktarımında, özel işleyicisi bulunmayan ve içeriği metin olarak tanınamayan dosyalar
**desteklenmeyen içerik** olarak raporlanır. Bu karar proje adına veya `.bin`
uzantısına özel değildir; içerik kontrolüne dayanır. Dosyalar taranmaz,
çıktıya kopyalanmaz ve onay/serbest bırakma kuyruğuna eklenmez. Bu durum
uyarı değil **bilgi notudur**: sonuç tek başına bu yüzden "uyarılı" olmaz; arayüz
ve işlem kayıtları hangi dosyaların kapsam dışı kaldığını gösterir. Hariç tutma
kuralına takılan dosyalar, symlink'ler ve "bu dosya türü için parser yok" gibi
doğrulama kapsamı notları da aynı şekilde bilgi olarak raporlanır. Arşivler,
çözülemeyen kodlama ve boyut aşımı ise uyarı olmaya devam eder. Metin içeren bilinmeyen uzantılar mevcut tarama
kurallarına tabidir. Boyut, kodlama, sözdizimi ve geri dönüş doğrulaması
hataları teknik doğrulama hatası olarak engellenmeye devam eder.

Kaynak kodu güncelledikten sonra backend ve UI süreçlerini yeniden başlatın;
açık kalan backend eski kodla çalışmaya devam edebilir. Eski işlem raporları
geçmişi gösterir; yeni davranışı görmek için orijinal projeyi yeniden tarayın.

Geri dönüş doğrulaması, geri çözülen içeriğin orijinal kaynakla birebir
aynı olmasını esas alır. Kaynakta zaten bulunan ve yer tutucuya benzeyen
bir sabit/sayı, içerik aynen geri elde ediliyorsa eksik eşleme hatası sayılmaz.
Gerçekten yeni üretilen bir yer tutucunun eşlemesi eksik veya yanlışsa kontrol
başarısız olur. Geri alma işleminde bu ayrım yalnızca imzalı bütünlük kaydıyla
hem dosyanın değişmediği hem de kaynak içeriğin aynen elde edildiği
doğrulandığında yapılır; bu kanıt yoksa çözülemeyen yer tutucular raporlanır.

### Java `.class` dosyaları

Hariç tutma kurallarına takılmayan `.class` dosyaları özel Java sabit havuzu
işleyicisiyle taranır. String sabitleri, anotasyon metinleri ve kaynak dosya
adındaki tespit edilen hassas değerler maskelenir. `ConstantValue` alan adları
(`PASSWORD` gibi) tespit bağlamı olarak korunur. Çıktı yine ikili `.class`
dosyasıdır; metin dökümü değildir. İndirmeden önce gerçek eşlemelerle geri
oluşturulan dosyanın bayt özeti orijinalle karşılaştırılır; geri almada da
imzalı bayt bütünlüğü doğrulanır. Java/JDK kurulumu veya internet gerekmez;
kullanıcının sınıfı yüklenmez ya da çalıştırılmaz.

Kapsam: metin sabitleri. Sayısal sabitler, bytecode ile çalışma anında üretilen
metinler ve şifreli veriler bu metin taramasına dahil değildir. Sınıf/metot
adları, descriptor gibi yapısal alanları değiştirmeyi gerektiren bulgular
çıktıyı engeller. Bilinmeyen/custom attribute, `SourceDebugExtension`, bozuk
class veya desteklenmeyen sürüm de açıklamalı hata verir. Karantinaya alınan
class dosyası, inceleme kararı sonrası orijinal projeden yeniden taranmalıdır.
Proje seviyesinde çalışma davranışı garanti edilmez; kapsam uyarısı raporda
gösterilir. Java 21 ile derlenmiş örnekte JVM doğrulaması ve birebir geri
dönüş test edilmiştir. Format referansı: [JVMS bölüm 4](https://docs.oracle.com/javase/specs/jvms/se25/html/jvms-4.html).

Venv `masking_service\.venv` altındadır; `.venv\Scripts\python.exe`'yi tam
yolla çağırdığınız sürece `Activate.ps1`'e gerek yoktur (execution policy
sorunu varsa bu yöntem onu tamamen bypass eder). Aşağıdaki adımlar için
**iki ayrı PowerShell penceresi** açık tutmanız gerekir.

### 1) Terminal 1 - backend'i başlat

```powershell
# masking_service klasorune gec
cd C:\masking_system\masking_service
```

```powershell
# FastAPI backend'i 127.0.0.1:8001'de baslat - bu terminal acik/calisir kalmali
.venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001
```

`Uvicorn running on http://127.0.0.1:8001` satırını görene kadar bekleyin,
bu terminali kapatmayın.

### 2) Terminal 2 - web arayüzünü başlat

Yeni bir PowerShell penceresi açın:

```powershell
# masking_service klasorune gec (yeni pencere, venv henuz aktif degil)
cd C:\masking_system\masking_service
```

```powershell
# Streamlit web arayuzunu 8501 portunda baslat - bu terminal de acik kalmali
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Streamlit otomatik olarak tarayıcıda `http://localhost:8501` adresini açar;
açmazsa adresi elle girin.

### 3) Terminal 3 - doğrulama

Üçüncü bir PowerShell penceresinde (backend/UI'ı durdurmadan):

```powershell
# Backend ayakta mi diye kontrol et - status: ok DONMELI
Invoke-RestMethod http://127.0.0.1:8001/health
```

Tarayıcıda Streamlit'in gösterdiği adresi açın. Kimlik girişinden sonra sol
menüdeki ekranlar:

- **Dışarı Çıkar** - proje klasörünü/dosyasını maskeleyip dışa aktarır (CLI
  `export` ile aynı işlem, web arayüzünden).
- **Onay Bekleyenler** - Katman 2/LLM'in düşük güvenle işaretlediği veya
  denetim (audit) uyarısı üretilen bulguları insan onayına sunar.
- **Geri Al** - maskelenmiş bir çıktıyı `proje + sicil + branch` üçlüsüyle
  gerçek değerlere geri dönüştürür (CLI `unmask` ile aynı işlem).
- **Geçmiş İşlemler** - `masking_runs` tablosundaki önceki export/unmask
  koşularını ve bulgu özetlerini listeler.
- **Kurumsal Terim Sözlüğü** - kurum içi terim listesini (.xlsx) toplu
  yükleyip önizleme/onay sonrası filtre kuralı olarak kaydeder (XML
  entity-expansion sertleştirmesiyle güvenli ayrıştırma).

### 4) Durdurma

```powershell
# Terminal 1 (backend) ve Terminal 2 (Streamlit) penceresinde ayri ayri:
# Ctrl+C tuslayin ve surecin tamamen kapandigini "Terminate batch job (Y/N)?"
# sorusuna Y ile onaylayin (sorulmazsa zaten kapanmis demektir)
```

Her iki süreç de kapandıktan sonra tekrar başlatmak için 1) ve 2) adımlarını
yineleyin; venv/veritabanı kurulumunu tekrar yapmanıza gerek yoktur.

## 3. Sorun giderme

**"database is locked" hatası (özellikle export sırasında):**
- Sadece **tek bir** `uvicorn` süreci çalıştığından emin olun (`tasklist | findstr python`
  ile kontrol edin; birden fazla varsa hepsini kapatıp tek seferde yeniden başlatın).
- Kurulum klasörünü (`masking_system`) antivirüs gerçek-zamanlı taramasından ve
  OneDrive/kurumsal bulut senkronizasyonundan **hariç tutun** — dosya kilitleme
  çakışmasının en sık nedeni budur.
- `masking.db` dosyasını DB Browser/SQLite viewer gibi bir araçla açık bırakmayın.
- Uygulamayı flash bellek/USB üzerinden değil, yerel diskten çalıştırın (exFAT/FAT32
  gibi taşınabilir dosya sistemleri SQLite WAL modunun ihtiyaç duyduğu kilitlemeyi
  düzgün desteklemeyebilir).
- Yarım kalmış bir önceki denemeden sonra `masking_service/masking.db-wal` dosyası
  büyük kaldıysa: tüm python süreçlerini düzgünce kapatıp tekrar başlatın, SQLite
  açılışta WAL'ı otomatik checkpoint'ler.

**Backend ayakta ama her istek 503/hata veriyor:**
`/health` endpoint'i veritabanı şemasını kontrol etmez (sadece bağlantıyı test eder),
bu yüzden migration'ları unutsanız bile "ok" döner. `python -m alembic upgrade head`
adımını atladıysanız gerçek endpoint'ler (kurallar, export, review vb.) "no such
table" hatasıyla başarısız olur — yukarıdaki "Veritabanını oluştur" adımını çalıştırın.

**Kurulum sırasında paket bulunamıyor / sürüm hatası:**
Hedef makinenin Python sürümü/mimarisi (`python --version`), wheelhouse'un hazırlandığı
hedeften (3.14 / win_amd64) farklıdır. Doğru sürümü kurup tekrar deneyin.

## 4. Temel kullanım (CLI)

Her yeni dışa aktarımın kendine ait bir **JOB ID**'si (işlem numarası) vardır.
IP, e-posta, secret ve diğer metinsel placeholder sayaçları her işlemde
ayrı ayrı 1'den başlar. Aynı proje tekrar dışa aktarıldığında da yeni bir
işlem oluşur. Aynı değer bir işlem içindeki tüm dosyalarda aynı placeholder'ı
kullanır; eşlemeler `JOB ID + placeholder` ile seçilir.

Geri alma sırasında JOB ID, paketin `.masking-integrity.json` dosyasından
imzası doğrulanarak otomatik okunur. Bu dosyayı paketle birlikte koruyun.
Tek dosya geri alırken veya paket kaydı eksikse **Geri Al** ekranındaki
"Kaynak maskeleme işlem numarası (JOB ID)" alanını, API'de `job_id` alanını
veya CLI'da `--job-id 123` seçeneğini kullanın. Kimlik eksikse sistem bir
işlem tahmin etmez. Eski sürüm çıktıları için geçmiş eşlemeler korunur.

Bu sürüme geçerken uygulamayı durdurun, veritabanını yedekleyin ve
`masking_service` klasöründe `.venv\Scripts\python.exe -m alembic upgrade head`
komutunu çalıştırıp uygulamayı yeniden başlatın (Linux: `.venv/bin/python`).

Kurumsal ifade yüklerken girilen **Başlık / Proje Adı**, yalnızca uygulama içindeki
gruplama için kullanılır. Bu ifadeler çıktıda `mask_kurumsal_ifade_<sayı>` olarak
maskelenir; başlık placeholder'a eklenmez. Önceden kayıtlı kurallar da yeni
dışa aktarımlarda bu genel adı kullanır. Eski placeholder eşlemeleri geçmiş
dosyaları geri alabilmek için korunur; önceden üretilmiş çıktıları düzeltmek
için orijinal kaynaktan yeniden dışa aktarım yapılmalıdır.

Aşağıdaki komutlar `masking_service` içinde, venv aktifken (`.venv\Scripts\Activate.ps1`)
ya da doğrudan `.venv\Scripts\python.exe -m app.cli ...` şeklinde çalıştırılır:

```powershell
# maskele + dışarı aktar
python -m app.cli export --kaynak .\proje --hedef D:\proje-masked `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# geri dönüştür
python -m app.cli unmask --kaynak D:\proje-masked --hedef .\proje-geri `
    --proje Poseidon --sicil EMP-1001 --branch feature/x

# yeni filtre kuralı ekle (kod değişikliği/deploy gerekmez)
python -m app.cli kural-ekle --tip tc_kimlik_no --pattern '\b\d{11}\b' `
    --placeholder-format 'TC_TEST_{sayac}' --olusturan EMP-1001

# kuralları listele / aktif-pasif et (silmez, geçmiş eşlemeler bozulmaz)
python -m app.cli kural-listele --sadece-aktif
python -m app.cli kural-aktif tc_kimlik_no
python -m app.cli kural-pasif tc_kimlik_no

# bir projenin en son ne zaman export/unmask edildiğini sorgula
python -m app.cli rapor-son-islem --proje Poseidon

# filtrelere uyan tüm export/unmask geçmişini listele
python -m app.cli rapor-gecmis --proje Poseidon --limit 20

# bir run_id'ye ait dosya bazlı audit log detayını göster
python -m app.cli rapor-detay --run-id 7

# uygulama export/unmask sırasında durdurulduysa yarım kalan hedef yazımını kurtar
python -m app.cli recover-output --hedef D:\proje-masked
```
