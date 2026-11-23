package ornek;

import java.util.ArrayList;
import java.util.List;

/** Test edilen "ürün" kodu (kasıtlı hatalı). */
public class Sepet {
    private final List<Double> fiyatlar = new ArrayList<>();

    public void ekle(double fiyat) {
        fiyatlar.add(fiyat);
    }

    public double toplam() {
        return fiyatlar.stream().mapToDouble(Double::doubleValue).sum();
    }

    public double kdvliToplam() {
        return Math.round(toplam() * 1.18 * 100) / 100.0; // hata: oran 1.20 olmalı
    }
}
