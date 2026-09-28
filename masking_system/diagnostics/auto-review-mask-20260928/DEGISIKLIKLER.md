# Onay ekranında otomatik maskeleme

Düzenle seçeneği kullanıcıdan metin istemez. Dosya seçildiğinde sistem o dosyadaki bekleyen riskli ifadeleri maskeler, geri alma eşlemelerini mevcut veritabanına kaydeder ve dosyayı yeniden doğrular. Başarılı dosya çıktı klasörüne alınır. Normal bulgu listesinde Tümünü Gizle ve Yanlış Alarm seçenekleri korunur. Güvenlik karantinasında da birebir doğrulanabilen bulgular için Düzenle eylemi vardır.

Maskeleme mevcut placeholder, JSON sayısal değer, sözdizimi ve birebir geri dönüş kurallarını kullanır. .env atamalarında anahtarlar, tırnaklar ve satır sonları korunur. Modelin belirttiği ifade dosyada yoksa veya teknik doğrulama hatası varsa dosya otomatik serbest bırakılmaz. Son AI denetimi hâlâ risk bildirirse dosya çıktıya eklenmez ve kullanıcıya açıklama gösterilir.

Tarama anında indirilen ZIP önbelleği onay/düzenleme kararlarından sonra geçersizleştirilir. Dışa Aktarım ekranına dönüldüğünde güncel paket alınır. Yenileme başarısız olursa eski ZIP sunulmaz. Geçmiş ekranında yeniden doğrulanarak yayımlanan dosyalar yanlış alarm diye etiketlenmez.

Yerel ve flash uygulama kaynaklarıyla 65 test geçti. Testler izole geçici veritabanı ve sahte LLM yanıtlarıyla Linux/Python 3.13 üzerinde çalıştırıldı; gerçek intranet vLLM davranışı burada test edilmedi. Flash'a test klasörü eklenmedi.

İntranete aşağıdaki dosyaları aynı konumlarına birlikte kopyalayıp backend ve arayüzü yeniden başlatın. Yeni migration/bağımlılık yoktur. .env ve mevcut veritabanını koruyun. Daha önce teknik hata durumuna geçmiş dosyalar yeniden taranmalıdır.

- `masking_service/app/services/review_masking.py`
- `masking_service/app/services/review_service.py`
- `masking_service/app/services/audit_warning_service.py`
- `masking_service/app/services/audit_warning_details.py`
- `masking_service/app/api/schemas.py`
- `masking_service/app/api/routers/review.py`
- `masking_service/app/api/routers/audit_warnings.py`
- `masking_service/app/webapp/api_client.py`
- `masking_service/app/webapp/review_page.py`
- `masking_service/app/webapp/export_page.py`
- `masking_service/app/webapp/history_page.py`

Yedek: `.update_backups/before-auto-review-mask-20260928T111831Z.zip`
