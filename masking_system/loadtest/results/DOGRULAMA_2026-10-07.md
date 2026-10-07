# Başlangıç kilidi, iş kuyruğu ve çıktı doğrulaması

Tarih: 7 Ekim 2026. Mevcut kaynak üzerinde yapılan düzeltmelerin doğrulama notu.

Devam çalışması: Bu notta açık kalan yoğun SQL sorunu daha sonra giderildi;
üç gerçek model tekrarı ve hız değerlendirmesi
[SQL doğrulama notunda](DOGRULAMA_SQL_2026-10-07.md) yer alıyor.
Aşağıdaki sonuçlar ilk aşamanın tarihsel ölçümleridir.

## Değişiklikler

- Çalışma kaydı kurtarma günlüğü oluşturulduktan sonra kaydediliyor. Pahalı detector/model kurulumu açık DB transaction'ı dışında yapılıyor.
- Kurulum hatasında eski çıktı korunuyor; çalışma başarısız olarak kapatılıyor. Hata DB'ye yazılamazsa kurtarma günlüğü saklanıyor.
- Arka plan export uçlarında süreç başına varsayılan 2 çalışan + 16 bekleyen iş sınırı var. Bekleyen iş “Sıradasınız” olarak gösteriliyor; kapasite aşımı HTTP 429 ve Retry-After ile reddediliyor.
- Eksik çıktının dosya sayısı arayüzde açıkça gösteriliyor.
- Yük testi ZIP/manifest iş kimliğini, dosya listesini, dosya sayısını ve SHA-256 özetlerini kontrol ediyor. Bozuk paket başarısız, eksik paket ayrı çıktı sınıfı olarak kaydediliyor.

## Test sonuçları

65 farklı otomatik regresyon testi geçti (63 testlik grup ve eklenen 2 test dahil ilgili 16 testlik tekrar). Test DB'si canlı veritabanının ayrı kopyasıydı. Asyncio kapanışı sandbox içinde takıldığı için ilgili testler aynı ayrı DB ile sandbox dışında çalıştırıldı. Uygulama kodundaki bir hata olarak raporlanmadı.

| Gerçek model senaryosu | İş | Eksiksiz çıktı | Eksik çıktı | İş hatası | Geçersiz ZIP/manifest |
|---|---:|---:|---:|---:|---:|
| Aynı anda 10 küçük proje | 10 | 10 | 0 | 0 | 0 |
| Yoğun veri içeren 1 orta proje | 1 | 0 | 1 | 0 | 0 |

10 küçük işte 60/60 dosya çıktıya alındı. DB kilit hatası 0; kayıt izolasyonu kontrolü geçti. Ölçülen en yüksek aktif iş sayısı 2. Ortanca uçtan uca süre 231.4 sn, en uzun süre 382.8 sn; işlem kapasitesi 1.56 proje/dk. Süreler kuyruk beklemesini içerir. 10 örnekten güvenilir bir p95/SLA sonucu çıkarılmadı.

Orta projede 10 dosyanın 9'u çıktıdaydı; 1 dosya teknik nedenle bloke edildi. İş tamamlanmış olsa da eksiksiz başarı sayılmadı. Dosyanın bloklanmasına neden olan model yanıt sınırı bu değişiklikte giderilmedi.

## Kaynaklar ve sınırlar

- `startup_queue_verified/R_startup_u10/rep1/`: sabit kaynakla temiz 10 kullanıcı koşusu; `startup_queue_verified/summary_conditions.csv` özet ölçümleri.
- `startup_queue_regression/R_dense_u01/rep1/`: yeni backend ile çalışmış orta proje kontrolü.
- `startup_queue_regression/R_startup_u10/rep1/` karşılaştırmadan çıkarıldı: test sırasında kaynak düzenlendiği için 3 iş BuildMismatchError aldı. Bu başarısız deneme silinmedi; VALIDATION_NOTE.md içinde işaretlendi.
- Son kaynak SHA-256: `050cb32f7a1c55912b13046eb6cdc98cbf86bd3ac194f2a4ec7fb500a6b00e48`. Temiz koşu boyunca değişmedi.
- Yerel model ve güvenlik kontrolleri açık kaldı. Model sunucusunun paralelliği değiştirilmedi; hız artışı iddia edilmiyor.
- Her gerçek model koşulu 1 tekrar ve sentetik veri kullandı. Sonuç, büyük gerçek projelerde uzun süreli üretim kabulü değildir.
- ZIP kontrolleri maskeleme recall'ını veya manifest imzasının doğruluğunu kanıtlamaz.

## Uygulama ve sonraki adım

Normal backend yeniden başlatılınca yeni kod ve varsayılan kuyruk sınırları etkinleşir. Bu çalışmada yalnız ayrı test backend'leri başlatıldı; canlı model servisi yeniden yapılandırılmadı. Kuyruk süreç belleğindedir; yeniden başlatmada otomatik devam ve çok-worker yönlendirmesi kapsam dışındadır. Eski senkron export uçları ile CLI bu arka plan kuyruğunu kullanmaz.

Sonraki performans adımı, model sunucusunda gerçek paralel işlemeyi ayrı bir deneyde doğrulamaktır. Uygulamanın LLM eşzamanlılık ayarını tek başına artırmak için bu test kanıt sağlamaz. Yoğun SQL dosyasının teknik blok nedeni de maskeleme doğruluğu korunarak ayrıca ele alınmalıdır.
