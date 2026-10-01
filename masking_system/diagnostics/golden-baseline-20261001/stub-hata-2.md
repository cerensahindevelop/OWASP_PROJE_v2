# Altin kume olcumu - LLM modu: stub (stub hata orani 0.02)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 11 / 9 | 11 / 9 | 11 / 9 |
| Onay kuyrugu orani | 18% (2) | 18% (2) | 18% (2) |
| Ciktidan disarida kalan orani | 18% (2) | 18% (2) | 18% (2) |
| Nedene gore | llm_denetimi=2 | llm_denetimi=2 | llm_denetimi=2 |
| LLM istek (dosya basina) | 29 (2.64) | 29 (2.64) | 29 (2.64) |
| Dosya basina LLM sn p50/p95 | 0.58/2.01 | 0.59/1.944 | 0.655/2.079 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/9 | 0/9 | 0/9 |
| javac | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [28, 28, 28], birebir ayni cikti dosyasi 9/9.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | llm_denetimi |
| `db/schema.sql` | READY |
| `pom.xml` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | READY |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/java/com/acme/karayel/poseidon/PoseidonGatewayClient.java` | llm_denetimi |
| `src/main/resources/application.properties` | READY |
| `src/main/resources/musteri.json` | READY |
