#!/usr/bin/env python3
"""Assert a provisioned demo stack end-to-end through the Grafana API.

For each <datasource-uid>:<sql|windows> argument:
  - the datasource exists and its auth type matches the expected mode
  - a live query through it succeeds, proving the stored credentials
    authenticate against SQL Server
  - at least one provisioned alert rule targets it

Environment variables:
  GRAFANA_API_KEY   Grafana service account token; read from ./.env when unset
  GRAFANA_URL       Grafana base URL (default: http://localhost:3000)

Usage: python3 scripts/e2e-checks.py perfmon-ds-sql2022:sql perfmon-ds-sqlad:windows
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")

AUTH_TYPES = {
    "sql": "SQL Server Authentication",
    "windows": "Windows AD: Username + password",
}

IDENTITY_SQL = "SELECT SUSER_SNAME() AS who"


def _api_key() -> str:
    key = os.environ.get("GRAFANA_API_KEY")
    if not key:
        env_file = pathlib.Path(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("GRAFANA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("GRAFANA_API_KEY not set and not found in ./.env")
    return key


def _request(key: str, path: str, payload: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"{GRAFANA}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _check_instance(key: str, uid: str, mode: str, rules: list) -> list[str]:
    """Return a list of failure messages for one datasource (empty = pass)."""
    failures = []

    try:
        ds = _request(key, f"/api/datasources/uid/{uid}")
    except urllib.error.HTTPError as err:
        return [f"{uid}: datasource lookup failed ({err.code})"]

    want_auth = AUTH_TYPES[mode]
    got_auth = ds.get("jsonData", {}).get("authenticationType", "")
    if got_auth != want_auth:
        failures.append(f"{uid}: auth type {got_auth!r}, expected {want_auth!r}")

    query = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": uid},
                "rawSql": IDENTITY_SQL,
                "format": "table",
            }
        ],
        "from": "now-5m",
        "to": "now",
    }
    try:
        result = _request(key, "/api/ds/query", query)["results"]["A"]
    except urllib.error.HTTPError as err:
        result = json.load(err)["results"]["A"]
    if "error" in result:
        failures.append(f"{uid}: live query failed: {result['error']}")
    else:
        who = result["frames"][0]["data"]["values"][0][0]
        print(f"  {uid}: connects as {who}")

    targeting = sum(
        1 for r in rules for q in r.get("data", []) if q.get("datasourceUid") == uid
    )
    if targeting == 0:
        failures.append(f"{uid}: no provisioned alert rules target this datasource")
    else:
        print(f"  {uid}: {targeting} alert rule queries target it")

    return failures


def main() -> None:
    specs = []
    for arg in sys.argv[1:]:
        uid, _, mode = arg.partition(":")
        if mode not in AUTH_TYPES:
            sys.exit(f"usage: {sys.argv[0]} <ds-uid>:<sql|windows> [...]")
        specs.append((uid, mode))
    if not specs:
        sys.exit(f"usage: {sys.argv[0]} <ds-uid>:<sql|windows> [...]")

    key = _api_key()
    rules = _request(key, "/api/v1/provisioning/alert-rules")

    failures = []
    for uid, mode in specs:
        failures += _check_instance(key, uid, mode, rules)

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    print(f"{len(specs)} datasource(s) checked, {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
