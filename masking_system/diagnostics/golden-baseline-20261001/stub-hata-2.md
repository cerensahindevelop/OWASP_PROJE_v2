# Altin kume olcumu - LLM modu: stub (stub hata orani 0.02)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 10 / 9 | 10 / 9 | 10 / 9 |
| Onay kuyrugu orani | 10% (1) | 10% (1) | 10% (1) |
| Ciktidan disarida kalan orani | 10% (1) | 10% (1) | 10% (1) |
| Nedene gore | llm_denetimi=1 | llm_denetimi=1 | llm_denetimi=1 |
| LLM istek (dosya basina) | 24 (2.4) | 24 (2.4) | 24 (2.4) |
| Dosya basina LLM sn p50/p95 | 0.509/0.838 | 0.54/0.844 | 0.521/0.848 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/9 | 0/9 | 0/9 |
| javac | basarisiz (hata 2, eksik java 0) | basarisiz (hata 2, eksik java 0) | basarisiz (hata 2, eksik java 0) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [28, 28, 28], birebir ayni cikti dosyasi 9/9.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | llm_denetimi |
| `db/schema.sql` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | READY |
| `src/main/java/com/acme/karayel/musteri/PoseidonGatewayClient.java` | READY |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/resources/application.properties` | READY |
| `src/main/resources/musteri.json` | READY |
