package org.springframework.data.jpa.repository;

public interface JpaRepository<T, ID> {
    T save(T entity);
}
