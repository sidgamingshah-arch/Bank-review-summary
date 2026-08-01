"""One-click local launcher for the CAM platform — cross-platform.

    Windows: double-click ``start-windows.bat``   (calls this script)
    Linux/macOS: run ``./start-linux.sh``          (calls this script)
    Direct:  python scripts/launch.py

What it does, idempotently (fast on subsequent runs thanks to marker files):
  1. create a virtualenv in ``.venv`` and ``pip install -e .`` (first run only)
  2. build the web UI (``npm install`` + ``npm run build``) if Node is available
     and no build exists yet — the gateway then serves the SPA on :8080
  3. start the whole stack (gateway + 8 services) via ``scripts/run_stack.py``
  4. seed demo data (templates, KPIs, a sample case) on the very first run
  5. open the browser to http://localhost:8080 and stay up until Ctrl-C / window close

Only the Python standard library is used here, so it runs under whatever
interpreter launched it; all real work is delegated to the project venv.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
DATA_DIR = ROOT / ".data-dev"
GATEWAY = "http://localhost:8080"
IS_WINDOWS = os.name == "nt"
# every service must answer /healthz before we seed (the gateway comes up before
# its backends, so polling only the gateway would seed against a half-ready stack)
SERVICE_PORTS = [8080, 8101, 8102, 8103, 8104, 8105, 8106, 8107, 8108]


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _run(cmd: list[str], **kw) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(ROOT), **kw)


def _which(name: str) -> str | None:
    from shutil import which
    # npm on Windows is npm.cmd
    return which(name) or (which(name + ".cmd") if IS_WINDOWS else None)


def ensure_venv() -> Path:
    py = venv_python()
    if not py.exists():
        print("• creating virtualenv (.venv) …")
        _run([sys.executable, "-m", "venv", str(VENV)])
    marker = VENV / ".cam-installed"
    if not marker.exists():
        print("• installing the platform into the venv (first run, ~1 min) …")
        _run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        _run([str(py), "-m", "pip", "install", "--quiet", "-e", "."])
        marker.write_text("ok\n", encoding="utf-8")
    return py


def build_frontend() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if dist.is_file():
        return  # already built — the gateway will serve it
    npm = _which("npm")
    if not npm:
        print("• Node.js/npm not found — starting API only (the web UI won't load).\n"
              "  Install Node 18+ from https://nodejs.org and re-run to get the UI.")
        return
    fe = ROOT / "frontend"
    if not (fe / "node_modules").is_dir():
        print("• installing web UI dependencies (first run) …")
        _run([npm, "install"], cwd=str(fe))
    print("• building the web UI …")
    _run([npm, "run", "build"], cwd=str(fe))


def _port_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_healthy(timeout: float = 150.0) -> bool:
    """Return True once every service (not just the gateway) answers /healthz."""
    deadline = time.monotonic() + timeout
    pending = list(SERVICE_PORTS)
    while time.monotonic() < deadline:
        pending = [p for p in pending if not _port_healthy(p)]
        if not pending:
            return True
        time.sleep(0.5)
    return False


def seed_first_run(py: Path) -> None:
    marker = DATA_DIR / ".seeded"
    if marker.exists():
        return
    print("• seeding demo data (templates, KPIs, a sample case) …")
    try:
        _run([str(py), str(ROOT / "scripts" / "seed_demo.py")])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok\n", encoding="utf-8")
    except subprocess.CalledProcessError:
        print("  (seed skipped — continuing; you can run scripts/seed_demo.py later)")


def main() -> int:
    # legacy Windows consoles default to a non-UTF-8 code page; make our glyphs safe
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("CAM Studio - local launcher\n" + "-" * 30)
    py = ensure_venv()
    build_frontend()

    print("• starting services …")
    stack = subprocess.Popen([str(py), str(ROOT / "scripts" / "run_stack.py")], cwd=str(ROOT))
    try:
        if not wait_healthy():
            print("!! services did not become healthy in time — see .data-dev/logs/*.log")
            stack.terminate()
            return 1
        seed_first_run(py)
        print(f"\n✔ CAM Studio is running at {GATEWAY}")
        print("  Log in as analyst1 / Demo#2026 (or admin1 / Demo#2026). Ctrl-C to stop.\n")
        try:
            webbrowser.open(GATEWAY)
        except Exception:
            pass
        stack.wait()
    except KeyboardInterrupt:
        print("\n• stopping …")
    finally:
        if stack.poll() is None:
            stack.terminate()
            try:
                stack.wait(timeout=10)
            except subprocess.TimeoutExpired:
                stack.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
