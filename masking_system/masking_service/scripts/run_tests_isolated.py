"""Run tests against a freshly migrated temporary DB, never the project DB."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

from cryptography.fernet import Fernet


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="masking-tests-") as directory:
        env = dict(os.environ, DB_PATH=str(Path(directory) / "tests.db"),
                   SECURITY_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                   VLLM_ENABLED="false", VLLM_HOST="http://127.0.0.1:1",
                   VLLM_MODEL="isolated-tests", VLLM_TIMEOUT_SECONDS="0.2")
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env)
        if result.returncode:
            return result.returncode
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *sys.argv[1:]],
            cwd=root, env=env,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
