# Intranet ölçüm kontrol listesi

Amaç: gerçek Qwen/vLLM ile gerçek projede karantina nedenlerinin dağılımını (`failed_check`)
ölçmek ve Faz 1 ayarlarının etkisini görmek. Faz 2b ve Faz 3'ün sırası bu verilere göre
belirlenecek.

Komutlar PowerShell'dir ve `C:\masking\masking_service` klasöründe çalıştırılır. Kendi
kurulum yolunuz farklıysa uyarlayın.

**Asla paylaşmayın:** `.env`, `masking.db`, maskeli çıktı klasörü, kaynak dosyalar, backend
logunun ham satırları. Faz 2a'dan itibaren loglar ve export raporu kaynak yol yerine
`maskeli/yol#<12 hex>` yazar (kural 7). Ancak maskeli yolda LLM kaynaklı terimler Faz 3'e kadar açık
kalabilir. Bu yüzden loglardan yine yalnızca aşağıda belirtilen **sayıları** getirin. Bir dosya
kimliğinin hangi kaynak dosya olduğunu yalnızca sunucuda görebilirsiniz:
`.venv\Scripts\python.exe -m app.cli dosya-kimligi --run-id <N> --id <12 hex>`.

Aşağıdaki "Getirin" maddeleri bu çalışmayı yürüten oturuma geri getirilecek çıktılardır.

---

## Adım 0 — `main`'den damgalı dağıtım ve preflight

Faz 2a PR'ı `main`'e merge edildikten sonra yapılır.

