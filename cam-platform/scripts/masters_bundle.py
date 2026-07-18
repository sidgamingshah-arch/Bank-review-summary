"""Move master configuration between environments (deploy-time swap of the
prompt library, templates, doc types, taxonomy and KPI sets).

    python scripts/masters_bundle.py export bundle.json --user admin1
    python scripts/masters_bundle.py import bundle.json --user admin1

Export captures every PUBLISHED master; import lands them as DRAFTS in the
target environment — maker-checker approval still governs publication there.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import GATEWAY, login  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("path", help="bundle JSON file")
    parser.add_argument("--user", default="admin1", help="business-admin username")
    parser.add_argument("--gateway", default=GATEWAY)
    args = parser.parse_args()

    password = getpass.getpass(f"password for {args.user}: ") \
        if sys.stdin.isatty() else None

    with httpx.Client(timeout=60.0, base_url=args.gateway) as client:
        if password is None:
            headers = login(client, args.user)  # dev default password
        else:
            r = client.post("/api/auth/token", json={"username": args.user,
                                                     "password": password})
            r.raise_for_status()
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        if args.action == "export":
            r = client.get("/api/masters/export-bundle", headers=headers)
            r.raise_for_status()
            bundle = r.json()
            Path(args.path).write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
            print(f"exported {len(bundle['masters'])} published masters -> {args.path}")
        else:
            bundle = json.loads(Path(args.path).read_text())
            r = client.post("/api/masters/import-bundle", headers=headers,
                            json={"masters": bundle["masters"]})
            r.raise_for_status()
            report = r.json()
            print(f"created: {len(report['created'])}  updated: {len(report['updated'])}  "
                  f"unchanged: {len(report['unchanged'])}  errors: {len(report['errors'])}")
            for err in report["errors"]:
                print(f"  ✘ {err['entry']}: {err['message']}")
            print(report["note"])
            if report["errors"]:
                sys.exit(1)


if __name__ == "__main__":
    main()
