"""Per-document lexical string spans; one scan, logarithmic interval lookup.

This is a boundary aid, not a language parser. Final syntax validation remains
mandatory. Offsets always refer to the unchanged source, including escapes.
"""

from bisect import bisect_left
from pathlib import Path

from app.services.syntax_validator import _BLOCK_COMMENT_PAIRS, _LINE_COMMENT_PREFIXES

# Python literal prefixes (r/b/u/f, any case, any valid 1-2 letter combo) that
# may legitimately sit directly in front of an opening quote - r'raw', rb'..',
# f'..', etc. Every other language handled here never has a real string
# delimiter directly preceded by a word character, so that position is always
# a contraction/possessive apostrophe (JSX text, comments-as-text, prose),
# never a string open (see _looks_like_quote_open).
_PY_STRING_PREFIX_CHARS = frozenset("rRbBuUfF")


def _is_word_char(char: str) -> bool:
    return char == "_" or char.isalnum()


def _looks_like_quote_open(text: str, pos: int, suffix: str) -> bool:
    """Reject a `'` that is really a contraction/possessive apostrophe.

    A real string-opening single quote is essentially never directly preceded
    by an identifier character (`admin'in izni`, `don't`, `kullanicinin'`).
    Python is the one exception in this table: r/b/u/f (and their 1-2 letter
    combinations) legitimately precede a quote as a string prefix, so that
    specific case is allowed back in.
    """
    if pos == 0 or not _is_word_char(text[pos - 1]):
        return True
    if suffix != "py":
        return False
    prefix_start = pos
    while prefix_start > 0 and pos - prefix_start < 2 and text[prefix_start - 1] in _PY_STRING_PREFIX_CHARS:
        prefix_start -= 1
    return prefix_start < pos and not (prefix_start > 0 and _is_word_char(text[prefix_start - 1]))


