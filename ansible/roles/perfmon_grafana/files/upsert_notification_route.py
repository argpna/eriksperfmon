#!/usr/bin/env python3
"""
Upsert the perfmon notification route in Grafana's notification policy.

Reads the current policy via the Grafana provisioning API, removes any existing
route that targets team=perfmon, appends the requested route, and writes it back.
All other routes are left untouched.

Required args: --grafana-url, --receiver
Required env:  GRAFANA_API_KEY
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

# Prometheus label matcher syntax: <label><op><value>, value optionally quoted.
_MATCHER_RE = re.compile(
    r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"?([^"]*)"?\s*$'
)


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _is_perfmon_route(route):
    # matchers is the upstream Prometheus format and Grafana's preferred direction
    for m in route.get("matchers") or []:
        match = _MATCHER_RE.match(str(m))
        if (
            match
            and match.group(1) == "team"
            and match.group(2) == "="
            and match.group(3) == "perfmon"
        ):
            return True
    # object_matchers is Grafana's older structured format, check as fallback
    for m in route.get("object_matchers") or []:
        if len(m) >= 3 and m[0] == "team" and m[1] == "=" and m[2] == "perfmon":
            return True
    return False


def _put_policy(url, policy, hdrs):
    body = json.dumps(policy).encode()
    req = urllib.request.Request(url, data=body, headers=hdrs, method="PUT")
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"PUT {url} failed: {exc.code} {exc.reason}\n{body}", file=sys.stderr)
        sys.exit(1)


def _remove_route(url, policy, hdrs):
    existing_routes = policy.get("routes") or []
    other_routes = [r for r in existing_routes if not _is_perfmon_route(r)]
    if len(other_routes) == len(existing_routes):
        print("ok")
        return
    policy["routes"] = other_routes
    _put_policy(url, policy, hdrs)
    print("changed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grafana-url", required=True)
    parser.add_argument("--receiver")
    parser.add_argument("--group-wait", default="30s")
    parser.add_argument("--group-interval", default="5m")
    parser.add_argument("--repeat-interval", default="4h")
    parser.add_argument("--group-by", default="team,instance,alertname")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the perfmon route instead of upserting it",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GRAFANA_API_KEY", "")
    if not api_key:
        print("GRAFANA_API_KEY env var is required", file=sys.stderr)
        sys.exit(1)

    hdrs = _headers(api_key)
    base = args.grafana_url.rstrip("/")
    url = f"{base}/api/v1/provisioning/policies"

    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req) as resp:
            policy = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"GET {url} failed: {exc.code} {exc.reason}", file=sys.stderr)
        sys.exit(1)

    if args.remove:
        _remove_route(url, policy, hdrs)
        return

    if not args.receiver:
        print("--receiver is required when not using --remove", file=sys.stderr)
        sys.exit(1)

    existing_routes = policy.get("routes") or []
    other_routes = [r for r in existing_routes if not _is_perfmon_route(r)]

    perfmon_route = {
        "matchers": ["team=perfmon"],
        "receiver": args.receiver,
        "group_by": args.group_by.split(","),
        "group_wait": args.group_wait,
        "group_interval": args.group_interval,
        "repeat_interval": args.repeat_interval,
    }

    existing_perfmon = next((r for r in existing_routes if _is_perfmon_route(r)), None)
    if existing_perfmon is not None:
        keys = [
            "receiver",
            "group_by",
            "group_wait",
            "group_interval",
            "repeat_interval",
        ]
        if all(existing_perfmon.get(k) == perfmon_route.get(k) for k in keys):
            print("ok")
            return

    policy["routes"] = other_routes + [perfmon_route]
    _put_policy(url, policy, hdrs)
    print("changed")


if __name__ == "__main__":
    main()
