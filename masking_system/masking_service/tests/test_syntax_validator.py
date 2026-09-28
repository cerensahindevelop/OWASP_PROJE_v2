"""syntax_validator.py'nin dosya-turune-gore-dogru-kontrol secimi icin
regresyon testleri.

Gercek export'ta gozlemlenen hata: bracket/quote denge kontrolcusu
(_check_bracket_and_quote_balance), .md/.rst/.toml/LICENSE/Dockerfile gibi
dogal-dil/doküman formatlarina da uygulaniyordu - bu formatlarda tek
tirnaklar sadece kesme isareti/alinti (orn. "don't") olup TOPLAM SAYILARI
genelde TEK sayidir, bu da kontrolcunun HER ZAMAN "kapanmamis string"
sanmasina yol aciyordu; maskeleme hicbir sey BOZMASA BILE. Bu artik SADECE
bilinen programlama dili uzantilarina uygulanir; digerleri (ve gercek bir
TOML parser'i olan .toml) icin dogru/hicbir sekilde-yanlis-alarm-vermeyen
kontrol yapilir.
"""

from __future__ import annotations

from app.services.syntax_validator import validate_masked_syntax


class TestProseFilesNeverFalsePositive:
    def test_markdown_with_odd_apostrophe_count_is_valid(self):
        text = "It's a great tool, don't you think? Here's why: it just works.\n"
        assert text.count("'") % 2 == 1  # tek sayida kesme isareti - once hataliydi
        assert validate_masked_syntax("README.md", text) is None

    def test_rst_with_unbalanced_parens_is_valid(self):
        text = "Note (see chapter 3 for details on this (and related) topics.\n"
        assert validate_masked_syntax("PROVIDERS.rst", text) is None

    def test_license_boilerplate_is_valid(self):
        text = (
            'Licensed under the "Apache License" (the "License"); '
            "you may not use this file except in compliance with the License.\n"
        )
        assert validate_masked_syntax("LICENSE", text) is None

    def test_dockerfile_is_valid(self):
        text = 'RUN echo "building" && pip install -r requirements.txt\n'
        assert validate_masked_syntax("Dockerfile", text) is None

    def test_dockerignore_is_valid(self):
        text = "*.pyc\n__pycache__/\n.git\n"
        assert validate_masked_syntax(".dockerignore", text) is None

    def test_extensionless_file_is_valid(self):
        text = "It's fine, don't worry about it.\n"
        assert validate_masked_syntax("NOTICE", text) is None


class TestTomlGetsARealParser:
    def test_valid_toml_passes(self):
        text = 'name = "airflow"\nversion = "3.0.0"\ndependencies = ["a>=1.0", "b>=2.0"]\n'
        assert validate_masked_syntax("pyproject.toml", text) is None

    def test_broken_toml_is_caught(self):
        text = 'name = "airflow"\nversion = "3.0.0\n'  # kapanmamis string
        error = validate_masked_syntax("pyproject.toml", text)
        assert error is not None
        assert "TOML" in error

    def test_toml_apostrophe_in_comment_is_not_a_false_positive(self):
        # Eskiden generic bracket/quote kontrolcusu buna da uygulanirdi ve
        # tek sayida tek-tirnak yuzunden hatali "kapanmamis string" derdi.
        text = "# don't change this without asking\nname = \"airflow\"\n"
        assert validate_masked_syntax("pyproject.toml", text) is None


class TestRealProgrammingLanguagesStillChecked:
    def test_go_unbalanced_braces_still_caught(self):
        text = "func main() {\n    fmt.Println(\"hi\")\n"  # kapanmamis "{"
        error = validate_masked_syntax("main.go", text)
        assert error is not None

    def test_bracket_error_message_points_to_the_line_it_opened_on(self):
        """The user must see WHERE to look, not just THAT something broke."""
        text = "func main() {\n    fmt.Println(\"hi\")\n"  # '{' opened on line 1
        error = validate_masked_syntax("main.go", text)
        assert "satir 1" in error

    def test_unbalanced_closing_bracket_message_points_to_its_own_line(self):
        text = "func main() {\n}\n}\n"  # extra unmatched '}' on line 3
        error = validate_masked_syntax("main.go", text)
        assert error is not None
        assert "satir 3" in error

    def test_unclosed_string_literal_message_points_to_where_it_opened(self):
        text = 'public class Foo {\n    String x = "never closed;\n}\n'
        error = validate_masked_syntax("Foo.java", text)
        assert error is not None
        assert "satir 2" in error

    def test_java_valid_code_passes(self):
        text = 'public class Foo {\n    void bar() { System.out.println("ok"); }\n}\n'
        assert validate_masked_syntax("Foo.java", text) is None

    def test_python_still_uses_real_ast_parser(self):
        text = "def f(:\n    pass\n"
        error = validate_masked_syntax("broken.py", text)
        assert error is not None
        assert "Python sozdizimi hatasi" in error


