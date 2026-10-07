# Yoğun SQL dosyasının eksik kalması: düzeltme ve doğrulama

7 Ekim 2026 — Yerel kaynak ve gerçek Ollama modeliyle doğrulandı.

## Sonuç

Önceki kontrol koşusunda orta projenin 10 dosyasından yalnızca 9'u çıktıya
alınıyordu. Düzeltmeden sonra üç ayrı tekrarda da 10/10 dosya üretildi.
Her tekrarda sentetik SQL kaynağındaki 75 farklı hassas değerin çıktı
dosyalarında açık kalmadığı ayrıca kontrol edildi.

| Tekrar | Eksiksiz çıktı | Teknik bloke | Açık kalan bilinen SQL değeri | Uçtan uca süre |
|---|---:|---:|---:|---:|
| 1 | 10/10 | 0 | 0/75 | 141,3 sn |
| 2 | 10/10 | 0 | 0/75 | 138,7 sn |
| 3 | 10/10 | 0 | 0/75 | 137,1 sn |

Toplam 30/30 dosya hazır; ortalama 139,1 sn, ortanca 138,7 sn. ZIP/manifest
iş kimliği, dosya listesi, dosya sayısı ve SHA-256 kontrolleri geçti. Kayıt
izolasyonu üç tekrarda da başarılı. Her tekrarın 6 dosyalık ısınma işi bu
tablodan çıkarıldı. Güvenlik karantinası ve inceleme bekleyen dosya sayısı da 0.

## Sorun ve değişiklik

`db/seed_9.sql` kısa olmasına rağmen çok sayıda hassas alan içeriyordu.
Modelin 2048 token yanıt sınırı yetmiyordu. Mevcut yeniden bölme işlemi,
800 karakterlik alt sınır yüzünden yaklaşık 1200 karakterlik bir alt
parçayı yeniden bölemiyor, dosyayı teknik nedenle bloke ediyordu.

`llm_recognizer.py` içinde alt sınır 200 karaktere indirildi. Tespit ve son
denetim aynı bölme fonksiyonunu kullanıyor. En fazla üç bölme seviyesi
korundu: başlangıç parçası başına en fazla 15 tarama denemesi; mevcut geçici
bağlantı hatası yeniden denemeleri bu sayıya dahil değildir. Bir alt parça
bile tamamlanamazsa dosyanın tamamı bloke edilir. Yanıt sınırı, model,
maskeleme kuralları ve güvenlik denetimleri değiştirilmedi.

Her gerçek tekrarda iki `LLMTruncatedError` görüldü; daha küçük parçalarla
yeniden tarama başarılı oldu. Bunlar kurtarılan model yanıtlarıdır;
sonuçta teknik olarak bloke edilen dosya yoktur.

Yük testi de güçlendirildi: tanınan sentetik SQL şablonundaki ad, kimlik,
e-posta, telefon ve IBAN değerlerinden biri eksiksiz çıktıda açık kalırsa
iş artık `invalid_output` sayılır. Kontrol edilen farklı değer sayısı
`sql_sensitive_values_checked` alanına kaydedilir; ham değerler loglanmaz.

## Otomatik testler

Bu aşamada 142 farklı test geçti:

- LLM parçalaması, yanıt doğrulaması, bozuk bulgu onarımı ve son denetim: 84.
- ZIP/manifest ve sentetik SQL çıktı kontrolleri: 8.
- Export hata yönetimi, eşzamanlılık, LLM girdi verimliliği ve satır tekilleştirme: 50.

Yeni regresyonlar, kısa ve yoğun girdinin yeniden bölünmesini, tüm bulgu
konumlarının korunmasını, son denetimin baştaki ve sondaki bulguları
görmesini ve açık kalan sentetik SQL değerinin reddedilmesini kapsıyor.
Önceki aşamanın 65 testiyle örtüşme olabileceğinden sayılar toplanmamalıdır.

## Hız değerlendirmesi

Önceki 121,4 saniyelik orta proje koşusu eksik çıktı üretiyordu. Yeni
137–141 saniyelik süreler bütün dosyaların işlenmesini içeriyor; bunlar
hız artışı kanıtı veya eşdeğer iş yükünde gerileme ölçümü değildir.

Yerel Ollama sürümü `0.35.1`, model `qwen3.6:35b`, mimari `qwen35moe`.
Bu sürümün sunucu kodu bu mimari için paralelliği 1'e indiriyor.
Dolayısıyla `OLLAMA_NUM_PARALLEL` veya uygulama eşzamanlılık ayarını tek
başına artırmak gerçek paralel model çalışması sağlamaz.
[Ollama v0.35.1 kaynak kodu](https://github.com/ollama/ollama/blob/v0.35.1/server/sched.go#L475-L486).

Ölçülen uygulama tespit bileşeni kurulumu 1,37–1,54 saniye; toplam yaklaşık
140 saniyenin küçük bir kısmı. Büyük hız kazanımı için öncelik model
servisinin kapasitesidir. Ayrı bir sunucu/model deneyi yapılacaksa aynı
veri, eksiksiz çıktı ve hassas değer kontrolleriyle karşılaştırılmalıdır.
Bu çalışmada model sunucusu değiştirilmedi.

## Kanıt ve tekrar çalıştırma

- Ham kayıtlar: `dense_split_verified/R_dense_u01/rep1/`, `rep2/`, `rep3/`.
- Özet: `dense_split_verified/summary_conditions.json` ve `summary_reps.csv`.
- Önceki eksik çıktı: `startup_queue_regression/R_dense_u01/rep1/jobs.jsonl`.
- Test boyunca uygulama kaynakları sabit kaldı. SHA-256:
  `268aa99a8731e6d0ecf560f03766005a56f484af1d9f9dd891f929f4884db1f7`.
  Hesap: sıralı `masking_service/app/**/*.py` dosyalarının `masking_system`
  köküne göre yolları ve içerikleri, sırayla ve ayraçsız SHA-256'ya eklenir.

`masking_system` dizininden, yeni bir kampanya adı kullanarak:

```bash
loadtest/.venv/bin/python -m loadtest.runner --plan regression --only R_dense_u01 --reps 3 --campaign loadtest/results/sql_yeni_dogrulama
loadtest/.venv/bin/python -m loadtest.analyze loadtest/results/sql_yeni_dogrulama
```

Testler canlı DB'nin ayrı kopyasını ve ayrı backend süreçlerini kullandı.
Normal backend yeni kodu yeniden başlatıldığında alır. Bu üç sentetik koşu
genel maskeleme recall'ını, SQL'in çalıştırılabilirliğini, büyük gerçek
projeleri veya uzun süreli çok kullanıcılı kapasiteyi kanıtlamaz.
