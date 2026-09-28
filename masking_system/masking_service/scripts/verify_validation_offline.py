"""Smoke-test real validators while denying network connections in this process."""
import argparse
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-path", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    sys.path.insert(0, str(args.service_path.resolve()))

    def no_network(*args, **kwargs):
        raise AssertionError("Validation attempted network access")

    socket.create_connection = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    from app.services.syntax_validator import inspect_masked_syntax

    cases = [
        ("a.ts", "const x: number = 1;", "const x: number = ;"),
        ("a.tsx", "const x = <A />;", "const x = <A><B></A>;"),
        ("a.js", "const x = 1;", "const x = ;"),
        ("a.jsx", "const x = <A />;", "const x = <A><B></A>;"),
        ("a.xml", "<r><x /></r>", "<r><x></r>"),
        ("a.sql", "SELECT x FROM t WHERE x = 1", "SELECT x FROM t WHERE x ="),
        ("a.json", '{"id": 1}', '{"KEY_TEST_1": "VALUE_TEST_1"}'),
        ("a.yaml", "value: 1", "value: ["),
    ]
    for path, original, broken in cases:
        good = inspect_masked_syntax(path, original, original)
        bad = inspect_masked_syntax(path, broken, original)
        if good.mode != "parser-based" or good.warnings or good.error or bad.error is None:
            raise SystemExit(f"FAILED: {path}; good={good}; bad={bad}")
        print(f"PASS: {path} real parser / invalid edit rejected")
    print("Offline validation smoke test passed; no source execution or network connection.")


if __name__ == "__main__":
    main()
