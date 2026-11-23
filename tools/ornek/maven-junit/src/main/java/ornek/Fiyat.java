package ornek;

/** Test edilen "ürün" kodu (kasıtlı hatalı). */
public class Fiyat {
    public static double kdvEkle(double tutar) {
        return Math.round(tutar * 1.18 * 100) / 100.0; // hata: oran 1.20 olmalı
    }

    public static double indirim(double tutar, double oran) {
        return tutar - tutar * oran;
    }
}
