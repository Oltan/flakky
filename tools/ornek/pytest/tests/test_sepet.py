import socket

import allure
import pytest
from hesap import indirim, kdv_ekle

_DENEME = {"n": 0}


@allure.feature("Sepet")
def test_indirim_dogru(sepet):
    with allure.step("Yüzde 10 indirim uygulanır"):
        assert indirim(100, 0.1) == 90


@allure.feature("Sepet")
def test_kdv_urun_hatasi(sepet):
    with allure.step("KDV eklenir"):
        sonuc = kdv_ekle(100)
    with allure.step("Sonuç 120 olmalı"):
        assert sonuc == 120, f"beklenen 120, gelen {sonuc}"


@allure.feature("Sepet")
def test_test_kodu_hatasi(sepet):
    musteri = None
    with allure.step("Müşteri adı okunur"):
        musteri.ad  # noqa: B018 - kasıtlı test kodu hatası


@allure.feature("Ödeme")
def test_odeme_servisi_ortam():
    with allure.step("Ödeme servisine bağlanılır"):
        s = socket.create_connection(("127.0.0.1", 1), timeout=1)
        s.close()


@allure.feature("Ödeme")
def test_flaky_ilk_denemede_kalir():
    _DENEME["n"] += 1
    allure.attach(f"deneme={_DENEME['n']}", name="deneme", attachment_type=allure.attachment_type.TEXT)
    with allure.step("Önbellek ısınmış olmalı"):
        assert _DENEME["n"] > 1, "önbellek henüz hazır değil"


@pytest.mark.skip(reason="özellik kapalı")
def test_atlanan():
    pass
