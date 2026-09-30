"""Backend (FastAPI/uvicorn) ve web arayuzunu (Streamlit) TEK komutla baslatir.

Calistirma (masking_service/ klasorunden):

    .venv\\Scripts\\python.exe start.py            (Windows)
    .venv/bin/python start.py                     (Linux/macOS)

Sirasiyla: .env ayarlarini dogrular, veritabani dosyasini kontrol eder,
backend'i baslatip /health yanit verene kadar bekler, ardindan Streamlit'i
acar. Ctrl+C ya da sureclerden birinin kapanmasi ikisini birlikte durdurur.
Ayar veya kurulum hatasi, surecler baslamadan acik bir mesajla bildirilir.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
HEALTH_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
STOP_TIMEOUT_SECONDS = 10.0


class StartupError(RuntimeError):
    """Uygulama baslatilamadi; mesaj kullaniciya aynen gosterilir."""


def load_settings():
    try:
        from app.core.config import settings
    except Exception as exc:  # pydantic ValidationError dahil: mesaj ayar adini soyler
        raise StartupError(f".env ayarlari gecersiz:\n{exc}") from exc
    return settings


def ensure_database(settings, migrate: bool) -> None:
    if migrate:
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT)
        if result.returncode:
            raise StartupError("Veritabani migration'i basarisiz (alembic upgrade head).")
        return
    if not settings.database.resolved_path.exists():
        raise StartupError(
            f"Veritabani bulunamadi: {settings.database.resolved_path}\n"
            "Ilk kurulumda once 'python -m alembic upgrade head' calistirin ya da start.py'yi --migrate ile baslatin."
        )


def start_backend(settings) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api_app:app",
         "--host", settings.web.api_host, "--port", str(settings.web.api_port)],
        cwd=ROOT,
    )


def start_ui(ui_port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(ui_port)],
        cwd=ROOT,
    )


def wait_for_health(backend: subprocess.Popen, base_url: str) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    url = f"{base_url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            raise StartupError("Backend acilirken kapandi; yukaridaki uvicorn ciktisina bakin.")
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(POLL_INTERVAL_SECONDS)
    raise StartupError(f"Backend {HEALTH_TIMEOUT_SECONDS:.0f} sn icinde hazir olmadi ({url}).")


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()


def supervise(processes: list[subprocess.Popen]) -> int:
    """Sureclerden biri kapanana (ya da Ctrl+C'ye) kadar bekler; cikis kodunu doner."""
    while True:
        for process in processes:
            code = process.poll()
            if code is not None:
                return code
        time.sleep(POLL_INTERVAL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maskeleme sistemini (backend + web arayuzu) baslatir.")
    parser.add_argument("--migrate", action="store_true", help="Baslamadan once 'alembic upgrade head' calistir.")
    parser.add_argument("--ui-port", type=int, default=8501, help="Streamlit portu (varsayilan 8501).")
    args = parser.parse_args(argv)

    processes: list[subprocess.Popen] = []
    try:
        settings = load_settings()
        ensure_database(settings, args.migrate)
        backend = start_backend(settings)
        processes.append(backend)
        wait_for_health(backend, settings.web.api_base_url)
        print(f"Backend hazir: {settings.web.api_base_url}", flush=True)
        processes.append(start_ui(args.ui_port))
        print(f"Web arayuzu: http://localhost:{args.ui_port}  (durdurmak icin Ctrl+C)", flush=True)
        return supervise(processes)
    except StartupError as exc:
        print(f"\nBASLATILAMADI: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop(processes)


if __name__ == "__main__":
    raise SystemExit(main())
