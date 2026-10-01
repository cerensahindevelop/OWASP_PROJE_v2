package com.acme.karayel.musteri;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/musteri")
public class MusteriController {

    private final MusteriService musteriService;

    public MusteriController(MusteriService musteriService) {
        this.musteriService = musteriService;
    }

    @GetMapping("/{tcKimlikNo}")
    public Musteri getir(@PathVariable("tcKimlikNo") String tcKimlikNo) {
        return musteriService.bul(tcKimlikNo);
    }
}
