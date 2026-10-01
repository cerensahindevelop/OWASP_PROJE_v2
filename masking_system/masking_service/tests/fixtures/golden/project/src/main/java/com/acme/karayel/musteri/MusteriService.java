package com.acme.karayel.musteri;

import org.springframework.stereotype.Service;

@Service
public class MusteriService {

    private final MusteriRepository musteriRepository;
    private final PoseidonGatewayClient poseidonGatewayClient;
    private final UserService userService;

    public MusteriService(MusteriRepository musteriRepository, PoseidonGatewayClient poseidonGatewayClient,
                          UserService userService) {
        this.musteriRepository = musteriRepository;
        this.poseidonGatewayClient = poseidonGatewayClient;
        this.userService = userService;
    }

    // Kural Ayse Demir ile yapilan toplantida kararlastirildi.
    public Musteri bul(String tcKimlikNo) {
        if (!poseidonGatewayClient.dogrula(tcKimlikNo)) {
            return null;
        }
        Musteri musteri = musteriRepository.findByTcKimlikNo(tcKimlikNo);
        if (musteri != null) {
            userService.audit(musteri.getCustomerId());
        }
        return musteri;
    }
}
