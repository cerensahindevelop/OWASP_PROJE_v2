"""Offline, syntax-only parser adapters. Never execute source or resolve imports."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
import json
from threading import local
import tomllib
from xml.parsers.expat import ExpatError
from xml.etree.ElementTree import ParseError as XMLParseError


@dataclass(frozen=True)
class ParseResult:
    error: str | None = None
    unavailable: str | None = None
    value: object = None


class JSONObject(list):
    """Ordered member occurrences, including duplicate keys and masked keys."""


_parsers = local()


@lru_cache(maxsize=3)
def _language(suffix: str):
    from tree_sitter import Language
    if suffix in {"ts", "tsx"}:
        import tree_sitter_typescript as grammar
        return Language(grammar.language_tsx() if suffix == "tsx" else grammar.language_typescript())
    import tree_sitter_javascript as grammar
    return Language(grammar.language())


def _script_parser(suffix: str):
    from tree_sitter import Parser
    key = suffix if suffix in {"ts", "tsx"} else "js"
    if not hasattr(_parsers, "by_language"):
        _parsers.by_language = {}
    if key not in _parsers.by_language:
        _parsers.by_language[key] = Parser(_language(key))
    return _parsers.by_language[key]


def parse_script(suffix: str, text: str) -> ParseResult:
    try:
        parser = _script_parser(suffix)
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        return ParseResult(unavailable="Script parser yuklenemedi; bracket/quote kontrolu kullanildi.")
    root = parser.parse(text.encode("utf-8")).root_node
    if not root.has_error:
        return ParseResult()
    # Descend only into erroneous branches; don't materialize every AST node.
    cursor = root.walk()
    while not (cursor.node.is_error or cursor.node.is_missing):
        if not cursor.goto_first_child():
            break
        while not cursor.node.has_error and not cursor.node.is_missing:
            if not cursor.goto_next_sibling():
                break
        if not cursor.node.has_error and not cursor.node.is_missing:
            break
    row, column = cursor.node.start_point
    return ParseResult(error=f"{suffix.upper()} sozdizimi hatasi (satir {row + 1}, byte sutunu {column + 1})")


def parse_sql(text: str, dialect: str | None) -> ParseResult:
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError, TokenError, UnsupportedError
    except ImportError:
        return ParseResult(unavailable="SQL parser yuklenemedi; bracket/quote kontrolu kullanildi.")
    try:
        # Zero context also prevents SQLGlot's Command fallback logger from
        # disclosing source fragments. We never log exception text ourselves.
        statements = sqlglot.parse(text, read=dialect or None, error_message_context=0)
        if any(statement is not None and next(statement.find_all(exp.Command), None) is not None for statement in statements):
            return ParseResult(unavailable="SQL ifadesi parser kapsaminda degil; bracket/quote kontrolu kullanildi.")
    except (ParseError, TokenError) as exc:
        errors = getattr(exc, "errors", None) or []
        first = errors[0] if errors else {}
        line, column = first.get("line"), first.get("col")
        position = f" (satir {line}, sutun {column})" if line and column else ""
        return ParseResult(error=f"SQL sozdizimi hatasi{position}")
    except (UnsupportedError, ValueError):
        return ParseResult(unavailable="SQL lehcesi/ifadesi desteklenmiyor; bracket/quote kontrolu kullanildi.")
    return ParseResult()


def parse_document(suffix: str, text: str, *, sql_dialect: str | None = None) -> ParseResult:
    if suffix in {"ts", "tsx", "js", "jsx"}:
        return parse_script(suffix, text)
    if suffix == "sql":
        return parse_sql(text, sql_dialect)
    try:
        if suffix == "py":
            ast.parse(text)
        elif suffix == "json":
            return ParseResult(value=json.loads(text, object_pairs_hook=JSONObject))
        elif suffix == "toml":
            tomllib.loads(text)
        elif suffix in {"yaml", "yml"}:
            try:
                import yaml
            except ImportError:
                return ParseResult(unavailable="YAML parser yuklenemedi; yalniz structural kontrol mevcut.")
            try:
                yaml.safe_load(text)
            except yaml.YAMLError as exc:
                # problem_mark is where the parser gave up, which for a broken
                # key/value pair is often a line or more past the actual
                # defect; context_mark is where the enclosing construct (e.g.
                # the "simple key" scan) started, so surface both when they
                # differ instead of pointing only at the misleading one.
                # Never include exc.problem/exc.context text here - PyYAML
                # sometimes embeds the offending source character in those
                # strings, and we never disclose source fragments.
                problem_mark = getattr(exc, "problem_mark", None)
                context_mark = getattr(exc, "context_mark", None)
                if context_mark is not None and problem_mark is not None and context_mark.line != problem_mark.line:
                    position = (
                        f" (satir {context_mark.line + 1}'de baslayan yapi, "
                        f"satir {problem_mark.line + 1} sutun {problem_mark.column + 1}'de hataya yol acti)"
                    )
                else:
                    mark = problem_mark or context_mark
                    position = f" (satir {mark.line + 1}, sutun {mark.column + 1})" if mark is not None else ""
                return ParseResult(error=f"YAML sozdizimi hatasi{position}")
        elif suffix == "xml":
            try:
                from defusedxml.ElementTree import fromstring
                from defusedxml.common import DefusedXmlException
            except ImportError:
                return ParseResult(unavailable="XML parser yuklenemedi; yalniz structural kontrol mevcut.")
            try:
                # Declarations are permitted for compatibility; no entity or
                # external DTD is resolved, and no network/file I/O occurs.
                fromstring(text, forbid_entities=True, forbid_external=True)
            except DefusedXmlException:
                return ParseResult(error="XML guvenli parser politikasi: entity/cozumleme desteklenmiyor")
    except (XMLParseError, ExpatError) as exc:
        row, column = getattr(exc, "position", (getattr(exc, "lineno", 0), getattr(exc, "offset", 0)))
        return ParseResult(error=f"XML sozdizimi hatasi (satir {row}, sutun {column + 1})")
    except SyntaxError as exc:
        return ParseResult(error=f"Python sozdizimi hatasi (satir {exc.lineno}, sutun {exc.offset})")
    except json.JSONDecodeError as exc:
        return ParseResult(error=f"JSON sozdizimi hatasi (satir {exc.lineno}, sutun {exc.colno})")
    except tomllib.TOMLDecodeError as exc:
        position = f" (satir {exc.lineno}, sutun {exc.colno})" if hasattr(exc, "lineno") else ""
        return ParseResult(error=f"TOML sozdizimi hatasi{position}")
    except (ValueError, RecursionError, OverflowError):
        return ParseResult(error=f"{suffix.upper()} parser siniri veya gecersiz icerik")
    return ParseResult()


def _json_type(value: object) -> str:
    if isinstance(value, JSONObject):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def compare_json_types(original: object, masked: object) -> str | None:
    # Edits preserve source member order. Match occurrences by position, not
    # by key spelling (keys themselves may be masked). Duplicate keys remain
    # visible rather than being discarded by dict construction.
    stack = [(original, masked, "$")]
    while stack:
        before, after, path = stack.pop()
        before_type, after_type = _json_type(before), _json_type(after)
        if before_type != after_type:
            return f"JSON veri tipi degisti ({path}): {before_type} -> {after_type}"
        if before_type in {"object", "array"}:
            if len(before) != len(after):
                return f"JSON yapisi degisti ({path}): oge sayisi farkli"
            for index in range(len(before) - 1, -1, -1):
                left, right = before[index], after[index]
                if before_type == "object":
                    left, right = left[1], right[1]
                stack.append((left, right, f"{path}/{before_type}[{index}]"))
    return None
