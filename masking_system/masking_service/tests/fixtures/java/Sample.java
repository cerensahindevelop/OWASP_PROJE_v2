import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
@Retention(RetentionPolicy.RUNTIME)
@interface Meta { String value(); }
@Meta("office@example.com")
public class Sample {
    public static final String CONTACT = "alice@example.com";
    public static final String HOST = "10.24.36.48";
    public static final String PASSWORD = "localSecretValue123";
    public static final String UNICODE = "Türkçe\u0000😀";
    public static final long NUMBER = 1234567890123456789L;
    public static String value() { return CONTACT; }
    public static void main(String[] args) { System.out.print(value()); }
}
