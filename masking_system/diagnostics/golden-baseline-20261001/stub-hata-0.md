# Altin kume olcumu - LLM modu: stub (stub hata orani 0.0)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 11 / 10 | 11 / 10 | 11 / 10 |
| Onay kuyrugu orani | 9% (1) | 9% (1) | 9% (1) |
| Ciktidan disarida kalan orani | 9% (1) | 9% (1) | 9% (1) |
| Nedene gore | llm_denetimi=1 | llm_denetimi=1 | llm_denetimi=1 |
| LLM istek (dosya basina) | 28 (2.55) | 28 (2.55) | 28 (2.55) |
| Dosya basina LLM sn p50/p95 | 0.616/0.978 | 0.651/0.98 | 0.689/1.032 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/10 | 0/10 | 0/10 |
| javac | basarisiz (hata 2, eksik java 0) | basarisiz (hata 2, eksik java 0) | basarisiz (hata 2, eksik java 0) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [28, 28, 28], birebir ayni cikti dosyasi 10/10.

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
| `src/main/java/com/acme/karayel/poseidon/PoseidonGatewayClient.java` | READY |
| `src/main/resources/application.properties` | READY |
| `src/main/resources/musteri.json` | READY |
