package ornek.adimlar;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.cucumber.java.After;
import io.cucumber.java.Before;
import io.cucumber.java.Scenario;
import io.cucumber.java.en.And;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import java.util.concurrent.TimeoutException;
import ornek.Sepet;

public class SepetAdimlari {
    private static int deneme = 0;
    private Sepet sepet;
    private String musteri;

    @Before
    public void hazirla(Scenario s) {
        s.attach("tarayıcı açıldı: " + s.getName(), "text/plain", "kurulum-log");
    }

    @After
    public void temizle(Scenario s) {
        s.attach("tarayıcı kapatıldı", "text/plain", "temizlik-log");
    }

    @Given("boş bir sepet")
    public void bosSepet() {
        sepet = new Sepet();
    }

    @When("sepete {double} TL'lik ürün eklenir")
    public void urunEklenir(double fiyat) {
        sepet.ekle(fiyat);
    }

    @Then("KDV dahil toplam {double} TL olmalı")
    public void kdvliToplam(double beklenen) {
        assertEquals(beklenen, sepet.kdvliToplam(), 0.001, "KDV dahil toplam");
    }

    @Then("toplam {double} TL olmalı")
    public void toplam(double beklenen) {
        assertEquals(beklenen, sepet.toplam(), 0.001);
    }

    @And("müşteri adı ekranda görünür")
    public void musteriAdi() {
        assertTrue(musteri.length() > 0); // kasıtlı NullPointerException: musteri hiç atanmadı
    }

    @When("ödeme servisi çağrılır")
    public void odemeServisi() throws TimeoutException {
        throw new TimeoutException("ödeme servisi 30 sn içinde yanıt vermedi");
    }

    @Then("önbellek hazır olmalı")
    public void onbellek() {
        deneme++;
        assertTrue(deneme > 1, "önbellek henüz hazır değil, deneme=" + deneme);
    }
}
