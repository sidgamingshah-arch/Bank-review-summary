"""Run the full CAM platform locally: gateway (APIM stand-in) + 8 services.

    python scripts/run_stack.py            # start, wait healthy, block until Ctrl-C
    from run_stack import Stack            # or embed (used by e2e_demo.py)

SQLite + local blob storage under .data-dev/ — production topology (PostgreSQL,
object store, real APIM) is described in docker-compose.yml and docs/.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "bin" / "python")

SERVICES = [
    ("gateway", "cam.gateway.main:app", 8080),
    ("auth", "cam.services.auth.main:app", 8101),
    ("master-config", "cam.services.master_config.main:app", 8102),
    ("document", "cam.services.document.main:app", 8103),
    ("tagging", "cam.services.tagging.main:app", 8104),
    ("orchestration", "cam.services.orchestration.main:app", 8105),
    ("genai", "cam.services.genai.main:app", 8106),
    ("output", "cam.services.output.main:app", 8107),
    ("audit", "cam.services.audit.main:app", 8108),
]


class Stack:
    def __init__(self, data_dir: str | None = None, log_dir: str | None = None):
        self.data_dir = data_dir or str(ROOT / ".data-dev")
        self.log_dir = Path(log_dir or (ROOT / ".data-dev" / "logs"))
        self.procs: list[subprocess.Popen] = []

    def start(self, wait: bool = True) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CAM_DATA_DIR": self.data_dir, "PYTHONUNBUFFERED": "1"}
        for name, module, port in SERVICES:
            log = open(self.log_dir / f"{name}.log", "w")
            self.procs.append(subprocess.Popen(
                [PYTHON, "-m", "uvicorn", module, "--port", str(port),
                 "--host", "127.0.0.1", "--log-level", "warning"],
                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT))
        if wait:
            self.wait_healthy()

    def wait_healthy(self, timeout: float = 45.0) -> None:
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=2.0) as client:
            for name, _, port in SERVICES:
                while True:
                    try:
                        if client.get(f"http://127.0.0.1:{port}/healthz").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        self.stop()
                        raise RuntimeError(f"service '{name}' failed to become healthy "
                                           f"(see {self.log_dir}/{name}.log)")
                    time.sleep(0.3)
        print(f"stack healthy: {len(SERVICES)} services up, gateway on :8080")

    def stop(self) -> None:
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []

    def __enter__(self) -> "Stack":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


if __name__ == "__main__":
    stack = Stack()
    stack.start()
    print("CAM platform running — gateway http://localhost:8080 · Ctrl-C to stop")
    try:
        signal.pause()
    except (KeyboardInterrupt, AttributeError):
        pass
    finally:
        stack.stop()
        sys.exit(0)