class StringLiteralIndex:
    def __init__(self, text: str, file_path: str = "") -> None:
        suffix = Path(file_path).suffix.lower().lstrip(".")
        line_comments = _LINE_COMMENT_PREFIXES.get(suffix, ())
        if suffix in {"py", "yaml", "yml", "toml"}:
            line_comments = ("#",)
        block_comments = _BLOCK_COMMENT_PAIRS.get(suffix, ())
        if suffix == "css":
            block_comments = (("/*", "*/"),)
        elif suffix == "xml":
            block_comments = (("<!--", "-->"),)
        self.spans: list[tuple[int, int, int, int]] = []
        self.comment_spans: list[tuple[int, int]] = []
        i = 0
        while i < len(text):
            comment_start = i
            block = next(((a, b) for a, b in block_comments if text.startswith(a, i)), None)
            if block is not None:
                end = text.find(block[1], i + len(block[0]))
                i = len(text) if end < 0 else end + len(block[1])
                self.comment_spans.append((comment_start, i))
                continue
            if any(text.startswith(prefix, i) for prefix in line_comments):
                end = text.find("\n", i)
                i = len(text) if end < 0 else end + 1
                self.comment_spans.append((comment_start, i))
                continue
            quote = text[i]
            if quote == "`" and suffix in {"js", "jsx", "ts", "tsx"}:
                # A ${...} interpolation is live code, not string content - it
                # must never be swallowed into (and then replaced as part of)
                # the surrounding template literal. See _scan_template_literal.
                i = self._scan_template_literal(text, i, suffix)
                continue
            if quote == '"' and suffix == "cs" and not text.startswith('"""', i):
                prefix = text[max(0, i - 2):i]
                if prefix.endswith("$") or prefix == "$@":
                    # $"..{expr}.." / $@"..": {expr} canli koddur, string
                    # icerigi degildir (bkz. _scan_cs_interpolated).
                    i = self._scan_cs_interpolated(text, i, verbatim="@" in prefix)
                    continue
                if prefix.endswith("@"):
                    # @"..": cok satirli olabilir, tirnak "" ile kacirilir ve
                    # ters bolu kacis karakteri degildir. Aksi halde @"..""a""..""" 
                    # sonundaki """ uc tirnakli string sanilip dosyanin geri
                    # kalanini yutar.
                    i = self._scan_cs_verbatim(text, i)
                    continue
            if quote not in "\"'" and not (quote == "`" and suffix == "go"):
                i += 1
                continue
            if quote == "'" and not _looks_like_quote_open(text, i, suffix):
                i += 1
                continue
            width = 3 if text.startswith(quote * 3, i) and quote != "`" else 1
            delimiter = quote * width
            quote_start = i
            content_start = i + width
            i = content_start
            literal_backslash = (suffix in {"toml", "yaml", "yml", "sql"} and quote == "'") or (suffix == "go" and quote == "`")
            while i < len(text):
                if text[i] == "\\" and not literal_backslash:
                    i += 2
                    continue
                if text.startswith(delimiter, i):
                    if width == 1 and quote == "'" and suffix in {"sql", "yaml", "yml"} and text.startswith("''", i):
                        i += 2
                        continue
                    self.spans.append((content_start, i, quote_start, i + width))
                    i += width
                    break
                if text[i] == "\n" and width == 1 and quote != "`" and suffix not in {"yaml", "yml", "sql"}:
                    # An unterminated ordinary string must not absorb the
                    # rest of a document (including prose apostrophes).
                    break
                i += 1
        self._starts = [span[2] for span in self.spans]
        self._comment_starts = [span[0] for span in self.comment_spans]

    def _scan_template_literal(self, text: str, start: int, suffix: str) -> int:
        """Index one backtick template literal as static-text spans around
        any ${...} interpolations, so a match inside an interpolation is
        never treated as "inside this string" (an interpolation is live
        code - see enclosing()/module docstring). Returns the index just
        past the literal, or len(text) if it is unterminated.
        """
        i = start + 1
        seg_start = i
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                if seg_start < i:
                    self.spans.append((seg_start, i, seg_start, i))
                return i + 1
            if ch == "$" and text.startswith("${", i):
                if seg_start < i:
                    self.spans.append((seg_start, i, seg_start, i))
                i = self._skip_interpolation(text, i + 2, suffix)
                seg_start = i
                continue
            i += 1
        if seg_start < i:
            self.spans.append((seg_start, i, seg_start, i))
        return i

    def _scan_cs_verbatim(self, text: str, start: int) -> int:
        i = start + 1
        while i < len(text):
            if text[i] == '"':
                if text.startswith('""', i):
                    i += 2
                    continue
                self.spans.append((start + 1, i, start, i + 1))
                return i + 1
            i += 1
        return i

    def _scan_cs_interpolated(self, text: str, start: int, *, verbatim: bool) -> int:
        """Index one C# interpolated string ($"..", $@"..", @$"..") as static
        text spans around its {expr} holes. Without this, the quotes inside a
        hole ($"TCKN: {dr["kimlikNo"]}\\n") are read as string boundaries and
        code such as `]}\\n` is mistaken for string content. `{{`/`}}` are
        literal braces; verbatim strings escape a quote as `""`.
        """
        i = start + 1
        seg_start = i
        while i < len(text):
            ch = text[i]
            if ch == "\\" and not verbatim:
                i += 2
                continue
            if ch == '"':
                if verbatim and text.startswith('""', i):
                    i += 2
                    continue
                if seg_start < i:
                    self.spans.append((seg_start, i, seg_start, i))
                return i + 1
            if ch in "{}" and text.startswith(ch * 2, i):
                i += 2
                continue
            if ch == "{":
                if seg_start < i:
                    self.spans.append((seg_start, i, seg_start, i))
                i = self._skip_interpolation(text, i + 1, "cs")
                seg_start = i
                continue
            if ch == "\n" and not verbatim:
                break
            i += 1
        if seg_start < i:
            self.spans.append((seg_start, i, seg_start, i))
        return i

    def _skip_interpolation(self, text: str, pos: int, suffix: str) -> int:
        """Skip a ${...} expression body starting right after '${'.

        Returns the index right after the matching '}'. Nested braces,
        strings and (recursively) nested template literals inside the
        expression are tracked so a '}' that belongs to a string/object
        literal never ends the interpolation early.
        """
        i = pos
        depth = 1
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch in "\"'":
                i = self._skip_simple_string(text, i)
                continue
            if ch == "`" and suffix != "cs":
                i = self._scan_template_literal(text, i, suffix)
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        return i

    @staticmethod
    def _skip_simple_string(text: str, start: int) -> int:
        quote = text[start]
        i = start + 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == quote:
                return i + 1
            if text[i] == "\n":
                break
            i += 1
        return i

    def enclosing(self, start: int, end: int) -> tuple[int, int, int, int] | None:
        # Preserve the historical rightmost-value choice for a detection
        # covering both a quoted JSON key and its quoted value.
        index = bisect_left(self._starts, end) - 1
        if index >= 0 and self.spans[index][3] > start:
            return self.spans[index]
        return None

    def in_comment(self, start: int, end: int) -> bool:
        """True if [start, end) falls ENTIRELY inside one recorded comment.

        A span that only partially overlaps a comment (a realistic detector
        should never produce one) is treated as NOT in a comment, so it
        falls back to the normal bare-code path rather than being silently
        accepted unexpanded.
        """
        index = bisect_left(self._comment_starts, start + 1) - 1
        if index < 0:
            return False
        comment_start, comment_end = self.comment_spans[index]
        return comment_start <= start and end <= comment_end
