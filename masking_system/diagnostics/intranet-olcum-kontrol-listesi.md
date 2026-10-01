# Intranet ölçüm kontrol listesi

Amaç: gerçek Qwen/vLLM ile gerçek projede karantina nedenlerinin dağılımını (`failed_check`)
ölçmek ve Faz 1 ayarlarının etkisini görmek. Faz 2b ve Faz 3'ün sırası bu verilere göre
belirlenecek.

Komutlar PowerShell'dir ve `C:\masking\masking_service` klasöründe çalıştırılır. Kendi
kurulum yolunuz farklıysa uyarlayın.

**Asla paylaşmayın:** `.env`, `masking.db`, maskeli çıktı klasörü, kaynak dosyalar, backend
logunun ham satırları. Faz 2a'ya kadar loglar orijinal dosya yollarını içerir (kural 7); loglardan
yalnızca aşağıda belirtilen **sayıları** getirin.

Aşağıdaki "Getirin" maddeleri bu çalışmayı yürüten oturuma geri getirilecek çıktılardır.

---

## Adım 0 — Tek commit'ten dağıtım ve preflight

1. Dağıtılacak commit'i not edin (GitHub'daki dal veya PR'ın son commit'i).
2. Bu commit'ten şunları **eksiksiz** kopyalayın (seçili dosya değil, klasörün tamamı):
   - `masking_service\app\`
   - `masking_service\alembic\`
   - `masking_service\scripts\`
   - `masking_service\tests\fixtures\golden\` (yalnızca ölçüm için)
3. Veritabanını yedekleyin ve migrasyonu uygulayın (yalnızca yeni bir kolon ekler):
   ```powershell
   Copy-Item ..\masking.db ..\masking.db.yedek-olcum
   .venv\Scripts\python.exe -m alembic upgrade head
   ```
4. Backend ve arayüzü kapatıp yeniden başlatın. Backend logunu dosyaya alın:
   ```powershell
   .venv\Scripts\python.exe -m uvicorn api_app:app --host 127.0.0.1 --port 8001 *>&1 | Tee-Object -FilePath ..\backend-olcum.log
   ```
5. Preflight:
   ```powershell
   .venv\Scripts\python.exe scripts\check_llm_preflight.py
   ```

**Getirin:**
- [ ] Dağıtılan commit kimliği
- [ ] `check_llm_preflight.py` çıktısının son satırları (`RESULT=...`). `FAIL` varsa `FAIL ... frames=`
      satırı. Kurulum yollarını gizleyebilirsiniz.
- [ ] `python --version` çıktısı

`RESULT=OFFLINE_OK` gelmeden sonraki adıma geçmeyin.

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
      Altlarındaki dosya listelerini getirmeyin.
- [ ] Log sayıları (4 sayı)
- [ ] `golden-real-adim1\once.json` ve `once.md`. İçerik sentetik, paylaşılabilir.
- [ ] Kullanılan `VLLM_*` ayarları: yalnızca anahtar ve değer. `VLLM_API_KEY` hariç.

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
