"""Post-mask validation with explicit parser, lexical and structural routes.

The legacy str-or-None API remains available. Diagnostics separately report
fallbacks and inherited source errors; an unavailable parser is never reported
as a successful parser check. No adapter executes the submitted source.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import re

# Re-export legacy helpers for callers such as StringLiteralIndex.
from app.services.syntax_brackets import (
    _BLOCK_COMMENT_PAIRS,
    _BRACKET_QUOTE_LANGUAGE_SUFFIXES,
    _LINE_COMMENT_PREFIXES,
    _check_bracket_and_quote_balance,
    SyntaxValidationError,
)
from app.services.syntax_parsers import ParseResult, compare_json_types, parse_document

logger = logging.getLogger(__name__)
_PARSER_SUFFIXES = {"py", "json", "yaml", "yml", "toml", "xml", "ts", "tsx", "js", "jsx", "sql"}


@dataclass(frozen=True)
class SyntaxResult:
    error: str | None
    mode: str
    warnings: tuple[str, ...] = ()


def validation_mode(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    if suffix in _PARSER_SUFFIXES:
        return "parser-based"
    if suffix in _BRACKET_QUOTE_LANGUAGE_SUFFIXES:
        return "bracket/quote-based"
    return "structural-only"


# Hata mesajlarindaki sutun numarasi ("sutun 12", "byte sutunu 12").
_COLUMN_NUMBER_RE = re.compile(r"(sutunu?) \d+")


# Iki hata mesajinin AYNI hatayi (tur + satir) gosterip gostermedigini soyler.
# Sutun, ayni satirda hatadan once maskelenen bir degerin uzunlugu kadar
# kayar; bu yuzden karsilastirmadan cikarilir. Satir KORUNUR: placeholder'lar
# tek satirdir, maskeleme satir kaydirmaz - kaynak zaten bozukken maskelemenin
# BASKA bir satirda yarattigi yeni hata boylece hala yakalanir.
def _same_error(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return _COLUMN_NUMBER_RE.sub(r"\1 #", a) == _COLUMN_NUMBER_RE.sub(r"\1 #", b)


# Parantez/tirnak denetleyicisinin karar verirken baktigi TEK seyler: tirnak,
# parantez, ters bolu, satir sonu ve dilin yorum ayraclari. En uzun ayrac
# once denenir ("--[[" once "--"), boylece "//" tek bir ayrac sayilir.
@lru_cache(maxsize=None)
def _skeleton_pattern(suffix: str) -> re.Pattern[str]:
    delimiters = set(_LINE_COMMENT_PREFIXES.get(suffix, ()))
    for opener, closer in _BLOCK_COMMENT_PAIRS.get(suffix, ()):
        delimiters.update((opener, closer))
    parts = [re.escape(delimiter) for delimiter in sorted(delimiters, key=len, reverse=True)]
    parts.append(r"[\"'`(){}\[\]\\\n]")
    return re.compile("|".join(parts))


# Metnin yapisal iskeleti: denetleyicinin gordugu ayraclarin sirali dizisi.
# Iki metnin iskeleti ayniysa denetleyici ikisinde de AYNI yoldan gecer;
# aralarindaki fark yalnizca ayrac-disi metindir (maskelenen deger).
def _structural_skeleton(suffix: str, text: str) -> tuple[str, ...]:
    return tuple(_skeleton_pattern(suffix).findall(text))


def _bracket_error(suffix: str, text: str) -> str | None:
    if suffix not in _BRACKET_QUOTE_LANGUAGE_SUFFIXES:
        return None
    try:
        _check_bracket_and_quote_balance(text, suffix)
    except SyntaxValidationError as exc:
        return f"Sozdizimi hatasi: {exc}"
    return None


def inspect_masked_syntax(
    relative_path: str,
    masked_text: str,
    original_text: str | None = None,
    *,
    sql_dialect: str | None = None,
) -> SyntaxResult:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    mode = validation_mode(relative_path)
    if mode == "structural-only":
        # The existing upstream token/round-trip checks remain authoritative.
        notices = ("Bu dosya turu icin parser yok; yalnizca metin/geri donus butunlugu kontrol edildi.",) if masked_text != original_text else ()
        return SyntaxResult(None, mode, notices)
    notices: list[str] = []
    if mode == "bracket/quote-based" and masked_text != original_text:
        notices.append("Bu dil icin tam parser yok; bracket/quote kontrolu derleme veya calisma dogrulamasi degildir.")
    original: ParseResult | None = None
    masked: ParseResult | None = None
    if mode == "parser-based":
        # Parse the source once when available. SQL source coverage determines
        # whether the two documents can be compared with this parser at all.
        if original_text is not None:
            original = parse_document(suffix, original_text, sql_dialect=sql_dialect)
        if suffix == "sql" and original is not None and (original.error or original.unavailable):
            notices.append(original.unavailable or "Kaynak SQL secilen lehcede parse edilemedi; bracket/quote kontrolu kullanildi.")
        else:
            masked = original if original_text == masked_text and original is not None else parse_document(suffix, masked_text, sql_dialect=sql_dialect)
            unavailable = (original.unavailable if original else None) or masked.unavailable
            if unavailable:
                notices.append(unavailable)
            else:
                if original is not None and original.error:
                    if masked.error and not (
                        _same_error(masked.error, original.error) and masked.kind == original.kind
                    ):
                        # A pre-existing parse error does not excuse a
                        # DIFFERENT one: the source being broken never proves
                        # masking introduced no new breakage (see module
                        # docstring policy) - only the same error (parser error
                        # kind + line; column may shift, bkz. _same_error) is
                        # evidence of the SAME pre-existing problem.
                        return SyntaxResult(masked.error, mode)
                    notices.append("Kaynak dosya zaten parser hatasi iceriyor; yeni bozulma olmadigi garanti edilemez.")
                    return SyntaxResult(None, mode, tuple(notices))
                if masked.error:
                    return SyntaxResult(masked.error, mode)
                if suffix == "json":
                    if original is None:
                        notices.append("JSON kaynak metni yok; veri tipi karsilastirmasi yapilamadi.")
                    else:
                        return SyntaxResult(compare_json_types(original.value, masked.value), mode)
                return SyntaxResult(None, mode, tuple(notices))
        mode = "bracket/quote-based" if suffix in _BRACKET_QUOTE_LANGUAGE_SUFFIXES else "structural-only"
    error = _bracket_error(suffix, masked_text)
    if error and original_text is not None and (
        _structural_skeleton(suffix, original_text) == _structural_skeleton(suffix, masked_text)
        or _same_error(_bracket_error(suffix, original_text), error)
    ):
        # Same rule as the parser-based route above: only the SAME
        # pre-existing error is suppressed. A different bracket/quote error
        # (or a new one where the source had none) still blocks export.
        # Kesin kisayol: yapisal iskelet ayniysa denetleyici iki metinde de
        # ayni yoldan gecer; maskeleme yeni bir parantez/tirnak hatasi
        # uretemez, hata kaynaktan gelir.
        notices.append("Kaynak dosya zaten ayni bracket/quote hatasini iceriyor; mevcut kaynak hatasi politikasi uygulandi.")
        error = None
    return SyntaxResult(error, mode, tuple(notices))


def validate_masked_syntax(
    relative_path: str,
    masked_text: str,
    original_text: str | None = None,
    *,
    sql_dialect: str | None = None,
    diagnostics: list[str] | None = None,
) -> str | None:
    result = inspect_masked_syntax(relative_path, masked_text, original_text, sql_dialect=sql_dialect)
    for notice in result.warnings:
        message = f"validation_mode={result.mode}; {notice}"
        if diagnostics is not None:
            diagnostics.append(message)
        else:
            logger.warning("%s", message)
    return result.error


def _syntax_error_for(suffix: str, text: str) -> str | None:
    """Compatibility adapter for older direct callers."""
    return validate_masked_syntax(f"file.{suffix}", text)
