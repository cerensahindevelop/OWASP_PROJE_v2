"""Legacy lexical validation for languages without a configured parser."""

_BRACKET_CLOSERS = {")": "(", "}": "{", "]": "["}
_BRACKET_OPENERS = set(_BRACKET_CLOSERS.values())

# _check_bracket_and_quote_balance() SADECE gercek programlama dilleri icin
# anlamlidir - tirnaklar orada GERCEKTEN string literal sinirlaridir. Doğal
# dil/doküman formatlarinda (Markdown, RST, LICENSE, Dockerfile, .env,
# duz metin...) tek tirnaklar sadece kesme isareti/alintidir (orn. "don't",
# "it's") ve TOPLAM SAYILARI cogunlukla TEK sayidir - bu da kontrolcunun her
# zaman "kapanmamis string" sanmasina yol acar; maskeleme hicbir sey
# BOZMASA BILE bu dosyalar hep "sozdizimi hatasi" olarak raporlanirdi (gercek
# projede dogrulandi: pip paketlerinin hic maskelenmemis orijinal LICENSE
# dosyalari bile bu kontrolden GECEMIYORDU). Bu yuzden kontrol SADECE
# asagidaki bilinen programlama dili uzantilarina uygulanir; listede
# olmayan HER SEY (md/rst/txt/LICENSE/Dockerfile/.dockerignore/uzantisiz
# dosyalar vb.) sozdizimi acisindan gecerli sayilir (None doner) - bu
# TokenBoundaryValidator bu dosyalarda span butunlugunu kontrol eder;
# dosya formatinin veya semantiginin korundugunu garanti etmez.
_BRACKET_QUOTE_LANGUAGE_SUFFIXES = {
    "java", "js", "jsx", "ts", "tsx", "go", "c", "h", "cpp", "hpp", "cc",
    "cs", "php", "rb", "rs", "kt", "kts", "swift", "scala", "groovy",
    "sql", "sh", "bash", "zsh", "ps1", "pl", "lua", "dart", "m", "mm",
}

# Her dil icin YORUM sinirlarini tanimlar - _check_bracket_and_quote_balance
# bir yorumun ICINDEYKEN tirnak/parantez saymaz. Bu tablo olmadan bir
# yorumun icindeki kesme isareti (orn. "// it's fine", "# don't touch this")
# yanlislikla acilmis bir string literal saniliyordu; gercek projede
# dogrulandi (bkz. modul docstring'indeki LICENSE dosyasi ornegi ile ayni
# sinif bir hata - farkla ki bu SEFER programlama dili UZANTILI dosyalarda,
# yorum SATIRLARINDA olusuyordu). Listede olmayan bir suffix icin bos tuple/
# liste donuyor - o dilde bilinen bir yorum sozdizimi yoksa hicbir sey
# atlanmaz, eski (yorum-farkinda OLMAYAN) davranis aynen surer.
_LINE_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "java": ("//",), "js": ("//",), "jsx": ("//",), "ts": ("//",), "tsx": ("//",),
    "go": ("//",), "c": ("//",), "h": ("//",), "cpp": ("//",), "hpp": ("//",), "cc": ("//",),
    "cs": ("//",), "rs": ("//",), "kt": ("//",), "kts": ("//",), "swift": ("//",),
    "scala": ("//",), "groovy": ("//",), "dart": ("//",), "m": ("//",), "mm": ("//",),
    "php": ("//", "#"),
    "sh": ("#",), "bash": ("#",), "zsh": ("#",), "ps1": ("#",), "pl": ("#",), "rb": ("#",),
    "sql": ("--",), "lua": ("--",),
}
_BLOCK_COMMENT_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "java": (("/*", "*/"),), "js": (("/*", "*/"),), "jsx": (("/*", "*/"),),
    "ts": (("/*", "*/"),), "tsx": (("/*", "*/"),), "go": (("/*", "*/"),),
    "c": (("/*", "*/"),), "h": (("/*", "*/"),), "cpp": (("/*", "*/"),),
    "hpp": (("/*", "*/"),), "cc": (("/*", "*/"),), "cs": (("/*", "*/"),),
    "php": (("/*", "*/"),), "rs": (("/*", "*/"),), "kt": (("/*", "*/"),),
    "kts": (("/*", "*/"),), "swift": (("/*", "*/"),), "scala": (("/*", "*/"),),
    "groovy": (("/*", "*/"),), "dart": (("/*", "*/"),), "m": (("/*", "*/"),),
    "mm": (("/*", "*/"),), "sql": (("/*", "*/"),), "lua": (("--[[", "]]"),),
}


