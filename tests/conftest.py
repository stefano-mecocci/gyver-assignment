import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLE_JOB_OFFER = BACKEND_DIR / "data" / "sample_job_offer.json"
STARTUP_TIMEOUT_SECONDS = 20


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_healthy(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The test server exited during startup")
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"The test server did not start within {STARTUP_TIMEOUT_SECONDS} seconds")


@pytest.fixture(scope="session")
def seeded_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    database = tmp_path_factory.mktemp("seed") / "seeded.db"
    subprocess.run(
        [sys.executable, "seed.py"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        check=True,
        capture_output=True,
    )
    return database


@pytest.fixture
def server_env() -> dict[str, str]:
    return {}


@pytest.fixture
def database(tmp_path: Path, seeded_database: Path) -> Path:
    path = tmp_path / "test.db"
    shutil.copy(seeded_database, path)
    return path


@pytest.fixture
def server(database: Path, server_env: dict[str, str], tmp_path: Path) -> Iterator[str]:
    port = free_port()
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database}",
        "IMAGES_DIR": str(tmp_path / "images"),
        "HF_TOKEN": "",
        "POLLINATIONS_URL": "http://127.0.0.1:9/prompt/",
        **server_env,
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_until_healthy(url, process)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def api(server: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server, timeout=400) as client:
        yield client


@pytest.fixture
def sample_job_offer() -> dict:
    return json.loads(SAMPLE_JOB_OFFER.read_text(encoding="utf-8"))