class TestCommentsDoNotAffectBalance:
    """Bir yorumun (// veya #) icindeki kesme isareti/parantez artik gercek
    bir string acilisi/parantez SANILMIYOR - yorum tespiti SADECE string
    disindayken devrede, tirnak/parantez sayimindan ONCE calisir."""

    def test_line_comment_apostrophe_in_go_is_not_a_false_positive(self):
        text = "func main() {\n    // it's fine, don't touch this\n    fmt.Println(\"hi\")\n}\n"
        assert validate_masked_syntax("main.go", text) is None

    def test_line_comment_apostrophe_in_shell_is_not_a_false_positive(self):
        text = "#!/bin/bash\n# don't run this twice\necho \"ok\"\n"
        assert validate_masked_syntax("deploy.sh", text) is None

    def test_line_comment_unbalanced_bracket_is_not_a_false_positive(self):
        text = "public class Foo {\n    // note: unbalanced ( on purpose in this comment\n    void bar() {}\n}\n"
        assert validate_masked_syntax("Foo.java", text) is None

    def test_block_comment_apostrophe_and_bracket_is_not_a_false_positive(self):
        text = "/* it's a helper, unbalanced ( here too */\npublic class Foo {\n    void bar() {}\n}\n"
        assert validate_masked_syntax("Foo.java", text) is None

    def test_line_comment_does_not_hide_a_real_unbalanced_bracket_outside_it(self):
        text = "func main() {\n    // it's a comment\n    fmt.Println(\"hi\")\n"  # kapanmamis "{"
        error = validate_masked_syntax("main.go", text)
        assert error is not None

    def test_url_with_double_slash_inside_string_is_not_treated_as_comment(self):
        # "//" bir string literal'in ICINDE - in_string dalinda islenir,
        # yorum baslangici olarak yorumlanmamali (gercek dil grameriyle tutarli).
        text = 'url := "http://example.com/a/b"\nfmt.Println(url)\n'
        assert validate_masked_syntax("main.go", text) is None


class TestOriginalTextPreExistingBreakage:
    """original_text verildiginde, kontrol SADECE maskelemenin BIZZAT
    yarattigi bozulmayi yakalar - kaynak dosya sisteme zaten gecersiz
    sozdizimiyle yuklendiyse export bloke edilmez."""

    def test_masking_introduced_break_is_still_caught_when_original_was_valid(self):
        original = "def f():\n    pass\n"
        masked = "def f(:\n    pass\n"  # maskeleme burada sozdizimini bozdu
        error = validate_masked_syntax("app.py", masked, original_text=original)
        assert error is not None
        assert "Python sozdizimi hatasi" in error

    def test_pre_existing_breakage_is_not_blocked(self):
        original = "def f(:\n    pass\n"  # kaynak dosya zaten gecersiz
        masked = "def f(:\n    pass\n"  # maskeleme hicbir sey degistirmedi
        assert validate_masked_syntax("app.py", masked, original_text=original) is None

    def test_pre_existing_breakage_not_blocked_even_if_masking_also_touched_it(self):
        # Kaynak zaten bozuk oldugu icin (ayni onkosul hatasi devam ettigi
        # surece) maskelemenin metni degistirmis olmasi onemli degil.
        original = 'password = "abc\n'  # kapanmamis string
        masked = 'password = "mask_password_1\n'  # hala kapanmamis
        assert validate_masked_syntax("app.py", masked, original_text=original) is None

    def test_without_original_text_behaves_as_before(self):
        # original_text verilmezse (varsayilan None), eski davranis aynen
        # surer - her zaman maskelenmis metnin kendisi kontrol edilir.
        masked = "def f(:\n    pass\n"
        error = validate_masked_syntax("app.py", masked)
        assert error is not None

    def test_pre_existing_breakage_does_not_hide_a_different_new_error(self):
        # Kaynak zaten bozuk olmasi, maskelemenin FARKLI bir hata yaratmadigini
        # KANITLAMAZ - sadece maskelenmis metindeki hata aynen kaynaktakiyle
        # birebir ayni ise (ayni mesaj) bilinen/onceden var olan sorun sayilir.
        original = "x = (1,\n"  # "'(' was never closed"
        masked = "x = (1,\ndef f(:\n    pass\n"  # tamamen farkli bir hata
        error = validate_masked_syntax("app.py", masked, original_text=original)
        assert error is not None
        assert "Python sozdizimi hatasi" in error

    def test_pre_existing_breakage_with_identical_error_is_still_not_blocked(self):
        original = "x = (1,\n"
        masked = "x = (mask_placeholder_1,\n"  # ayni hata, ayni satir/sutun
        assert validate_masked_syntax("app.py", masked, original_text=original) is None

    def test_pre_existing_bracket_error_does_not_hide_a_different_new_one(self):
        original = 'func main() {\n    x := "abc\n}\n'  # kapatilmamis string
        masked = 'func main() {\n    fmt.Println("hi")\n'  # farkli hata: kapatilmamis parantez
        error = validate_masked_syntax("main.go", masked, original_text=original)
        assert error is not None
