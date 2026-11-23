import allure
import pytest


@pytest.fixture
def sepet():
    with allure.step("Sepet hazırlanıyor"):
        s = {"urunler": []}
    allure.attach("sepet kuruldu", name="kurulum-log", attachment_type=allure.attachment_type.TEXT)
    yield s
    allure.attach("sepet temizlendi", name="temizlik-log", attachment_type=allure.attachment_type.TEXT)
