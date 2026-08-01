# Deployment guide (Linux & Windows)

One codebase, several targets. Pick the row that matches your host; every option
runs the same nine services behind the gateway on **:8080** (which also serves the
built web UI, so the whole app is one origin).

| Target | OS | Best for | Section |
|---|---|---|---|
| One-click dev launcher | Windows / Linux / macOS | demo, evaluation, a single analyst | [1](#1-one-click-dev-launcher) |
| systemd service | Linux | a single always-on Linux host | [2](#2-linux-production-systemd) |
| Scheduled-task service | Windows | a single always-on Windows host/VM | [3](#3-windows-production-service) |
| Containers (Postgres) | Linux / Windows (Docker) | the standard production topology | [4](#4-containers-docker--postgresql) |
| Cloud / bank | Linux (managed) | Azure / behind the bank APIM | [5](#5-cloud--bank-target) |

**Prerequisites (native options):** Python **3.10+** (required) and Node.js **18+**
(only to build the web UI; the API runs without it). Containers need only Docker.

---

## 1. One-click dev launcher

Zero external dependencies (SQLite, local blob dirs, mock LLM). First run creates
the venv, builds the UI, seeds demo data, starts everything, and opens the browser;
later runs start in seconds (marker files skip setup).

**Windows** — double-click **`start-windows.bat`** (or run it in a terminal).

**Linux / macOS**
```bash
./start-linux.sh
```

Both call the same cross-platform `scripts/launch.py`. Sign in at
**http://localhost:8080** as `analyst1` / `Demo#2026` (or `admin1` / `Demo#2026`).
Press Ctrl-C (or close the window) to stop.

---

## 2. Linux production (systemd)

A single always-on host, one supervisor process managing the nine services, with
restart-on-failure. (For more than one host, use [containers](#4-containers-docker--postgresql).)

```bash
# one-time host setup
sudo useradd --system --home /opt/cam --shell /usr/sbin/nologin cam
sudo cp -r cam-platform /opt/cam/cam-platform
sudo -u cam python3 -m venv /opt/cam/cam-platform/.venv
sudo -u cam /opt/cam/cam-platform/.venv/bin/pip install -e /opt/cam/cam-platform
# build the web UI so the gateway can serve it (needs Node)
sudo -u cam bash -c 'cd /opt/cam/cam-platform/frontend && npm ci && npm run build'

# config (holds secrets)
sudo mkdir -p /etc/cam
sudo cp /opt/cam/cam-platform/deploy/systemd/cam-platform.env.example /etc/cam/cam-platform.env
sudo chmod 600 /etc/cam/cam-platform.env
sudo "$EDITOR" /etc/cam/cam-platform.env      # set CAM_JWT_SECRET, CAM_DB_URL, provider keys …

# install + start
sudo cp /opt/cam/cam-platform/deploy/systemd/cam-platform.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cam-platform
sudo systemctl status cam-platform          # journalctl -u cam-platform -f  for logs
```

The unit binds services to `127.0.0.1`; put **nginx/Apache** in front for TLS and
to expose :8080 publicly. `systemctl stop` sends SIGTERM to the supervisor, which
tears down the child services cleanly (`run_stack.py` handles it).

---

## 3. Windows production (service)

For an always-on Windows host/VM, register CAM Studio to start at boot using a
built-in **Scheduled Task** (no third-party tools):

```powershell
# elevated PowerShell, from the repo root — venv + frontend\dist must already exist
.\deploy\windows\install-service.ps1
Start-ScheduledTask -TaskName CAMStudio        # browse http://localhost:8080
# remove later:  .\deploy\windows\uninstall-service.ps1
```

Because the task runs as `SYSTEM`, production config must be set as **machine-level**
environment variables (a SYSTEM task ignores user env), e.g.:
```powershell
setx CAM_JWT_SECRET "a-long-random-secret" /M
setx CAM_DB_URL "postgresql+psycopg://cam:cam@localhost:5432/cam" /M
setx CAM_LLM_PROVIDER "anthropic" /M ; setx ANTHROPIC_API_KEY "..." /M
```
For a true SCM-managed Windows Service, wrap `scripts\run_stack.py` with
[NSSM](https://nssm.cc/) (one-liner in `install-service.ps1`'s header).

---

## 4. Containers (Docker + PostgreSQL)

The standard production topology — PostgreSQL system-of-record + one container per
service (same image, per-container `SERVICE_MODULE`), gateway on :8080. Runs the
same on Linux and on Windows with Docker Desktop.

```bash
docker compose up --build          # then, once, seed masters:
python scripts/seed_demo.py        # or drive the APIs / import a masters bundle
```

Scale generation horizontally by scaling the orchestration container — queue claims
stay disjoint under `SELECT … FOR UPDATE SKIP LOCKED`. Configure via the same
`CAM_*` variables (see `.env.example` and `docker-compose.yml`).

---

## 5. Cloud / bank target

Selected entirely by environment variables — no code change:

- **Model**: `CAM_LLM_PROVIDER` = `anthropic` | `openai` (any OpenAI-compatible
  endpoint) | `azure` (Azure OpenAI, incl. reasoning). Keys referenced by env-var
  name only, never stored.
- **Retrieval**: `CAM_RETRIEVAL_BACKEND` = `local` | `azure_search` (Azure AI Search).
- **Storage**: `CAM_BLOB_BACKEND` = `local` | `azure` (Azure Blob).
- **Identity**: swap the auth-adapter for the bank IdP (OIDC/SAML) — one service.
- **Prompt store**: `CAM_OPIK_ENABLED=true` + `CAM_OPIK_*` makes Opik the
  system-of-record for section prompts (self-hosted or Comet cloud). Disabled = a
  local snapshot stand-in. Install the extra: `pip install -e .[opik]`.
- **Edge**: services behind the real APIM; the built-in gateway policies mirror it.

See `docs/architecture.md` (§14) and `docs/LIVE_RUN.md` for provider/RAG specifics.

---

## Configuration & operations

- **Two config layers:** environment (`CAM_*`, secrets, deployment shape) and
  runtime **master settings** (operating levers — concurrency, connectors, email —
  editable in the admin UI, no restart). Full list: `.env.example`.
- **Ports:** gateway `8080` (the only one to expose); services `8101–8108` stay on
  loopback behind the gateway.
- **Web UI:** the gateway serves `frontend/dist` when present. Build with
  `npm run build` (or `make frontend`); override the location with `CAM_FRONTEND_DIST`.
- **Health:** `GET /healthz` on any service (`:8080/healthz` for the gateway).
- **Upgrade:** pull, `pip install -e .`, rebuild the UI, restart the unit/task/containers.
- **Verify a host:** `make test` (208 backend tests) and `python scripts/e2e_demo.py`
  (full acceptance walkthrough) both run against SQLite + mock LLM with no external deps.
