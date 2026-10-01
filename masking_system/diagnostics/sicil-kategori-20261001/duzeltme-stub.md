# Altin kume olcumu - LLM modu: stub (stub hata orani 0.0)

| Metrik | Kosu 1 | Kosu 2 | Kosu 3 |
|---|---|---|---|
| Taranan / hazir dosya | 12 / 8 | 12 / 8 | 12 / 8 |
| Onay kuyrugu orani | 33% (4) | 33% (4) | 33% (4) |
| Ciktidan disarida kalan orani | 33% (4) | 33% (4) | 33% (4) |
| Nedene gore | llm_denetimi=4 | llm_denetimi=4 | llm_denetimi=4 |
| Yol/icerik uyusmazligi (olcum) | 1 dosya / 1 terim | 1 dosya / 1 terim | 1 dosya / 1 terim |
| LLM istek (dosya basina) | 26 (2.17) | 26 (2.17) | 26 (2.17) |
| Dosya basina LLM sn p50/p95 | 0.842/1.122 | 0.742/0.951 | 0.689/0.943 |
| Canary sizintisi | 0 | 0 | 0 |
| Terim sizintisi | tckimlik=5 | tckimlik=5 | tckimlik=5 |
| Maskelenmemesi gereken ihlali | UserService | UserService | UserService |
| Geri alma bayt farki | 0/8 | 0/8 | 0/8 |
| javac | basarisiz (hata 3, eksik java 2) | basarisiz (hata 3, eksik java 2) | basarisiz (hata 3, eksik java 2) |

Determinizm: Jaccard (min) 1.0, maskelenen deger sayilari [29, 29, 29], birebir ayni cikti dosyasi 8/8.

## Dosya bazinda sonuc (kosu 1)

| Dosya | Sonuc |
|---|---|
| `README.md` | llm_denetimi |
| `db/schema.sql` | READY |
| `docs/ekip/P-GOLDEN-0001/notlar.md` | READY |
| `pom.xml` | READY |
| `src/main/java/com/acme/karayel/musteri/Musteri.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriController.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriRepository.java` | READY |
| `src/main/java/com/acme/karayel/musteri/MusteriService.java` | llm_denetimi |
| `src/main/java/com/acme/karayel/musteri/UserService.java` | READY |
| `src/main/java/com/acme/karayel/poseidon/PoseidonGatewayClient.java` | llm_denetimi |
| `src/main/resources/application.properties` | llm_denetimi |
| `src/main/resources/musteri.json` | READY |
