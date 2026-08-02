# Offline / air-gapped install (bank server)

The platform installs with **no internet access on the target server** using a
pre-built **wheelhouse** (every Python dependency, pinned). All Python libraries
are captured in [`requirements.lock.txt`](requirements.lock.txt) (runtime + all
optional backends: PostgreSQL, Opik, Anthropic, Azure).

## 1. On an internet-connected BUILD host

The build host must match the target server's **OS, CPU architecture and Python
version** (3.11), because some wheels contain compiled C extensions.

```bash
git clone <repo> && cd cam-platform
bash deploy/build-offline-kit.sh          # -> deploy/wheelhouse/ (all dependency wheels + the cam-platform wheel)
# (optional) build the web UI so the gateway can serve it at :8080
cd frontend && npm ci && npm run build && cd ..
```

Transfer to the server: this repository **plus** `deploy/wheelhouse/` (and, if you
built it, `frontend/dist/`).

## 2. On the air-gapped SERVER (no internet)

```bash
python3 -m venv .venv
# install the platform + every dependency from the local wheelhouse ONLY (no network)
.venv/bin/pip install --no-index --find-links deploy/wheelhouse cam-platform
#   add backends you use, still offline, e.g.:
.venv/bin/pip install --no-index --find-links deploy/wheelhouse "cam-platform[postgres,opik]"
```

`--no-index` guarantees pip never reaches out to the internet; everything is
resolved from your vetted wheelhouse.

## 3. Configure and run

```bash
sudo mkdir -p /etc/cam
sudo cp deploy/systemd/cam-platform.env.example /etc/cam/cam-platform.env
sudo chmod 600 /etc/cam/cam-platform.env         # holds secrets
sudo "$EDITOR" /etc/cam/cam-platform.env         # set CAM_JWT_SECRET, CAM_DB_URL, provider keys, (optional) CAM_OPIK_*
sudo cp deploy/systemd/cam-platform.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cam-platform
```

See [DEPLOYMENT.md](../docs/DEPLOYMENT.md) for the systemd/Windows-service details
and the **security checklist**.

## Notes

- **Supply-chain integrity.** Installing from your own wheelhouse with `--no-index`
  means only the files you vetted are used. For stricter control, generate a
  hash-pinned lock on the build host (`pip install pip-tools && pip-compile
  --generate-hashes`) and install with `pip install --require-hashes`.
- **No new libraries at runtime.** The services import only what is in the lock;
  nothing is fetched at run time. Optional backends stay dormant until configured.
- **Frontend.** If you cannot run Node on the build host, the API still runs
  headless; ship a `frontend/dist/` built elsewhere and point `CAM_FRONTEND_DIST`
  at it (or serve it from any static host).
