"""Tablolu veride (CSV/TSV, SQL INSERT) sutun bazli LLM taramasi."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.llm_recognizer import find_llm_detections
from app.services.tabular_scan import (
    MIN_TABLE_ROWS, build_condensed, classify_columns, find_tables, render_sample,
)

_FIRST = ["Ayse", "Mehmet", "Zeynep", "Ali", "Elif", "Can", "Deniz", "Selin", "Burak", "Ece", "Kaan", "Derya"]
_LAST = ["Demir", "Kaya", "Arslan", "Yilmaz", "Sahin", "Celik", "Aydin", "Ozturk", "Kilic", "Dogan"]
_RARE_NAME = "Hakan Gizlioglu"


def _name(i: int) -> str:
    return f"{_FIRST[i % len(_FIRST)]} {_LAST[(i * 7) % len(_LAST)]}{'' if i < 120 else 'oglu'}"


def _sql_dump(rows: int = 400) -> str:
    out = [
        "-- ogrenci dokumu\n",
        "CREATE TABLE ogrenci (\n  numara VARCHAR(10) PRIMARY KEY,\n  ad_soyad TEXT NOT NULL,\n"
        "  durum TEXT,\n  notlar TEXT\n);\n",
    ]
    for i in range(rows):
        note = f"{_RARE_NAME} ile gorusuldu" if i == 287 else ("burs basvurusu" if i % 3 else "")
        out.append(f"INSERT INTO ogrenci VALUES ('{2021000 + i}', '{_name(i)}', '{'AKTIF' if i % 4 else 'PASIF'}', '{note}');\n")
    return "".join(out)


def _settings(**overrides):
    defaults = dict(
        enabled=True, host="http://localhost:8000", model="test-model", api_key=None,
        timeout_seconds=5.0, max_file_chars=6_000, chunk_overlap_chars=200, seed=42,
        redact_known_findings=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _response(findings: list[dict]) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"bulgular": findings})}, "finish_reason": "stop"}]}


def test_sql_tables_use_create_table_columns_and_parse_values():
    text = (
        "CREATE TABLE t (id INT, ad TEXT, aciklama TEXT, PRIMARY KEY (id));\n"
        + "".join(f"INSERT INTO t VALUES ({i}, 'O''Brien {i}', NULL);\n" for i in range(MIN_TABLE_ROWS))
    )
    [table] = find_tables(text, "dump.sql")
    assert table.columns == ["id", "ad", "aciklama"]
    first = table.rows[0]
    assert text[first[0].start:first[0].end] == "0"
    assert text[first[1].start:first[1].end] == "O''Brien 0"
    assert first[2] is None  # NULL hucre degildir


def test_sql_multi_row_insert_with_explicit_columns():
    values = ", ".join(f"('{i}', 'Kisi {i}')" for i in range(MIN_TABLE_ROWS))
    text = f"INSERT INTO `kisi` (`no`, `ad`) VALUES {values};\n"
    [table] = find_tables(text, "dump.sql")
    assert table.columns == ["no", "ad"] and len(table.rows) == MIN_TABLE_ROWS


def test_csv_handles_quoted_delimiters_and_newlines():
    rows = "".join(f'{i};"Demir, Ayse {i}";"satir1\nsatir2"\n' for i in range(MIN_TABLE_ROWS))
    text = "no;ad;not\n" + rows
    [table] = find_tables(text, "liste.csv")
    assert table.columns == ["no", "ad", "not"]
    cell = table.rows[3][1]
    assert text[cell.start:cell.end] == "Demir, Ayse 3"
    assert "\n" in text[table.rows[0][2].start:table.rows[0][2].end]


def test_small_or_non_tabular_files_are_not_planned():
    assert find_tables("INSERT INTO t VALUES (1, 'a');\n", "a.sql") == []
    assert find_tables(_sql_dump(), "notlar.txt") == []


def test_whole_cell_finding_selects_column_partial_finding_does_not():
    text = _sql_dump()
    tables = find_tables(text, "dump.sql")
    findings = [(_name(0), "PERSON", "yuksek", "kisi"), ("burs", "KURUM_JARGONU", "dusuk", "x")]
    decisions = classify_columns(text, tables, findings)
    assert list(decisions) == [(0, 1)]  # yalnizca ad_soyad; notlar serbest metin
    condensed = build_condensed(text, tables, decisions)
    assert "## ogrenci.ad_soyad" not in condensed.text
    assert "## ogrenci.notlar" in condensed.text and _RARE_NAME in condensed.text
    assert condensed.text.count("AKTIF") == 1  # dusuk cesitlilikli sutun bir kez


def test_detection_masks_column_and_finds_rare_value_with_few_requests(monkeypatch):
    text = _sql_dump()
    sent = []

    async def _fake_call(host, timeout, payload, api_key=None):
        chunk = payload["messages"][-1]["content"]
        sent.append(chunk)
        found = []
        for line in chunk.splitlines():
            if line.startswith("numara="):  # ornek tur: ad_soyad hucresi
                found.append({"bulunan_deger": line.split("ad_soyad=")[1].split(" |")[0],
                              "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "kisi"})
        if _RARE_NAME in chunk:
            found.append({"bulunan_deger": _RARE_NAME, "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "kisi"})
        return _response(found)

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)

    detections = asyncio.run(find_llm_detections(text, [], _settings(), metadata={"file_path": "db/dump.sql"}))

    masked = [text[d.start:d.end] for d in detections]
    assert sorted(masked.count(_name(i)) >= 1 for i in range(400)) == [True] * 400  # her satirin adi
    assert _RARE_NAME in masked  # ornekte olmayan seyrek deger de bulundu
    assert all(d.raw_result.get("sutun") == "ogrenci.ad_soyad" for d in detections if d.deger != _RARE_NAME)
    assert len(sent) <= 3
    assert sum(len(chunk) for chunk in sent) < len(text) / 3


def test_render_sample_spreads_rows_across_table():
    text = _sql_dump()
    tables = find_tables(text, "dump.sql")
    sample = render_sample(text, tables)
    assert "numara=2021000" in sample and "numara=2021384" in sample  # bastan ve sondan


def _json_dump(rows: int = 300) -> str:
    records = []
    for i in range(rows):
        note = f"{_RARE_NAME} aradi" if i == 211 else ("burs" if i % 2 else "")
        records.append({
            "numara": 2021000 + i, "ad_soyad": _name(i), "aktif": bool(i % 3),
            "adres": {"il": ["Ankara", "Izmir"][i % 2], "posta_kodu": f"06{i % 90:03d}"},
            "dersler": [{"kod": "MAT101", "not": 70 + i % 30}], "notlar": note,
        })
    return json.dumps({"ogrenciler": records, "surum": 2}, ensure_ascii=False, indent=2)


def test_json_array_of_objects_becomes_tables_with_nested_columns():
    text = _json_dump()
    tables = {table.name: table for table in find_tables(text, "veri/ogrenci.json")}
    students = tables["$.ogrenciler[]"]
    assert students.columns == ["numara", "ad_soyad", "aktif", "adres.il", "adres.posta_kodu", "notlar"]
    cell = students.rows[5][1]
    assert text[cell.start:cell.end] == _name(5)
    assert students.rows[0][2] is None  # true/false hucre degildir
    assert len(tables["$.ogrenciler[].dersler[]"].rows) == 300  # ic ice dizi kendi tablosu


def test_json_lines_and_invalid_json():
    lines = "".join(json.dumps({"ad": f"Kisi {i}", "no": i}) + "\n" for i in range(MIN_TABLE_ROWS))
    [table] = find_tables(lines, "kayit.jsonl")
    assert table.name == "$[]" and table.columns == ["ad", "no"]
    assert find_tables('{"a": [1, 2', "bozuk.json") == []


def test_json_detection_masks_column_and_finds_rare_value(monkeypatch):
    text = _json_dump()
    sent = []

    async def _fake_call(host, timeout, payload, api_key=None):
        chunk = payload["messages"][-1]["content"]
        sent.append(chunk)
        found = [{"bulunan_deger": line.split("ad_soyad=")[1].split(" |")[0], "tip": "PERSON",
                  "guven_seviyesi": "yuksek", "gerekce": "kisi"}
                 for line in chunk.splitlines() if "ad_soyad=" in line]
        if _RARE_NAME in chunk:
            found.append({"bulunan_deger": _RARE_NAME, "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "kisi"})
        return _response(found)

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)
    detections = asyncio.run(find_llm_detections(text, [], _settings(), metadata={"file_path": "ogrenci.json"}))

    names = [d for d in detections if d.raw_result.get("sutun") == "$.ogrenciler[].ad_soyad"]
    assert len(names) == 300
    assert any(d.deger == _RARE_NAME for d in detections)
    assert sum(len(chunk) for chunk in sent) < len(text) / 3


def test_decoded_value_is_matched_to_escaped_json_text(monkeypatch):
    # Model kacisli `Tokgöz` yerine cozulmus `Tokgöz` dondurse de bulgu kaybolmaz.
    text = json.dumps({"veli": "Nermin Tokgöz aradi"}, ensure_ascii=True)

    async def _fake_call(host, timeout, payload, api_key=None):
        return _response([{"bulunan_deger": "Nermin Tokgöz", "tip": "PERSON", "guven_seviyesi": "yuksek", "gerekce": "k"}])

    monkeypatch.setattr("app.services.llm_recognizer.call_vllm", _fake_call)
    [detection] = asyncio.run(find_llm_detections(text, [], _settings(), metadata={"file_path": "a.json"}))
    assert text[detection.start:detection.end] == "Nermin Tokg\\u00f6z"


def test_csv_masking_is_checked_against_source_structure():
    from app.services.syntax_validator import validate_masked_syntax, validation_mode

    src = 'no;ad;not\n1;"Demir; Ayse";"satir1\nsatir2"\n2;Ali Kaya;x\n3;eksik\n'
    assert validation_mode("veri/liste.csv") == "csv-structure"
    ok = src.replace("Demir; Ayse", "mask_personel_1").replace("Ali Kaya", "mask_personel_2")
    assert validate_masked_syntax("liste.csv", ok, original_text=src) is None  # kaynaktaki eksik satir sorun degil
    broken = src.replace("Ali Kaya", "Ali;Kaya")
    assert "3. kayitta alan sayisi 3 iken 4" in validate_masked_syntax("liste.csv", broken, original_text=src)
    tsv = "a\tb\n1\tx y\n"
    assert validate_masked_syntax("t.tsv", tsv.replace("x y", "x\ty"), original_text=tsv) is not None
