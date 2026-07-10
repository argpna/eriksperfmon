#!/usr/bin/env python3
"""Smoke-test every panel query in every dashboard through the Grafana query API.

Derives template-variable substitutions from each dashboard's own `templating.list`,
walks panels recursively. Zero rows is not a failure; a SQL error is.

Cannot detect: a variable's cold-load interpolation behavior. This script builds its own
substitution SQL in Python and calls /api/ds/query directly, bypassing Grafana's frontend
variable-interpolation engine entirely. Real Grafana collapses ${var:sqlstring} to a
zero-length token (not an empty quoted string) on a dashboard's first/cold load when the
variable's value is "" - this script's _sqlstring() always quotes, so it cannot reproduce
that divergence. A regression to an empty-string default on a new optional textbox filter
will smoke-test clean here and still break on first page load. See _shared.py's text_var()
- its `default` argument has no fallback for this reason.

Environment variables:
  GRAFANA_API_KEY         Grafana service account token (preferred)
  GRAFANA_ADMIN_PASSWORD  basic-auth password for the admin user (alternative)
  GRAFANA_URL             Grafana base URL (default: http://localhost:3000)
  PERFMON_DASHBOARD_DIR   directory containing dashboard JSON files

Usage: python3 scripts/verify-panels.py <datasource-uid> [<datasource-uid> ...]
"""

import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
_api_key = os.environ.get("GRAFANA_API_KEY")
_admin_password = os.environ.get("GRAFANA_ADMIN_PASSWORD")
if _api_key:
    AUTH_HEADER = f"Bearer {_api_key}"
elif _admin_password:
    AUTH_HEADER = (
        "Basic " + base64.b64encode(f"admin:{_admin_password}".encode()).decode()
    )
else:
    sys.exit("Set GRAFANA_API_KEY or GRAFANA_ADMIN_PASSWORD")
if len(sys.argv) < 2:
    sys.exit("Usage: verify-panels.py <datasource-uid> [<datasource-uid> ...]")
DS_UIDS = sys.argv[1:]

REQUEST_TIMEOUT_SECONDS = 30

NON_SQL_VARIABLE_TYPES = {"datasource"}

# Escape hatch: only needed when a variable's own `current.value` in the dashboard JSON
# isn't a good smoke-test value e.g. would trivially return 0 rows for a reason that
# masks a real bug. Empty by design - prefer fixing the dashboard's default instead of
# adding an entry here.
SUBS_OVERRIDES: dict[str, str] = {}


def _escape_sql_string(value: str) -> str:
    """Reproduce T-SQL single-quote doubling. Bare ${var} does not add surrounding
    quotes (the SQL text supplies its own); only :sqlstring does - keep these as
    separate substitutions rather than collapsing both into one pre-quoted form,
    or a bare ${var} call site would get double-quoted and silently mask errors."""
    return str(value).replace("'", "''")


def _sqlstring(value: str) -> str:
    """Reproduce Grafana's ${var:sqlstring} formatting: quote and escape for T-SQL."""
    return "'" + _escape_sql_string(value) + "'"


def _dashboard_subs(dash: dict) -> dict[str, str]:
    """Build the ${name} -> escaped-only value table for one dashboard, derived from its
    own templating.list instead of a hand-maintained global dict."""
    subs = {}
    for var in dash.get("templating", {}).get("list", []):
        name = var.get("name")
        if not name or var.get("type") in NON_SQL_VARIABLE_TYPES:
            continue
        if name in SUBS_OVERRIDES:
            value = SUBS_OVERRIDES[name]
        else:
            current = var.get("current", {})
            value = current.get("value", "")
            if isinstance(value, list):
                value = value[0] if value else ""
        subs[name] = str(value)
    return subs


def _apply_subs(sql: str, subs: dict[str, str]) -> str:
    for name, value in subs.items():
        sql = sql.replace(f"${{{name}:sqlstring}}", _sqlstring(value))
        sql = sql.replace(f"${{{name}}}", _escape_sql_string(value))
    return sql


def iter_panels(panels):
    """Yield every panel, recursing into collapsed rows' nested "panels" list so those
    targets aren't silently skipped."""
    for panel in panels:
        yield panel
        if panel.get("type") == "row" and panel.get("panels"):
            yield from iter_panels(panel["panels"])


def run_query(ds_uid: str, sql: str, fmt: str):
    body = json.dumps(
        {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"type": "mssql", "uid": ds_uid},
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
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            r = json.load(e)
        except json.JSONDecodeError:
            return ("ERROR", f"HTTP {e.code}: {e.reason}")
    except (urllib.error.URLError, TimeoutError) as e:
        return ("ERROR", f"request failed: {e}")

    a = r.get("results", {}).get("A")
    if a is None:
        return ("ERROR", f"no result for query A: {r}")
    if a.get("error"):
        return ("ERROR", a["error"])
    rows = 0
    for fr in a.get("frames", []):
        vals = fr["data"]["values"]
        rows += len(vals[0]) if vals else 0
    return ("OK", rows)


def load_dashboard(path: pathlib.Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{path.name:28s} | <<< COULD NOT PARSE: {e}")
        return None


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    _default_dir = (
        root
        / "ansible"
        / "roles"
        / "perfmon_grafana"
        / "files"
        / "grafana"
        / "dashboards"
        / "perfmon"
    )
    dashboard_dir = (
        pathlib.Path(os.environ["PERFMON_DASHBOARD_DIR"])
        if "PERFMON_DASHBOARD_DIR" in os.environ
        else _default_dir
    )
    failures = 0
    tested = 0
    for f in sorted(dashboard_dir.rglob("*.json")):
        dash = load_dashboard(f)
        if dash is None:
            failures += 1
            continue
        name = f.name
        subs = _dashboard_subs(dash)
        for panel in iter_panels(dash.get("panels", [])):
            for t in panel.get("targets", []):
                sql = t.get("rawSql")
                if not sql:
                    continue  # builder-mode / non-SQL target, nothing to smoke-test
                sql = _apply_subs(sql, subs)
                for ds_uid in DS_UIDS:
                    tested += 1
                    status, detail = run_query(ds_uid, sql, t.get("format", "table"))
                    flag = ""
                    if status == "ERROR":
                        failures += 1
                        flag = "  <<< FAIL"
                        detail = str(detail)[:160]
                    prefix = f"{name:28s} | {panel.get('title', '(untitled)')[:52]:52s}"
                    suffix = f"[{ds_uid}]" if len(DS_UIDS) > 1 else ""
                    print(f"{prefix} | {status} {detail}{flag} {suffix}")
    print(f"\n{tested} panel queries checked across {len(DS_UIDS)} datasource(s)")
    print(f"{f'FAILURES: {failures}' if failures else 'ALL PANEL QUERIES OK'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
