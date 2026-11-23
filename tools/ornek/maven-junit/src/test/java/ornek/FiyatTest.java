package ornek;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Map;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

class FiyatTest {
    private static int deneme = 0;

    @Test
    void indirimDogru() {
        assertEquals(90.0, Fiyat.indirim(100, 0.1), 0.001);
    }

    @Test
    void kdvUrunHatasi() {
        assertEquals(120.0, Fiyat.kdvEkle(100), 0.001, "KDV dahil tutar");
    }

    @Test
    void testKoduHatasi() {
        Map<String, String> musteri = null;
        assertTrue(musteri.get("ad").isEmpty()); // kasıtlı NullPointerException
    }

    @Test
    void odemeServisiOrtam() throws Exception {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", 1), 1000);
        }
    }

    @Test
    void flakyIlkDenemedeKalir() {
        deneme++;
        assertTrue(deneme > 1, "önbellek henüz hazır değil, deneme=" + deneme);
    }

    @Test
    @Disabled("özellik kapalı")
    void atlanan() {}
}
