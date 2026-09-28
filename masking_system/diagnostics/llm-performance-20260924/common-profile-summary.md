# Ortak uygulama profili değerlendirmesi

Önerilen ve kod varsayılanlarına uygulanan profil: concurrency=1, timeout=200 saniye, chunk=6000 karakter, overlap=500 karakter, max_tokens=512. Yerel .env zaten bu profildeydi. Concurrency=2, mevcut Ollama Parallel:1 için koşulsuz güvenilir kabul edilmedi. İki 110 saniyelik isteğin ikinci yanıtı yaklaşık 220 saniye gerektirebilir; sunucu kuyruğundaki bekleme HTTP timeout'a dahildir. Concurrency=1 bu uygulamanın beklemesini HTTP deadline başlamadan yerel kuyrukta yapar. Diğer process/istemcilerin yükünü sınırlamaz.

Chunking, duplicate birleştirme, timeout/karantina, output truncation ve performans loglama akışları korunmuştur. Minimum değişiklikler: config timeout 60→200, concurrency 4→1, eksik concurrency için runtime fallback 4→1 ve .env.example eşleştirmesi. Backend yeni kod varsayılanlarını yeniden başlatılınca alır; inference servis ayarları değiştirilmedi.

Ollama: OLLAMA_NUM_PARALLEL=1 korunur. 2 ancak ayrı server ayarı olarak VRAM/context ve yük testiyle denenir. Context, tokenizer ile prompt+kurum talimatları+chunk+çıktı bütçesine göre seçilir; mevcut 32768 otomatik küçültülmez. 8192 yalnız yeterliliği doğrulanırsa adaydır.

Qwen/vLLM: kesin model/GPU/sürüm doğrulanmadan nihai ayar verilemez. max_model_len=8192/16384, max_num_seqs=8, max_num_batched_tokens=4096/8192, gpu_memory_utilization=0.85/0.90 benchmark adaylarıdır. TP ve quantization model+cache+runtime belleği ve donanım desteğine göre ayrıca belirlenir. Concurrency=2 ortak varsayılan değil, intra performans adayıdır. Thinking destekli Qwen'de kısa JSON için non-thinking profilinin kalitesi ayrıca sınanmalıdır. 512 çıktı tokenı yoğun bulgulara yetmeyebilir; kesilme karantinaya gider.

İlgili regresyon: 75 geçti, 1 mevcut bağımlılık deprecation uyarısı (common-profile-tests.log). Env'den bağımsız varsayılanlar ve tek runner altında concurrency=2/4 kuyruk timeout riski test edildi.

Canlı kayıtlar 25 Eylül 2026'da kontrol edildi. Her profil için 5546 karakterlik iki sentetik dosya, dört detection/audit isteği:

| Concurrency | Grup toplamı | Maksimum HTTP süresi | Sonuç |
|---|---|---|---|
| 1 | 202.122 s | 74.468 s | 4/4 başarılı |
| 2 | 158.832 s | 123.417 s | 4/4 başarılı |

Timeout ve çıktı kesilmesi görülmedi. Bu küçük örnekte concurrency=2 başarısız olmadı; genel güvenilirliği kanıtlanmadı. Diğer sunucu istemcileri izole edilmedi ve profiller sıralı çalıştı: süre farkı kontrollü hızlanma ölçümü değildir. Intra Qwen/vLLM canlı testi yapılmadı. Kanıt: common-profile-live.json ve common-profile-live.log.
