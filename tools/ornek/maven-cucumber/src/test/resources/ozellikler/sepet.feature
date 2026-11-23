Feature: Sepet

  Background:
    Given boş bir sepet

  Scenario: Toplam doğru hesaplanır
    When sepete 100.0 TL'lik ürün eklenir
    Then toplam 100.0 TL olmalı

  Scenario: KDV dahil toplam
    When sepete 100.0 TL'lik ürün eklenir
    Then KDV dahil toplam 120.0 TL olmalı

  Scenario: Müşteri adı gösterilir
    When sepete 50.0 TL'lik ürün eklenir
    And müşteri adı ekranda görünür

  Scenario: Ödeme yapılır
    When sepete 50.0 TL'lik ürün eklenir
    And ödeme servisi çağrılır
    Then toplam 50.0 TL olmalı

  Scenario: Önbellekli sepet
    Then önbellek hazır olmalı
