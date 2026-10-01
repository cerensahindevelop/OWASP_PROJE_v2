# Altin kume olcumu - LLM modu: off

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 10 / 10 | 10 / 10 | 10 / 10 |
| Onay kuyrugu orani | 0% (0) | 0% (0) | 0% (0) |
| Ciktidan disarida kalan orani | 0% (0) | 0% (0) | 0% (0) |
| Nedene gore | - | - | - |
| LLM istek (dosya basina) | - | - | - |
| Dosya basina LLM sn p50/p95 | - | - | - |
| Canary sizintisi | canary_host_db (cnry-db01/duz), canary_host_gw (cnry-gw01/duz), canary_email (hakan.yilmaz/duz) | canary_host_db (cnry-db01/duz), canary_host_gw (cnry-gw01/duz), canary_email (hakan.yilmaz/duz) | canary_host_db (cnry-db01/duz), canary_host_gw (cnry-gw01/duz), canary_email (hakan.yilmaz/duz) |
| Terim sizintisi | tckimlik=5, kod_adi_poseidon=21, kisi_ayse=2, adres_cok_satirli=1 | tckimlik=5, kod_adi_poseidon=21, kisi_ayse=2, adres_cok_satirli=1 | tckimlik=5, kod_adi_poseidon=21, kisi_ayse=2, adres_cok_satirli=1 |
| Maskelenmemesi gereken ihlali | Teknik sorumlu, Veri merkezi | Teknik sorumlu, Veri merkezi | Teknik sorumlu, Veri merkezi |
| Geri alma bayt farki | 0/10 | 0/10 | 0/10 |
| javac | basarili (hata 0, eksik java 0) | basarili (hata 0, eksik java 0) | basarili (hata 0, eksik java 0) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [19, 19, 19], birebir ayni cikti dosyasi 10/10.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | READY |
| `db/schema.sql` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | READY |
| `src/main/java/com/acme/karayel/musteri/PoseidonGatewayClient.java` | READY |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/resources/application.properties` | READY |
| `src/main/resources/musteri.json` | READY |
