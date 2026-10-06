"""Tablolu veri (CSV/TSV, SQL INSERT dokumleri, JSON/NDJSON) icin LLM tarama plani.

Buyuk bir veri dokumunde her satir farkli bir kisi/adres icerdigi icin satir
tekrari ayiklamasi kazanc saglamaz. Bunun yerine tablo sutun bazinda taranir:

1. Ornek tur: tablodan dagitilmis birkac satir LLM'e gosterilir. LLM bir
   hucrenin TAMAMINI hassas bulursa (orn. `ad_soyad` = `Ayse Demir`) o sutunun
   butun hucreleri ayni tiple maskelenir; LLM onlari tek tek okumaz.
2. Kalan sutunlar atlanmaz: her birinin farkli degerleri (yalnizca rakamlari
   farkli olanlar bir kez) sutun adiyla LLM'e okutulur. Ornege denk gelmeyen
   seyrek bir deger de boylece gorulur.
3. Tablo disi metin (CREATE TABLE, yorumlar) satir sablonuyla bir kez taranir.

Ayristirma basarisiz olursa ya da tablo kucukse dosya normal yoldan taranir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.text_chunking import DedupedText, digit_key

# Bu kadar satiri olmayan tablo icin sutun plani kurulmaz (normal tarama yeterli).
MIN_TABLE_ROWS = 30
# Ornek turda LLM'e gosterilen, tabloya esit aralikla dagitilmis satir sayisi.
SAMPLE_ROWS = 25
_CSV_SUFFIXES = {".csv": None, ".tsv": "\t"}
_JSON_LINES_SUFFIXES = {".jsonl", ".ndjson"}
_CSV_DELIMITERS = (",", ";", "\t", "|")
_SQL_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_INSERT_RE = re.compile(
    r"INSERT\s+(?:IGNORE\s+)?INTO\s+((?:[`\"\[]?[\w$]+[`\"\]]?\.)?[`\"\[]?[\w$]+[`\"\]]?)"
    r"\s*(?:\(([^()]*)\))?\s*VALUES\s*",
    re.IGNORECASE,
)
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:[`\"\[]?[\w$]+[`\"\]]?\.)?[`\"\[]?[\w$]+[`\"\]]?)\s*\(",
    re.IGNORECASE,
)
_CONSTRAINT_WORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "KEY", "CONSTRAINT", "CHECK", "INDEX", "FULLTEXT",
                     "SPATIAL", "EXCLUDE"}


@dataclass(frozen=True)
class Cell:
    start: int
    end: int


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[list[Cell | None]] = field(default_factory=list)

    def label(self, column: int) -> str:
        return f"{self.name}.{self.columns[column]}"


@dataclass(frozen=True)
class ColumnDecision:
    """Ornek turda hucrenin tamami hassas bulunan sutun: tum hucreleri maskelenir."""

    tip: str
    guven_seviyesi: str
    gerekce: str


def _strip_name(name: str) -> str:
    return re.sub(r"[`\"\[\]]", "", name).strip()


def _one_line(value: str) -> str:
    # Uzunluk korunur: tekrarsiz metindeki konum orijinal hucreyle birebir eslesir.
    return value.replace("\r", " ").replace("\n", " ")


# --------------------------------------------------------------------------
# Ayristirma
# --------------------------------------------------------------------------

def find_tables(text: str, file_path: str | None) -> list[Table]:
    suffix = Path(file_path or "").suffix.lower()
    if suffix in _CSV_SUFFIXES:
        tables = _csv_tables(text, _CSV_SUFFIXES[suffix], Path(file_path or "").name)
    elif suffix == ".sql":
        tables = _sql_tables(text)
    elif suffix == ".json":
        tables = _json_tables(text, lines=False)
    elif suffix in _JSON_LINES_SUFFIXES:
        tables = _json_tables(text, lines=True)
    else:
        return []
    return [table for table in tables if len(table.rows) >= MIN_TABLE_ROWS]


def _csv_records(text: str, delimiter: str):
    position, length = 0, len(text)
    record: list[Cell] = []
    while position < length:
        if text[position] == '"':
            start = position + 1
            index = start
            while index < length:
                if text[index] == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        index += 2
                        continue
                    break
                index += 1
            record.append(Cell(start, min(index, length)))
            position = index + 1
            while position < length and text[position] not in (delimiter, "\n"):
                position += 1
        else:
            start = position
            while position < length and text[position] not in (delimiter, "\n"):
                position += 1
            end = position - 1 if position > start and text[position - 1] == "\r" else position
            record.append(Cell(start, end))
        if position < length and text[position] == delimiter:
            position += 1
            if position >= length:
                record.append(Cell(position, position))
            continue
        position += 1  # satir sonu
        if any(cell.end > cell.start for cell in record):
            yield record
        record = []
    if any(cell.end > cell.start for cell in record):
        yield record


def csv_delimiter(text: str, suffix: str) -> str:
    """`.tsv` icin sekme; `.csv` icin ilk satirda en sik gecen ayirici."""
    if _CSV_SUFFIXES.get(f".{suffix.lstrip('.')}"):
        return _CSV_SUFFIXES[f".{suffix.lstrip('.')}"]
    first_line = text.split("\n", 1)[0]
    return max(_CSV_DELIMITERS, key=first_line.count)


def csv_record_shape(text: str, delimiter: str) -> list[int]:
    """Her kaydin alan sayisi (tirnak icindeki ayirici ve satir sonlari dahil dogru sayilir)."""
    return [len(record) for record in _csv_records(text, delimiter)]


def _csv_tables(text: str, delimiter: str | None, name: str) -> list[Table]:
    first_line = text.split("\n", 1)[0]
    if delimiter is None:
        delimiter = max(_CSV_DELIMITERS, key=first_line.count)
    if not first_line.count(delimiter):
        return []
    records = list(_csv_records(text, delimiter))
    if len(records) < 2:
        return []
    header = [text[cell.start:cell.end].strip() or f"kolon_{index + 1}" for index, cell in enumerate(records[0])]
    rows = [record for record in records[1:] if len(record) == len(header)]
    # Duzensiz satiri cok olan dosya tablo sayilmaz; az sayidaki duzensiz satir
    # normal yoldan taranir (hucre olarak isaretlenmez).
    if len(rows) < 0.9 * (len(records) - 1):
        return []
    return [Table(name=name, columns=header, rows=rows)]


def _skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _matching_paren(text: str, position: int) -> int:
    depth, quote = 0, None
    for index in range(position, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(body: str) -> list[str]:
    parts, depth, quote, current = [], 0, None, []
    for char in body:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _create_table_columns(text: str) -> dict[str, list[str]]:
    columns: dict[str, list[str]] = {}
    for match in _CREATE_RE.finditer(text):
        close = _matching_paren(text, match.end() - 1)
        if close < 0:
            continue
        names = []
        for part in _split_top_level(text[match.end():close]):
            words = part.strip().split()
            if words and _strip_name(words[0]).upper() not in _CONSTRAINT_WORDS:
                names.append(_strip_name(words[0]))
        columns[_strip_name(match.group(1)).lower()] = names
    return columns


def _sql_value(text: str, position: int) -> tuple[Cell | None, int] | None:
    """Tek bir VALUES degerini okur; (hucre ya da None, sonraki konum) doner."""
    length = len(text)
    if position + 1 < length and text[position] in "NnEe" and text[position + 1] == "'":
        position += 1
    if position < length and text[position] == "'":
        start = index = position + 1
        while index < length:
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == "'":
                if index + 1 < length and text[index + 1] == "'":
                    index += 2
                    continue
                return Cell(start, index), index + 1
            index += 1
        return None
    start, depth, index = position, 0, position
    while index < length:
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        index += 1
    raw = text[start:index]
    token = raw.strip()
    if _SQL_NUMBER_RE.fullmatch(token):
        token_start = start + raw.index(token)
        return Cell(token_start, token_start + len(token)), index
    return (None, index) if index < length else None  # NULL, fonksiyon cagrisi vb.


def _sql_tuples(text: str, position: int) -> list[list[Cell | None]]:
    rows: list[list[Cell | None]] = []
    while True:
        position = _skip_space(text, position)
        if position >= len(text) or text[position] != "(":
            return rows
        position += 1
        row: list[Cell | None] = []
        while True:
            parsed = _sql_value(text, _skip_space(text, position))
            if parsed is None:
                return rows
            cell, position = parsed
            row.append(cell)
            position = _skip_space(text, position)
            if position < len(text) and text[position] == ",":
                position += 1
                continue
            if position < len(text) and text[position] == ")":
                position += 1
                break
            return rows
        rows.append(row)
        position = _skip_space(text, position)
        if position < len(text) and text[position] == ",":
            position += 1
            continue
        return rows


def _sql_tables(text: str) -> list[Table]:
    declared = _create_table_columns(text)
    tables: dict[tuple[str, int], Table] = {}
    for match in _INSERT_RE.finditer(text):
        name = _strip_name(match.group(1))
        explicit = [_strip_name(part) for part in match.group(2).split(",")] if match.group(2) else None
        for row in _sql_tuples(text, match.end()):
            width = len(row)
            columns = explicit or declared.get(name.lower()) or []
            if len(columns) != width:
                columns = [f"kolon_{index + 1}" for index in range(width)]
            table = tables.setdefault((name.lower(), width), Table(name=name, columns=columns))
            table.rows.append(row)
    return list(tables.values())


# JSON: konum bilgisi tutan kucuk bir okuyucu. Dugumler: dict (nesne), list
# (dizi), Cell (metin/sayi; metin icin tirnaklar haric ham icerik), None
# (true/false/null). Her nesne dizisi bir tablo, her ogesi bir satir, her
# skaler alani (ic ice nesnelerde `adres.il`) bir sutundur; ic ice diziler
# kendi tablolarini olusturur (`$.ogrenciler[].dersler[]`).
_JSON_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_JSON_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_JSON_LITERAL_RE = re.compile(r"true|false|null")


class _JsonError(ValueError):
    pass


def _json_value(text: str, position: int):
    position = _skip_space(text, position)
    if position >= len(text):
        raise _JsonError("beklenmeyen son")
    char = text[position]
    if char == "{":
        members: dict = {}
        position = _skip_space(text, position + 1)
        if position < len(text) and text[position] == "}":
            return members, position + 1
        while True:
            match = _JSON_STRING_RE.match(text, _skip_space(text, position))
            if match is None:
                raise _JsonError("anahtar bekleniyor")
            position = _skip_space(text, match.end())
            if position >= len(text) or text[position] != ":":
                raise _JsonError("':' bekleniyor")
            members[text[match.start() + 1:match.end() - 1]], position = _json_value(text, position + 1)
            position = _skip_space(text, position)
            if position < len(text) and text[position] == ",":
                position += 1
                continue
            if position < len(text) and text[position] == "}":
                return members, position + 1
            raise _JsonError("',' ya da '}' bekleniyor")
    if char == "[":
        items: list = []
        position = _skip_space(text, position + 1)
        if position < len(text) and text[position] == "]":
            return items, position + 1
        while True:
            item, position = _json_value(text, position)
            items.append(item)
            position = _skip_space(text, position)
            if position < len(text) and text[position] == ",":
                position += 1
                continue
            if position < len(text) and text[position] == "]":
                return items, position + 1
            raise _JsonError("',' ya da ']' bekleniyor")
    if char == '"':
        match = _JSON_STRING_RE.match(text, position)
        if match is None:
            raise _JsonError("kapanmayan metin")
        return Cell(match.start() + 1, match.end() - 1), match.end()
    match = _JSON_NUMBER_RE.match(text, position) or _JSON_LITERAL_RE.match(text, position)
    if match is None:
        raise _JsonError("gecersiz deger")
    return (Cell(match.start(), match.end()) if char not in "tfn" else None), match.end()


def _json_tables(text: str, *, lines: bool) -> list[Table]:
    roots: list = []
    try:
        if lines:
            position = 0
            for line in text.splitlines(keepends=True):
                if line.strip():
                    node, _end = _json_value(text, position)
                    roots.append(node)
                position += len(line)
            root = roots
        else:
            root, end = _json_value(text, 0)
            if _skip_space(text, end) != len(text):
                return []
    except _JsonError:
        return []

    tables: dict[str, Table] = {}

    def add_row(path: str, leaves: dict[str, Cell | None]) -> None:
        table = tables.setdefault(path, Table(name=path, columns=[]))
        for name in leaves:
            if name not in table.columns:
                table.columns.append(name)
                for row in table.rows:
                    row.append(None)
        table.rows.append([leaves.get(name) for name in table.columns])

    def flatten(node: dict, prefix: str, leaves: dict, element_path: str) -> None:
        for key, child in node.items():
            name = f"{prefix}{key}"
            if isinstance(child, dict):
                flatten(child, f"{name}.", leaves, element_path)
            elif isinstance(child, list):
                walk(child, f"{element_path}.{name}")
            else:
                leaves[name] = child

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            element_path = f"{path}[]"
            for element in node:
                if isinstance(element, dict):
                    leaves: dict[str, Cell | None] = {}
                    flatten(element, "", leaves, element_path)
                    if leaves:
                        add_row(element_path, leaves)
                elif isinstance(element, list):
                    walk(element, element_path)
                elif element is not None:
                    add_row(element_path, {"deger": element})

    walk(root, "$")
    return list(tables.values())


# --------------------------------------------------------------------------
# Ornek tur ve sutun karari
# --------------------------------------------------------------------------

def sample_rows(table: Table) -> list[int]:
    count = len(table.rows)
    if count <= SAMPLE_ROWS:
        return list(range(count))
    step = count / SAMPLE_ROWS
    return sorted({int(index * step) for index in range(SAMPLE_ROWS)})


def render_sample(text: str, tables: list[Table]) -> str:
    lines = []
    for table in tables:
        lines.append(f"# tablo {table.name}: {', '.join(table.columns)}\n")
        for row_index in sample_rows(table):
            cells = [
                f"{table.columns[column]}={_one_line(text[cell.start:cell.end])}"
                for column, cell in enumerate(table.rows[row_index]) if cell is not None
            ]
            lines.append(" | ".join(cells) + "\n")
    return "".join(lines)


_CONFIDENCE_RANK = {"dusuk": 0, "orta": 1, "yuksek": 2}


# Deger hucrede kelime sinirlariyla mi geciyor? `Ayse Demir`, `Ayse Demiroglu`
# hucresinin parcasi sayilmaz (ayri bir ad), `Ayse Demir ile gorusuldu` sayilir.
def _contains_word(content: str, value: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(value)}(?!\w)", content) is not None


def classify_columns(
    text: str, tables: list[Table], findings: list[tuple[str, str, str, str]],
) -> dict[tuple[int, int], ColumnDecision]:
    """Ornek turdaki bulgulardan, tum hucreleri maskelenecek sutunlari secer.

    findings: (deger, tip, guven_seviyesi, gerekce). Bir sutun, ornekte en az
    bir hucresinin TAMAMI hassas bulunduysa ve hicbir hucresinde yalnizca bir
    PARCA bulunmadiysa secilir. Parca bulunan (serbest metin) sutunlar secilmez;
    onlarin farkli degerleri ayrica LLM'e okutulur.
    """
    whole: dict[tuple[int, int], ColumnDecision] = {}
    partial: set[tuple[int, int]] = set()
    for table_index, table in enumerate(tables):
        for row_index in sample_rows(table):
            for column, cell in enumerate(table.rows[row_index]):
                if cell is None:
                    continue
                content = text[cell.start:cell.end].strip()
                if not content:
                    continue
                for value, tip, confidence, reason in findings:
                    value = value.strip()
                    if not value:
                        continue
                    key = (table_index, column)
                    if value == content:
                        previous = whole.get(key)
                        if previous is None or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[previous.guven_seviyesi]:
                            whole[key] = ColumnDecision(tip, confidence, reason)
                    elif _contains_word(content, value):
                        partial.add(key)
    return {key: decision for key, decision in whole.items() if key not in partial}


def column_cells(tables: list[Table], decisions: dict[tuple[int, int], ColumnDecision]):
    """Secilen sutunlarin her hucresi: (tablo, sutun, hucre, karar)."""
    for (table_index, column), decision in decisions.items():
        table = tables[table_index]
        for row in table.rows:
            cell = row[column]
            if cell is not None and cell.end > cell.start:
                yield table, column, cell, decision


# --------------------------------------------------------------------------
# Yogunlastirilmis tarama metni
# --------------------------------------------------------------------------

def build_condensed(
    text: str, tables: list[Table], decisions: dict[tuple[int, int], ColumnDecision],
) -> DedupedText:
    """LLM'e gidecek metin: tablo disi satirlar + secilmeyen sutunlarin farkli degerleri.

    Hucre iceren satirlar hucreleri `?` yapilmis sablon olarak bir kez gider
    (tablo/sutun adlari yine taranir); karsiliklari yoktur, bulgu metinde
    birebir aranir. Sutun degeri satirlari ise ayni sutundaki, yalnizca
    rakamlari farkli hucrelerin tamamini temsil eder.
    """
    cells = sorted(
        (cell for table in tables for row in table.rows for cell in row if cell is not None),
        key=lambda cell: cell.start,
    )
    kept: list[str] = []
    line_starts: list[int] = []
    members: list[tuple[tuple[int, int], ...]] = []
    classes: dict[str, int] = {}
    kept_length = 0

    def add(line: str, key: str | None, span: tuple[int, int] | None) -> None:
        nonlocal kept_length
        if key is not None and key in classes:
            if span is not None:
                index = classes[key]
                members[index] = members[index] + (span,)
            return
        if key is not None:
            classes[key] = len(kept)
        kept.append(line)
        line_starts.append(kept_length)
        members.append((span,) if span is not None else ())
        kept_length += len(line)

    position, cell_index = 0, 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_start, line_end = position, position + len(content)
        position += len(line)
        while cell_index < len(cells) and cells[cell_index].end <= line_start and cells[cell_index].start < line_start:
            cell_index += 1
        overlapping = []
        probe = cell_index
        while probe < len(cells) and cells[probe].start <= line_end:
            if cells[probe].end >= line_start:
                overlapping.append(cells[probe])
            probe += 1
        if not overlapping:
            add(line, "L:" + digit_key(content), (line_start, line_end))
            continue
        pieces, cursor = [], line_start
        for cell in overlapping:
            start, end = max(cell.start, line_start), min(cell.end, line_end)
            pieces.append(text[cursor:start] + "?")
            cursor = max(cursor, end)
        pieces.append(text[cursor:line_end])
        template = "".join(pieces)
        add(template + "\n", "T:" + digit_key(template), None)

    for table_index, table in enumerate(tables):
        for column in range(len(table.columns)):
            if (table_index, column) in decisions:
                continue
            header_key = f"H:{table_index}:{column}"
            add(f"## {table.label(column)}\n", header_key, None)
            for row in table.rows:
                cell = row[column] if column < len(row) else None
                if cell is None or not text[cell.start:cell.end].strip():
                    continue
                value = _one_line(text[cell.start:cell.end])
                add(value + "\n", f"C:{table_index}:{column}:{digit_key(value)}", (cell.start, cell.end))
    return DedupedText("".join(kept), tuple(line_starts), tuple(members))
