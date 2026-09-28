"""Asama 2 / Adim 1b: cli.py'nin SessionLocal() kullanimindan
app.db.session.session_scope()'a gecmesinin CLI komutlarini bozmadigini
dogrulayan smoke test. Salt-okunur bir komut (kural-listele) secildi - DB'de
hicbir yaziya sebep olmaz.
"""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_kural_listele_runs_successfully_via_session_scope():
    result = runner.invoke(app, ["kural-listele"])

    assert result.exit_code == 0
    assert "project_name" in result.output
