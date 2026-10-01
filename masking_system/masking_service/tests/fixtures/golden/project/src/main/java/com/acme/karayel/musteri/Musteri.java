package com.acme.karayel.musteri;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

/**
 * KARAYEL musteri kaydi.
 * Sorumlu: Hakan Yilmaz (hakan.yilmaz@karayel-bank.com.tr)
 */
@Entity
@Table(name = "KARAYEL_MUSTERI")
public class Musteri {

    @Id
    private Long id;

    @Column(name = "TC_KIMLIK_NO", length = 11)
    @JsonProperty("tckimlik")
    private String tcKimlikNo;

    private String name;

    private Long customerId;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getTcKimlikNo() {
        return tcKimlikNo;
    }

    public void setTcKimlikNo(String tcKimlikNo) {
        this.tcKimlikNo = tcKimlikNo;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public Long getCustomerId() {
        return customerId;
    }

    public void setCustomerId(Long customerId) {
        this.customerId = customerId;
    }
}
