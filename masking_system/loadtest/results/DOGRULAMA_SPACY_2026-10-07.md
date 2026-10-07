# spaCy modelinin işler arasında paylaşılması: doğrulama

7 Ekim 2026. Yerel kaynak ve gerçek Ollama modeliyle doğrulandı.

## Sonuç

Her export işi spaCy modelini (`en_core_web_lg`) baştan yüklüyordu. Model artık
süreç başına bir kez yükleniyor; eşzamanlı 10 işte backend belleği 7001 MiB'dan
1212 MiB'a düştü. Çıktı, hata ve izolasyon sonuçları değişmedi. Bu makinede hız
değişmedi; süreyi Ollama'nın tek model slotu belirliyor.

| Ölçüm (10 eşzamanlı küçük iş) | Önce | Sonra |
|---|---:|---:|
| Backend RSS maks. | 7001 MiB | 1212 MiB |
| Sistem RAM kullanımı maks. | 24,3 GiB | 18,6 GiB |
| Tespit bileşeni kurulumu ort. / maks. | 4,29 / 6,50 sn | 0,06 / 0,09 sn |
| İş başlangıç beklemesi ort. / maks. | 7,8 / 15,2 sn | 2,3 / 2,5 sn |
| Eksiksiz çıktı / iş hatası / izolasyon | 10/10, 0, OK | 10/10, 0, OK |
| Kapasite | 1,60 proje/dk | 1,68 proje/dk |
| Uçtan uca p50 / maks. | 357 / 375 sn | 349 / 357 sn |

Kapasite ve uçtan uca süre farkları tek tekrarlık ölçümün oynama aralığındadır;
hız artışı iddia edilmiyor.

## Değişiklik

- `presidio_detector.py`: spaCy NLP motoru (dil, model) anahtarıyla süreç başına
  bir kez yüklenir ve paylaşılır. Her iş kendi `RecognizerRegistry` ve
  `AnalyzerEngine` nesnesini kurmaya devam eder: DB kuralları, izin listesi,
  dosya türü kısıtlamaları ve kapatılan kategoriler işe özeldir. Yükleme hatası
  önbelleğe alınmaz.
- spaCy `Language` eşzamanlı çağrı için güvenli olmadığından kilit motorla
  birlikte paylaşılır; tüm işlerin Presidio analizi süreç içinde sıralanır.
- Analizler `nlp.memory_zone()` içinde çalışır. Ön denemede 20 analiz, bölge
  olmadan modelin sözlüğüne 2391 kalıcı kayıt ekledi, bölge içinde 0 ekledi.
- `instrumented_server.py`: `_analyze_serialized` kopyası yeni gövdeyle
  (`memory_zone`) uyumlu hale getirildi.

## Yan etki: ortak Presidio kilidi

İş başına Presidio kilit beklemesi ortalama 30 sn'den 41 sn'ye çıktı. Dosya başına
toplam Presidio süresi aynı kaldı (7,0 → 7,2 sn). Değişen, sürenin hesaplama ve
bekleme arasındaki dağılımı: önce ~2 sn hesaplama + ~5 sn bekleme, sonra ~0,3 sn
hesaplama + ~6,9 sn bekleme. Bu makinede bekleme LLM kuyruğunun içinde kalıyor.

Intranet'te (vLLM, paralel model) ortak kilit tavan oluşturabilir. Kaba tahmin,
süreç başına dakikada ~200 dosyadır; vLLM ölçümünde `presidio_lock_wait`
izlenmelidir. Gerekirse süreç başına küçük bir motor havuzu ya da birden fazla
backend süreci kullanılabilir.

## Testler

Tam test paketi: 1662 geçti, 3 atlandı, 4 xfail. Yeni testler
(`tests/test_presidio_shared_nlp.py`) motor ve kilit paylaşımını, hatalı
yüklemenin önbelleğe alınmamasını ve paylaşılan modelin sözlüğünün analizler
boyunca büyümemesini kontrol eder.

## Kanıt ve tekrar çalıştırma

- Önce: `spacy_paylasim_once/` (commit `27fd4b4`, değişiklik öncesi kaynak).
- Sonra: `spacy_paylasim_sonra/`.
- İki koşu da `EXPORT_JOBS_MAX_RUNNING=10` ile çalıştı (varsayılan 2). Bu ayar
  `environment.json`'a yazılmıyor.

`masking_system` dizininden, yeni bir kampanya adıyla:

```bash
EXPORT_JOBS_MAX_RUNNING=10 loadtest/.venv/bin/python -m loadtest.runner --plan regression --only R_startup_u10 --campaign loadtest/results/<yeni_ad>
loadtest/.venv/bin/python -m loadtest.analyze loadtest/results/<yeni_ad>
```

Her koşul tek tekrar ve sentetik veridir. Uzun süreli bellek davranışı ve
intranet kapasitesi bu ölçümle kanıtlanmaz.
