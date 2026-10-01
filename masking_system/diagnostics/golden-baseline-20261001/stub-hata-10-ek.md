# Altin kume olcumu - LLM modu: stub (stub hata orani 0.1)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 11 / 8 | 11 / 8 | 11 / 8 |
| Onay kuyrugu orani | 27% (3) | 27% (3) | 27% (3) |
| Ciktidan disarida kalan orani | 27% (3) | 27% (3) | 27% (3) |
| Nedene gore | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 |
| LLM istek (dosya basina) | 29 (2.64) | 29 (2.64) | 29 (2.64) |
| Dosya basina LLM sn p50/p95 | 0.696/3.092 | 0.669/2.979 | 0.663/3.025 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/8 | 0/8 | 0/8 |
| javac | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [26, 26, 26], birebir ayni cikti dosyasi 8/8.

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
| `src/main/resources/application.properties` | llm_denetimi_tamamlanamadi |
| `src/main/resources/musteri.json` | READY |
