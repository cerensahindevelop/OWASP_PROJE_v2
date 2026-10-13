"""Hizli metin dosya turu: otomatik algilama ve genis tur listesi."""
import asyncio

import pytest

from app.services import exporter
from app.webapp.quick_text import (
    AUTO_DETECT_LABEL, QUICK_TEXT_TYPES, detect_quick_text_type, quick_text_file_name, resolve_quick_text_type,
)

DETECTION_SAMPLES = [
    (".json", '{"db": {"host": "10.0.0.5", "user": "app"}}'),
    (".json", '[{"ad": "mask_person_1", "ip": "mask_ip_2"}]'),
    (".xml", '<project>\n  <groupId>com.x</groupId>\n</project>'),
    (".xml", '<bean id="ds" class="x.DataSource"><property name="url" value="jdbc:x"/></bean>'),
    (".html", '<!DOCTYPE html><html><body>x</body></html>'),
    (".properties", 'spring.datasource.url=jdbc:x\nspring.datasource.password=abc\n# yorum\nserver.port=8080'),
    (".properties", 'db.url: jdbc:x\ndb.user: app\ndb.pass: abc'),
    (".env", 'DB_HOST=10.0.0.5\nDB_PASS=abc\nexport API_KEY=xyz'),
    (".yml", 'pipeline:\n  name: x\n  agents:\n    - host: a\n      site: b'),
    (".yml", '# Spring ayarlari\nspring:\n  datasource:\n    url: jdbc:oracle:thin:@10.0.0.5:1521/X\n    password: S1'),
    (".yml", 'version: "3"\nservices:\n  web:\n    image: nginx\n    environment:\n      - DB_HOST=10.0.0.5'),
    (".sql", "-- rapor\nSELECT p.ad FROM personel p WHERE p.id = 3;"),
    (".sql", "select ad, soyad\nfrom personel\nwhere sicil = 'A100233';"),
    (".sql", "BEGIN\n  UPDATE hesap SET bakiye = 0 WHERE id = 5;\nEND;"),
    (".java", 'package com.x.y;\n\nimport java.util.List;\npublic class A {\n  private int x;\n}'),
    (".java", 'public void baglan() {\n    String url = "jdbc:x";\n    this.conn = DriverManager.getConnection(url);\n}'),
    (".java", '@Override\npublic String toString() {\n    return "Kullanici{" + ad + "}";\n}'),
    (".cs", 'using System;\nnamespace A.B\n{\n  public class C { public int X { get; set; } }\n}'),
    (".cs", 'var client = new HttpClient();\nclient.BaseAddress = new Uri("https://x");\nConsole.WriteLine("x");'),
    (".py", 'import os\n\ndef f(x):\n    return x\n'),
    (".py", 'DB_HOST = "10.0.0.5"\nprint(DB_HOST)\nfor i in range(3):\n    print(i)'),
    (".ts", 'export interface User {\n  name: string;\n}\nconst u: User = {name: "a"};'),
    (".js", 'const x = require("y");\nmodule.exports = function () { return x; };'),
    (".sh", '#!/bin/bash\nexport A=1\nrsync -az /a b'),
    (".sh", 'cd /opt/app\n./start.sh --port 8080\necho "bitti"'),
    (".cpp", '#include <stdio.h>\nint main() { return 0; }'),
    (".go", 'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("x") }'),
    (".kt", 'data class User(val name: String)\nfun main() { val u = User("a") }'),
    (".rs", 'use std::io;\nfn main() -> io::Result<()> { let mut x = 1; Ok(()) }'),
    (".vhd", 'library ieee;\nentity counter is\n port (clk : in std_logic);\nend counter;'),
    (".log", '2026-10-09 10:00:01 INFO Basladi\n2026-10-09 10:00:02 ERROR Hata host=10.0.0.5'),
    (".log", 'Oct 09 10:00:01 srv01 sshd[123]: Accepted password for root from 10.0.0.5'),
    (".ini", '[database]\nhost=10.0.0.5\nuser=app'),
    (".csv", 'ad,soyad,sicil\nAli,Kaya,A100\nAyse,Demir,A101'),
    (".txt", 'Merhaba, bu toplantı notu. Sunucu 10.0.0.5 üzerinde çalışıyor.'),
    (".txt", 'Toplantı notu:\nSunucu adresi = 10.0.0.5 olarak güncellendi.\nSorumlu: Ali Kaya'),
    (".txt", 'Ali Kaya\nAyşe Demir\nMehmet Yılmaz'),
    (".txt", ""),
]


@pytest.mark.parametrize("expected, text", DETECTION_SAMPLES)
def test_detects_common_types(expected, text):
    assert detect_quick_text_type(text) == expected


def test_listed_choice_wins_over_detection():
    assert resolve_quick_text_type("Java (.java)", '{"a": 1}') == (".java", False)
    assert resolve_quick_text_type(AUTO_DETECT_LABEL, '{"a": 1}') == (".json", True)


def test_every_listed_type_is_scanned_and_masked(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(exporter.settings.vllm, "enabled", False)
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    names = {quick_text_file_name(ext) for ext in QUICK_TEXT_TYPES.values()}
    for name in names:
        (source / name).write_text("sunucu = 10.42.17.80\n", encoding="utf-8")

    report = asyncio.run(exporter.export_project(
        db_session, source_path=str(source), target_path=str(target),
        project_name="hizli-tur", sicil_no="P-QT", branch_name="main", initiated_by="test"))

    statuses = {o.relative_path: o.status for o in report.outcomes}
    assert all(status != "excluded" for status in statuses.values()), statuses
    leaked = [name for name in names
              if (target / name).exists() and "10.42.17.80" in (target / name).read_text(encoding="utf-8")]
    assert leaked == []
