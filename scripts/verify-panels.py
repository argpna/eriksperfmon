#!/usr/bin/env python3
"""Smoke-test every panel query in every dashboard through the Grafana query API.

Substitutes template variables with placeholder values, runs each rawSql against
one instance datasource, and reports SQL errors and row counts. Zero rows is not a
failure; a SQL error is.

Environment variables:
  GRAFANA_API_KEY         Grafana service account token (preferred)
  GRAFANA_ADMIN_PASSWORD  basic-auth password for the admin user (alternative)
  GRAFANA_URL             Grafana base URL (default: http://localhost:3000)
  PERFMON_DASHBOARD_DIR   directory containing dashboard JSON files

Usage: python3 scripts/verify-panels.py <datasource-uid>
"""

import base64
import json
import os
import pathlib
import sys
import urllib.request

GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
_api_key = os.environ.get("GRAFANA_API_KEY")
_admin_password = os.environ.get("GRAFANA_ADMIN_PASSWORD")
if _api_key:
    AUTH_HEADER = f"Bearer {_api_key}"
elif _admin_password:
    AUTH_HEADER = "Basic " + base64.b64encode(f"admin:{_admin_password}".encode()).decode()
else:
    sys.exit("Set GRAFANA_API_KEY or GRAFANA_ADMIN_PASSWORD")
if len(sys.argv) < 2:
    sys.exit("Usage: verify-panels.py <datasource-uid>")
DS_UID = sys.argv[1]

# Placeholder substitutions for Grafana template variables
SUBS = {
    "$topn": "25",
    "${counter:sqlstring}": "'Batch Requests/sec'",
    "${heatmap_metric}": "Duration",
    "${filter}": "",
    "${database}": "",
    "${schema_name}": "",
    "${procedure_name}": "",
    "${query_hash}": "",
    "${deadlock_id}": "",
    "${wait_type}": "",
}


def run_query(sql: str, fmt: str):
    body = json.dumps(
        {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"type": "mssql", "uid": DS_UID},
                    "format": fmt,
                    "rawQuery": True,
                    "rawSql": sql,
                    "intervalMs": 60000,
                    "maxDataPoints": 500,
                }
            ],
            "from": "now-3h",
            "to": "now",
        }
    ).encode()
    req = urllib.request.Request(
        f"{GRAFANA}/api/ds/query",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": AUTH_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        r = json.load(e)
    a = r["results"]["A"]
    if a.get("error"):
        return ("ERROR", a["error"])
    rows = 0
    for fr in a.get("frames", []):
        vals = fr["data"]["values"]
        rows += len(vals[0]) if vals else 0
    return ("OK", rows)


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    _default_dir = root / "ansible" / "roles" / "perfmon_grafana" / "files" / "grafana" / "dashboards" / "perfmon"
    dashboard_dir = pathlib.Path(os.environ["PERFMON_DASHBOARD_DIR"]) if "PERFMON_DASHBOARD_DIR" in os.environ else _default_dir
    failures = 0
    for f in sorted(dashboard_dir.rglob("*.json")):
        with open(f, encoding="utf-8") as fh:
            dash = json.load(fh)
        name = pathlib.Path(f).name
        for panel in dash["panels"]:
            for t in panel.get("targets", []):
                sql = t["rawSql"]
                for k, v in SUBS.items():
                    sql = sql.replace(k, v)
                status, detail = run_query(sql, t.get("format", "table"))
                flag = ""
                if status == "ERROR":
                    failures += 1
                    flag = "  <<< FAIL"
                    detail = str(detail)[:160]
                print(
                    f"{name:28s} | {panel['title'][:52]:52s} | {status} {detail}{flag}"
                )
    print(f"\n{f'FAILURES: {failures}' if failures else 'ALL PANEL QUERIES OK'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
