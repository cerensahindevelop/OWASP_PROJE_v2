package com.acme.karayel.musteri;

import org.springframework.data.jpa.repository.JpaRepository;

public interface MusteriRepository extends JpaRepository<Musteri, Long> {

    Musteri findByTcKimlikNo(String tcKimlikNo);
}
