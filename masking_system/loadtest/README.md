# Yük testi ve düzeltme doğrulaması

Eski `results/main` ve `results/workers` kampanyaları başlangıç ölçümüdür;
yeni kodla üzerine yazılmaz. `regression` planı 10 küçük projenin aynı anda
gönderilmesini ve yoğun veri içeren tek orta projeyi çalıştırır. Her koşulda
bir ısınma işi ölçüm dışında tutulur. Canlı DB'nin SQLite backup kopyası ve
ayrı test backend'i kullanılır; yerel model sunucusu ortaktır.

`masking_system` klasöründe:

```bash
loadtest/.venv/bin/python -m loadtest.runner --plan regression --campaign loadtest/results/yeni_dogrulama
loadtest/.venv/bin/python -m loadtest.analyze loadtest/results/yeni_dogrulama
```

Test boyunca `masking_service/app` içeriği değiştirilmemelidir. Backend'in
sürüm kontrolü bu durumda işleri `BuildMismatchError` ile durdurur. Böyle bir
koşu performans karşılaştırmasına alınmaz; yeni kampanya dizininde tekrarlanır.
Modeli kullanan başka trafik varsa karşılaştırma ayrıca etkilenebilir.

## Başarı ve çıktı ayrımı

- `completed`: İş ve indirme ölçüm penceresi içinde bitti; eksiksiz çıktı anlamına gelmez.
- `completed_after_window`: İş bitti, ancak ölçüm penceresinden sonra.
- `complete_outputs`: Pencere içinde biten, ZIP/manifest dosya listesi, iş kimliği,
  dosya sayısı ve SHA-256 özetleri doğrulanan eksiksiz çıktılar.
- `partial_outputs`: Aynı kontrolleri geçen ama `complete=false` olan çıktılar.
- `invalid_outputs`: ZIP, manifest, iş kimliği veya dosya bütünlüğü doğrulaması başarısız.
- `unverified_outputs`: Yeni çıktı kontrolü yapılmadan kaydedilmiş eski tamamlanmalar.

Eksiksiz çıktılarda, yük testi üreticisinin bilinen sentetik SQL kayıtları
ayrıca kontrol edilir. Kaynaktaki ad, kimlik, e-posta, telefon ve IBAN
değerlerinden biri çıktıdaki herhangi bir dosyada açık kalmışsa sonuç
`invalid_output` olur. `sql_sensitive_values_checked` kontrol edilen farklı
değer sayısını kaydeder; ham değerler test kaydına yazılmaz. Bu kontrol
yalnızca tanınan sentetik SQL şablonunu kapsar ve eksik çıktıya uygulanmaz.

Bu kontroller maskelemenin hassas verilerin tamamını bulduğunu veya manifest
imzasının geçerli olduğunu kanıtlamaz; bunlar ayrı doğruluk/güvenlik testleridir.

Arka plan iş sınırları varsayılan olarak backend süreci başına 2 çalışan +
16 bekleyen iştir. Değişiklikler yeniden başlatılmış test backend'inde ölçülür.
Bu kısa regresyon, uzun süreli yük ve gerçek proje ölçeğinde kabul testinin
yerine geçmez. Model sunucusunun paralelliği bu planda değiştirilmez.
