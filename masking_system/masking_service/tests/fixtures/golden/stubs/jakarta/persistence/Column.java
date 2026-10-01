package jakarta.persistence;

public @interface Column {
    String name() default "";
    int length() default 255;
}