class SyntaxValidationError(ValueError):
    """Basit denge kontrolcusunun (bracket/tirnak) basarisiz oldugunu belirtir."""


# 0-tabanli karakter ofsetini 1-tabanli (satir, sutun) ciftine cevirir -
# kullaniciya HANGI satirda oldugunu gosterebilmek icin (bkz. asagidaki
# raise'ler - "dosya X'te bir sorun var" yerine "dosya X, satir Y'de ŞU sorun var").
def _line_column(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    last_newline = text.rfind("\n", 0, pos)
    column = pos - last_newline
    return line, column


# Java/JS/Go gibi diller icin basit bir parantez/tirnak denge kontrolu yapar
# (string ve yorumlarin icindekileri saymadan). Her hata mesaji, kullanicinin
# dosyada nereye bakacagini bulabilmesi icin (satir, sutun) konumu tasir -
# "sozdizimi bozuldu" tek basina yeterli degil, TAM OLARAK NEREDE oldugu da
# gosterilmeli.
def _check_bracket_and_quote_balance(text: str, suffix: str) -> None:
    line_prefixes = _LINE_COMMENT_PREFIXES.get(suffix, ())
    block_pairs = _BLOCK_COMMENT_PAIRS.get(suffix, ())
    stack: list[tuple[str, int]] = []  # (karakter, acildigi ofset)
    in_string: str | None = None
    string_start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue

        # Blok yorum baslangici mi? Oyleyse kapanisina kadar (bulunamazsa
        # metnin sonuna kadar) hicbir tirnak/parantez saymadan atla.
        block_matched = False
        for opener, closer in block_pairs:
            if text.startswith(opener, i):
                end_idx = text.find(closer, i + len(opener))
                i = (end_idx + len(closer)) if end_idx != -1 else n
                block_matched = True
                break
        if block_matched:
            continue

        # Satir yorumu baslangici mi? Oyleyse bir sonraki satira (ya da
        # metnin sonuna) kadar atla.
        if any(text.startswith(prefix, i) for prefix in line_prefixes):
            newline = text.find("\n", i)
            i = newline if newline != -1 else n
            continue

        if ch in ("'", '"') or (ch == "`" and suffix in {"go", "js", "jsx", "ts", "tsx"}):
            in_string = ch
            string_start = i
            i += 1
            continue
        if ch in _BRACKET_OPENERS:
            stack.append((ch, i))
        elif ch in _BRACKET_CLOSERS:
            expected = _BRACKET_CLOSERS[ch]
            if not stack or stack[-1][0] != expected:
                line, column = _line_column(text, i)
                raise SyntaxValidationError(
                    f"dengesiz parantez: beklenmeyen '{ch}' (satir {line}, sutun {column})"
                )
            stack.pop()
        i += 1

    if in_string is not None:
        line, column = _line_column(text, string_start)
        raise SyntaxValidationError(
            f"kapatilmamis string literal ({in_string}) - baslangic: satir {line}, sutun {column}"
        )
    if stack:
        first_char, first_pos = stack[0]
        line, column = _line_column(text, first_pos)
        remaining = "".join(ch for ch, _pos in stack)
        raise SyntaxValidationError(
            f"kapatilmamis parantez(ler): {remaining} - ilki '{first_char}' satir {line}, sutun {column}"
        )


