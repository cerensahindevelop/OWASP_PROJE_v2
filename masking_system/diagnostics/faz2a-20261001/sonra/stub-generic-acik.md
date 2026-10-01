# Altin kume olcumu - LLM modu: stub (stub hata orani 0.0)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 11 / 7 | 11 / 7 | 11 / 7 |
| Onay kuyrugu orani | 36% (4) | 36% (4) | 36% (4) |
| Ciktidan disarida kalan orani | 36% (4) | 36% (4) | 36% (4) |
| Nedene gore | llm_denetimi=4 | llm_denetimi=4 | llm_denetimi=4 |
| Yol/icerik uyusmazligi (olcum) | 0 dosya / 0 terim | 0 dosya / 0 terim | 0 dosya / 0 terim |
| LLM istek (dosya basina) | 24 (2.18) | 24 (2.18) | 24 (2.18) |
| Dosya basina LLM sn p50/p95 | 0.762/1.115 | 0.909/1.31 | 0.887/1.074 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | 0 | 0 | 0 |
| Geri alma bayt farki | 0/7 | 0/7 | 0/7 |
| javac | basarisiz (hata 2, eksik java 2) | basarisiz (hata 2, eksik java 2) | basarisiz (hata 2, eksik java 2) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [23, 23, 23], birebir ayni cikti dosyasi 7/7.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | llm_denetimi |
| `db/schema.sql` | READY |
| `pom.xml` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | llm_denetimi |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/java/com/acme/karayel/poseidon/PoseidonGatewayClient.java` | llm_denetimi |
| `src/main/resources/application.properties` | llm_denetimi |
| `src/main/resources/musteri.json` | READY |
