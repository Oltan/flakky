"""Test edilen 'ürün' kodu (kasıtlı hatalı)."""


def kdv_ekle(tutar: float) -> float:
    return round(tutar * 1.18, 2)  # hata: oran 1.20 olmalı


def indirim(tutar: float, oran: float) -> float:
    return tutar - tutar * oran
