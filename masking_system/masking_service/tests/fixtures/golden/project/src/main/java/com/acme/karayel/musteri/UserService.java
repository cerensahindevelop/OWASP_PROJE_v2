package com.acme.karayel.musteri;

import org.springframework.stereotype.Service;

@Service
public class UserService {

    public void audit(Long customerId) {
        if (customerId == null) {
            throw new IllegalArgumentException("customerId");
        }
    }
}
