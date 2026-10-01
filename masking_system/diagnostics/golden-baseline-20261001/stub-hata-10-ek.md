# Altin kume olcumu - LLM modu: stub (stub hata orani 0.1)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 10 / 7 | 10 / 7 | 10 / 7 |
| Onay kuyrugu orani | 30% (3) | 30% (3) | 30% (3) |
| Ciktidan disarida kalan orani | 30% (3) | 30% (3) | 30% (3) |
| Nedene gore | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 | llm_denetimi=2, llm_denetimi_tamamlanamadi=1 |
| LLM istek (dosya basina) | 25 (2.5) | 25 (2.5) | 25 (2.5) |
| Dosya basina LLM sn p50/p95 | 0.491/1.88 | 0.505/1.819 | 0.5/1.798 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/7 | 0/7 | 0/7 |
| javac | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) | basarisiz (hata 4, eksik java 1) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [25, 25, 25], birebir ayni cikti dosyasi 7/7.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | llm_denetimi |
| `db/schema.sql` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | llm_denetimi |
| `src/main/java/com/acme/karayel/musteri/PoseidonGatewayClient.java` | READY |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/resources/application.properties` | llm_denetimi_tamamlanamadi |
| `src/main/resources/musteri.json` | READY |