1. **Paketi git'in olduğu makinede hazırlayın** (flash'a kopyalamadan önce):
   ```powershell
   git fetch origin main
   git checkout --detach origin/main
   git rev-parse --short=12 HEAD
   cd masking_system\masking_service
   .venv\Scripts\python.exe scripts\write_build_stamp.py
   ```
   - Son komut `app\BUILD_STAMP.json` dosyasını yazar: commit + `app\` dosyalarının özetleri.
   - Çıktı `stamp=... commit=<12 hex> tree=<12 hex> files=N` biçimindedir.
   - `commit` değeri `-dirty` ile bitiyorsa çalışma kopyasında commit'lenmemiş değişiklik vardır;
     temiz bir checkout'tan yeniden üretin.
   - `scripts\build_offline_bundle.py` kullanıyorsanız damga otomatik üretilir.
2. Bu commit'ten şunları **eksiksiz** kopyalayın. Seçili dosyalar değil, klasörlerin tamamı;
   `app\BUILD_STAMP.json` dahil:
   - `masking_service\app\`
   - `masking_service\alembic\`
   - `masking_service\scripts\`
   - `masking_service\tests\fixtures\golden\` (yalnızca ölçüm için)

   Intranette eski `app\` klasörünün üzerine kopyalıyorsanız, damgada olmayan eski dosyalar
   preflight'ta `WARN ... reason=damgada_yok` olarak görünür. Bu engellemez, ama mümkünse eski
   `app\` klasörünü silip temiz kopyalayın.
3. Veritabanını yedekleyin ve migrasyonu uygulayın:
   ```powershell
   Copy-Item ..\masking.db ..\masking.db.yedek-olcum
   .venv\Scripts\python.exe -m alembic upgrade head
   ```
   Faz 2a yeni migrasyon eklemedi. Faz 0'ın kolon migrasyonu henüz uygulanmadıysa bu komut onu
   uygular.
4. Backend ve arayüzü kapatıp yeniden başlatın. Backend logunu dosyaya alın:
   ```powershell
   .venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001 *>&1 | Tee-Object -FilePath ..\backend-olcum.log
   ```
   Açılışta logda `build state=... commit=...` satırı görünür.
   - `state=ok`: damga uyuşuyor.
   - `state=no_stamp`: damga kopyalanmamış. Yalnızca uyarıdır, ama bu ölçüm için 1. adıma dönün.
   - `state=mismatch`: backend açılır ama dışa aktarma 503 ile reddedilir; ayrıntı logdaki
     `build_mismatch commit=... files=...` satırındadır. `app\` klasörünü aynı paketten yeniden
     kopyalayıp backend'i yeniden başlatın.
   - Kod kopyalanıp backend yeniden başlatılmadıysa da dışa aktarma reddedilir
     (`export_refused reason=code_changed_since_start`).
5. Preflight ve health:
   ```powershell
   .venv\Scripts\python.exe scripts\check_llm_preflight.py
   if ($LASTEXITCODE -ne 0) { Write-Host "PREFLIGHT BASARISIZ" }
   Invoke-RestMethod http://127.0.0.1:8001/health | ConvertTo-Json
   ```
   Preflight önce sürüm tutarlılığını kontrol eder (app modüllerini import etmeden):
   - `build state=... commit=...` ve `PASS|FAIL stage=build_stamp`
   - `PASS|FAIL stage=signature_consistency checked_calls=N`: modüller arası çağrı/imza uyumu.
     Uyumsuzlukta `FAIL stage=signature_consistency caller=app/...:satir callee=... reason=...`
     yazar. TypeError olayının deseni budur.

   `/health` yalnızca `{"status": "ok"}` ya da `{"status": "degraded"}` döner.
6. Sicil kuralının kategorisi (salt okunur; bkz. "Bilinen sorun" notu aşağıda):
   ```powershell
   .venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:../masking.db?mode=ro', uri=True); print(c.execute(\"SELECT kural_adi, kategori, aktif_mi FROM filtre_kurallari WHERE desen_tipi='parametric'\").fetchall())"
   ```
   DB yolunuz farklıysa `.env`'deki `DB_PATH`'i kullanın.

**Getirin:**
- [ ] 1. adımdaki `git rev-parse` çıktısı ve `write_build_stamp.py` satırı (`commit=... tree=...`)
- [ ] Backend logundaki açılış satırı: `build state=... commit=... tree=...`
- [ ] `check_llm_preflight.py` çıktısındaki tüm `PASS`/`FAIL`/`WARN` satırları ve `RESULT=...`.
      `FAIL` varsa `FAIL ... frames=` ya da `FAIL stage=signature_consistency ...` satırı.
      Kurulum yollarını gizleyebilirsiniz.
- [ ] `/health` yanıtı
- [ ] 6. adımdaki sorgunun çıktısı (yalnızca kural adı/kategori; değer içermez)
- [ ] `python --version` çıktısı

`build state=ok` ve `RESULT=OFFLINE_OK` gelmeden sonraki adıma geçmeyin.

**Bilinen sorun (Faz 2a'da bulundu, henüz düzeltilmedi):** alembic seed verisi sicil kuralını
`personnel_no` kategorisiyle oluşturuyor, export ise değeri `sicil_no` anahtarıyla veriyor. Böyle
bir DB'de sicil değeri içerikte ve yolda maskelenmez. 6. adımın çıktısı, gerçek DB'nin bundan
etkilenip etkilenmediğini gösterecek.

---

## Adım 1 — Mevcut `.env` ile ölçüm (ayar değişikliği YOK)

1. 3/4 sonucunu veren gerçek projede normal export'u çalıştırın (web arayüzü veya CLI):
   ```powershell
   .venv\Scripts\python.exe -m app.cli export --kaynak <proje> --hedef <cikti> --proje <ad> --sicil <sicil> --branch <branch>
   ```
2. Karantina nedeni dağılımı (DB salt okunur açılır; yol, değer ya da gerekçe yazdırmaz):
   ```powershell
   .venv\Scripts\python.exe scripts\failed_check_summary.py --son --proje <ad>
   ```
3. Backend logundan yalnızca sayılar:
   ```powershell
   (Select-String -Path ..\backend-olcum.log -Pattern "detector_crash").Count
   (Select-String -Path ..\backend-olcum.log -Pattern "finish_reason='length'").Count
   (Select-String -Path ..\backend-olcum.log -Pattern "llm_retry").Count
   (Select-String -Path ..\backend-olcum.log -Pattern "llm_request .*status=timeout").Count
   ```
4. Altın kümede gerçek model baseline'ı (sentetik veri; geçici DB kullanır, `.env`'den yalnızca
   `VLLM_*` okunur):
   ```powershell
   .venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-adim1 --name once
   ```

**Getirin:**
- [ ] `failed_check_summary.py` çıktısının tamamı
- [ ] Export raporundaki `Durumlar`, `Ciktiya alinmama nedenleri` ve `LLM kullanimi` satırları.
      Altlarındaki dosya listelerini getirmeyin. Varsa `Yol/icerik uyusmazligi` satırındaki iki sayı
      (dosya ve terim; kimlikleri getirmeyin). `failed_check_summary.py` de bu sayıları yazar.
- [ ] Log sayıları (4 sayı)
- [ ] `golden-real-adim1\once.json` ve `once.md`. İçerik sentetik, paylaşılabilir.
- [ ] Kullanılan `VLLM_*` ayarları: yalnızca anahtar ve değer. `VLLM_API_KEY` hariç.
- [ ] Backend logundan şu sayı (0 olmalı): `(Select-String -Path ..\backend-olcum.log -Pattern "export_refused").Count`

Not: Faz 2a'dan sonra `llm_denetimi` payının önceki koda göre artması beklenir. Denetim
alıntıları artık identifier'a genişletilemiyor. `failed_check_summary.py`'deki
"Otomatik duzeltme basarisizlik nedeni" satırında `maskeleme` sayısı bunu gösterir.

---

## Adım 1b — Aynı projede generic bileşik ad filtresi açık (ayrı ölçüm)

Adım 1'den hemen sonra, `.env`'de **yalnızca bu bayrağı** değiştirerek yapılır. Diğer ayarlar
Adım 1'deki gibi kalır.

1. `.env`'yi yedekleyin ve şu satırı ekleyin:
   ```powershell
   Copy-Item ..\.env ..\.env.yedek-adim1b
   Add-Content ..\.env "SCAN_GENERIC_COMPOUND_FILTER=true"
   ```
   `.env`, `masking_service` klasörünün bir üstündedir (`..\.env`). Backend'i yeniden başlatın ve
   logu ayrı bir dosyaya alın (`..\backend-adim1b.log`). Açılış logunda yine `build state=ok`
   görünmeli.
2. Aynı projede, **aynı proje/sicil/branch** ile export'u tekrarlayın. Hedef klasör farklı olsun:
   ```powershell
   .venv\Scripts\python.exe -m app.cli export --kaynak <proje> --hedef <cikti-adim1b> --proje <ad> --sicil <sicil> --branch <branch>
   .venv\Scripts\python.exe scripts\failed_check_summary.py --son --proje <ad>
   ```
3. Altın kümede aynı bayrakla:
   ```powershell
   $env:SCAN_GENERIC_COMPOUND_FILTER="true"
   .venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-adim1 --name generic-acik
   Remove-Item Env:SCAN_GENERIC_COMPOUND_FILTER
   ```
4. Ölçümden sonra bayrağı geri alın (`.env.yedek-adim1b`'yi geri kopyalayın) ve backend'i yeniden
   başlatın. Bayrağın kalıcı açılması ayrı bir karar.

**Getirin:**
- [ ] `failed_check_summary.py` çıktısının tamamı (Adım 1 ile karşılaştırmak için)
- [ ] Export raporundaki `Durumlar`, `Ciktiya alinmama nedenleri`, `Bulunan hassas veri turleri`
      satırlarındaki sayılar ve varsa `Yol/icerik uyusmazligi` sayıları. Dosya listeleri ve
      kimlikler hariç.
- [ ] `golden-real-adim1\generic-acik.json` ve `generic-acik.md`
- [ ] Bayrak açıkken maskelenmeyen ama maskelenmesi gerektiğini düşündüğünüz bir ad gördüyseniz,
      **değerini değil** yalnızca türünü yazın (ör. "iki parçalı Türkçe kod adı").

---

## Adım 2 — Faz 1 ayarları, benchmark ve tekrar ölçüm

1. Sunucu kontrolü:
   ```powershell
   curl http://<vllm>:8000/v1/models
   curl http://<vllm>:8000/metrics | findstr /i "prefix_cache num_requests_waiting"
   ```
2. Eşzamanlılık taraması. Gerçek endpoint'e yalnızca sentetik metin gider:
   ```powershell
   .venv\Scripts\python.exe scripts\benchmark_llm.py --concurrency 1 2 4 8 --files 16 --chars 12000 --chunk-chars 6000 --max-tokens 2048 > ..\bench-c.json 2> ..\bench-c.log
   (Select-String -Path ..\bench-c.log -Pattern "finish_reason='length'|status=timeout|status=error").Count
   ```
   Seçim kuralı: `diagnostics/faz1-llm-altyapi-onerileri.md` bölüm 3.2.
3. `.env`'yi yedekleyin. Seçilen değerleri uygulayın; başlangıç noktası `.env.example`'daki
   "Onerilen kurum ici vLLM" bloğu. `VLLM_PRESENCE_PENALTY` 0'da kalır. Backend'i yeniden
   başlatın.
4. Aynı ölçümleri tekrarlayın: Adım 1'in 1–3. maddeleri ve
   ```powershell
   .venv\Scripts\python.exe scripts\measure_golden.py --llm real --runs 3 --out ..\diagnostics\golden-real-adim2 --name sonra
   ```

**Getirin:**
- [ ] `bench-c.json` (sentetik, paylaşılabilir) ve `bench-c.log`'daki hata/kesilme sayısı
- [ ] `/metrics`'teki prefix cache satırları
- [ ] Uyguladığınız `VLLM_*` değerleri
- [ ] Adım 1 ile aynı çıktılar (özet, rapor satırları, log sayıları, `golden-real-adim2\sonra.*`)

---

## Sonuçların yorumu

Bkz. `golden-baseline-20261001/RAPOR.md` bölüm 4.4. Kısaca:
- `tespit_katmani` + `TypeError` → dağıtım sorunu sürüyor (Adım 0'a dönün).
- `llm_tespit`, `llm_denetimi_tamamlanamadi` ve zaman aşımı/kesilme sayıları → Faz 1 ayarlarıyla
  düşmesi beklenir.
- `llm_denetimi` → Faz 2b'nin hedefi.
- `acik_terim`, `sozdizimi` → Faz 3 öne alınabilir.

---

## Sicil düzeltmesi (migrasyon `f1c3a5e7b9d2`) — ölçümden ÖNCE

Alembic ile kurulan DB'lerde sicil kuralı `personnel_no` kategorisindeydi. Bu yüzden
kullanıcının sicil değeri içerikte ve yolda maskelenmiyordu. Düzeltme bir alembic migrasyonudur
ve Adım 0'ın parçası olarak çalışır. Adım 1'e bu bölümün kontrolleri geçmeden başlamayın.

**Nerede çalışır:** Adım 0'da `app\`, `alembic\` ve `scripts\` kopyalandıktan sonra, **backend
yeniden başlatılmadan önce**, DB yedeğinin hemen ardından:

```powershell
Copy-Item ..\masking.db ..\masking.db.yedek-sicil
.venv\Scripts\python.exe -m alembic upgrade head
```

- Çıktıda `sicil_kategori_duzeltme duzeltilen=1` görünmeli.
- DB elle kurulduysa ya da zaten düzeltildiyse `duzeltilen=0` görünür; bu da normaldir.
  Migrasyon idempotenttir, ikinci kez çalıştırmak bir şey değiştirmez.
- `... DEGISTIRILMEDI` ya da `aktif 'sicil_no' parametrik kurali yok` uyarısı görünürse **durun**
  ve aşağıdaki kontrollerin çıktısını getirin. Bu, kuralın elle değiştirilmiş olduğunu gösterir;
  migrasyon böyle bir kurala bilerek dokunmaz.

Ardından backend'i yeniden başlatın ve şu kontrolleri yapın:

1. Kural sorgusu (salt okunur):
   ```powershell
   .venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:../masking.db?mode=ro', uri=True); print(c.execute(\"SELECT kural_adi, kategori, aktif_mi FROM filtre_kurallari WHERE desen_tipi='parametric'\").fetchall())"
   ```
   - **Beklenen ("etkilenmiyor"):** listede `('sicil_no', 'sicil_no', 1)` var ve `personnel_no`
     hiç geçmiyor.
   - Sıra farklı olabilir; `project_name` ve `branch_name` satırları da `1` ile görünmeli.
2. Preflight çıktısında:
   `PASS stage=runtime_rules project_name=aktif sicil_no=aktif branch_name=aktif`.
3. Geçmiş etki raporu (salt okunur; değer ve yol yazmaz):
   ```powershell
   .venv\Scripts\python.exe scripts\sicil_etki_raporu.py --once <migrasyonu_calistirdiginiz_tarih> --tara
   ```
   - İlk satır `sicil_kurali=duzeltilmis export_sayisi=N` olmalı.
   - Her export için bir `run_id=... sonuc=etkilenmedi|olasi` satırı yazılır.
   - `--tara` ile `olasi` satırlarında `kaynakta=`/`ciktida=` dosya sayıları görünür:
     - `kaynakta>0`: sicil o export'ta maskelenmeden çıktıya gitti.
     - `ciktida>0`: hedef klasörde bugün hâlâ açık.
     - `yok`: klasör artık sunucuda değil.

**Getirin:**
- [ ] `alembic upgrade head` çıktısındaki `sicil_kategori_duzeltme ...` satırı (ve varsa uyarılar)
- [ ] 1. maddedeki sorgunun çıktısı
- [ ] Preflight'taki `stage=runtime_rules` satırı
- [ ] `sicil_etki_raporu.py` çıktısının tamamı (yalnızca run kimliği, tarih, durum ve sayılar
      içerir)

`ciktida>0` olan export'ların çıktıları dışarı verildiyse, onları yeniden export etmek ve eski
paketi geri çekmek size kalmış bir karardır. Komut bu klasörlere yazmaz ve onları silmez.

Geri dönüş gerekirse: `.venv\Scripts\python.exe -m alembic downgrade e3a7c1f9d2b5` yalnızca bu
migrasyonun düzelttiği kaydı eski haline getirir. Önerilmez, çünkü sızıntı geri gelir.
