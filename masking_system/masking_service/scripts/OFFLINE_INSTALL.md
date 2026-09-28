# Windows çevrimdışı kurulum paketi

Hedef, `manifest.json` içinde belirtilir. Bu paket Python **3.14 / Windows x64**
için hazırlanmıştır. Bilgisayarda bu Python kurulmuş olmalıdır; ARM64 veya
başka Python sürümü için wheelhouse yeniden hazırlanmalıdır. Parser'lar ayrıca
Node.js, Java veya C++ derleyicisi istemez. Kurulum/çalışma sırasında internet
indirmesi yapılmaz. vLLM varsa yalnızca kurum içi endpoint'e bağlanır.

## Flash bellekten kurulum

1. Paketi yazılabilir bir yerel klasöre çıkarın. PowerShell'i bu klasörde açın.
2. Çalıştırın:

   ```powershell
   .\install_offline.ps1
   ```

   Kurumunuzun PowerShell çalıştırma politikası izin vermiyorsa politikayı
   değiştirmeden kurumun onaylı script çalıştırma yöntemini kullanın.

3. Script Python/mimariyi ve wheel SHA-256 özetlerini kontrol eder, sanal ortamı
   oluşturur, `--no-index --require-hashes` ile kurar, `pip check` ve ağ
   bağlantıları engellenmiş parser testini çalıştırır.
4. `source/.env.example` dosyasını `source/.env` olarak kopyalayıp kurum
   ayarlarını ve şifreleme anahtarını doldurun. Mevcut kurulumu güncelliyorsanız
   **mevcut şifreleme anahtarını ve veritabanı yolunu koruyun**.
5. `source/masking_service` altında:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head
   .\.venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001
   ```

   Ayrı terminalde:

   ```powershell
   .\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
   ```

Mevcut kaynak dizinine kurulacaksa script'in `-ServicePath` ve `-VenvPath`
parametrelerini kullanın; o dizinde güncellenmiş kaynak kod bulunmalıdır.
`--validation-only` ile oluşturulan küçük paket yalnız parser ekidir; onunla
yeni bir uygulama ortamı kurmak yerine mevcut uygulamanın sanal ortamını seçin.

## Kapsam ve doğrulama

- TS/TSX/JS/JSX: Tree-sitter ile yalnız sözdizimi. Import, tip kontrolü ve
  kaynak çalıştırma yoktur.
- SQL: SQLGlot. `VALIDATION_SQL_DIALECT=tsql`, `postgres`, `oracle`, `mysql`
  vb. seçilebilir. Boş değer ortak gramerdir. Desteklenmeyen kaynak lehçesi veya
  eksik parser eski denge kontrolüne döner ve raporda açık uyarı oluşturur.
- XML: dış entity/DTD çözümlemeden parse edilir.
- JSON: kaynak geçerliyse member sırası üzerinden tür ve yapı karşılaştırılır;
  anahtarların maskelenmesi desteklenir. Sayı→string değişimi engellenir.
- Diğer formatların mevcut structural/bracket kontrolleri korunur.
- Tutarlılık geçişinin sonunda tüm gerçek mapping'lerle geri çözüm yapılır;
  sonuç özgün metnin SHA-256 özeti ve karakter sayısıyla doğrulanır.

Kaynaklar, bağımlılıklar, spaCy modeli, sürüm kilidi, wheel özetleri ve testler
pakete dahildir. Gerçek `.env`, veritabanı ve yüklenmiş kullanıcı dosyaları
dahil değildir. `manifest.json` wheel bütünlüğünü denetler; bir dijital imza değildir.

Linux'ta temiz ortamda çevrimdışı parser kurulumu ve testleri çalıştırılmıştır.
Windows wheel etiketleri, Python gereksinimleri ve Windows'a özgü transitif
bağımlılık kapanışı kontrol edilir; Windows'ta yerel çalışma testini kurulum
script'i yapar. Bu iki kontrolün kapsamı aynı değildir.
