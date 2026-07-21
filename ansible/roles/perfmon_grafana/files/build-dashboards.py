#!/usr/bin/env python3
"""Generate the PerformanceMonitor Grafana dashboards.

Dashboard source is split into per-dashboard modules under dashboard_defs/.
Shared helpers live in dashboard_defs/_shared.py.

Usage:
  python3 build-dashboards.py
  python3 build-dashboards.py --fleet-instances sql01,sql02
  python3 build-dashboards.py --output /custom/path
"""

import argparse
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from dashboard_defs._shared import OUT, reset_id
from dashboard_defs.fleet import fleet, fleet_static, fleet_v2
from dashboard_defs.instance_overview import instance_overview
from dashboard_defs.queries import queries
from dashboard_defs.waits import waits
from dashboard_defs.blocking import blocking
from dashboard_defs.memory import memory
from dashboard_defs.collection import collection
from dashboard_defs.system_events import system_events
from dashboard_defs.query_history import query_history
from dashboard_defs.proc_history import proc_history
from dashboard_defs.deadlock_detail import deadlock_detail
from dashboard_defs.wait_drill_down import wait_drill_down
from dashboard_defs.finops.recommendations import recommendations
from dashboard_defs.finops.utilization import utilization
from dashboard_defs.finops.database_resources import database_resources
from dashboard_defs.finops.storage_growth import storage_growth
from dashboard_defs.finops.object_sizes import object_sizes
from dashboard_defs.finops.index_usage import index_usage
from dashboard_defs.finops.locking import locking
from dashboard_defs.finops.database_sizes import database_sizes
from dashboard_defs.finops.index_analysis import index_analysis
from dashboard_defs.finops.optimization import optimization
from dashboard_defs.finops.high_impact import high_impact
from dashboard_defs.finops.application_connections import application_connections
from dashboard_defs.finops.server_inventory import server_inventory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PerformanceMonitor Grafana dashboards."
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        help=(
            "Root directory to write dashboard JSON files into. "
            "All dashboards (perfmon and FinOps) are written to <DIR>/perfmon/. "
            "Defaults to grafana/dashboards/ relative to the build-dashboards.py script location."
        ),
    )
    parser.add_argument(
        "--fleet-instances",
        metavar="NAMES|@FILE",
        help=(
            "Hostnames to bake into a static fleet dashboard. "
            "Pass a comma-separated list (sql01,sql02) or @path/to/file.txt "
            "where the file has one hostname per line (blank lines and # comments ignored). "
            "Produces a single Mixed-datasource table sortable by severity score. "
            "Without this flag the default dynamic fleet is written, which discovers "
            "instances automatically from Grafana datasources but cannot sort "
            "across instances."
        ),
    )
    args = parser.parse_args()

    if args.output:
        out = pathlib.Path(args.output) / "perfmon"
    else:
        out = OUT

    dashboards = {
        "instance-overview.json": instance_overview,
        "query-performance.json": queries,
        "query-history.json": query_history,
        "proc-history.json": proc_history,
        "waits-resources.json": waits,
        "blocking-deadlocks.json": blocking,
        "deadlock-detail.json": deadlock_detail,
        "wait-drill-down.json": wait_drill_down,
        "memory.json": memory,
        "collection-health.json": collection,
        "system-events.json": system_events,
    }

    finops_dashboards = {
        "recommendations.json": recommendations,
        "utilization.json": utilization,
        "database-resources.json": database_resources,
        "storage-growth.json": storage_growth,
        "object-sizes.json": object_sizes,
        "index-usage.json": index_usage,
        "locking.json": locking,
        "database-sizes.json": database_sizes,
        "index-analysis.json": index_analysis,
        "optimization.json": optimization,
        "high-impact.json": high_impact,
        "application-connections.json": application_connections,
        "server-inventory.json": server_inventory,
    }

    out.mkdir(parents=True, exist_ok=True)
    for fname, builder in dashboards.items():
        reset_id()
        path = out / fname
        path.write_text(json.dumps(builder(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")

    for fname, builder in finops_dashboards.items():
        reset_id()
        path = out / fname
        path.write_text(json.dumps(builder(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")

    fleet_path = out / "fleet-overview.json"
    # Schema-v2 variant of the dynamic fleet - conditional rendering hides
    # filtered-out instances.
    fleet_v2_path = out / "fleet-overview-v2.json"
    reset_id()
    if args.fleet_instances:
        raw = args.fleet_instances.strip()
        if raw.startswith("@"):
            file_path = pathlib.Path(raw[1:])
            if not file_path.exists():
                sys.exit(f"error: fleet instances file not found: {file_path}")
            lines = file_path.read_text(encoding="utf-8").splitlines()
            names = [
                ln.strip()
                for ln in lines
                if ln.strip() and not ln.strip().startswith("#")
            ]
        else:
            names = [n.strip() for n in raw.split(",") if n.strip()]
        fleet_path.write_text(
            json.dumps(fleet_static(names), indent=2, ensure_ascii=False) + "\n"
        )
        print(
            f"wrote {fleet_path} (static fleet, {len(names)} instances, sorted by severity)"
        )
        # static mode covers filtering and sorting natively; drop a stale v2
        # dynamic variant so the output dir has one fleet flavor
        if fleet_v2_path.exists():
            fleet_v2_path.unlink()
            print(f"removed {fleet_v2_path} (static fleet replaces it)")
    else:
        fleet_path.write_text(json.dumps(fleet(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {fleet_path} (dynamic fleet)")
        reset_id()
        fleet_v2_path.write_text(
            json.dumps(fleet_v2(), indent=2, ensure_ascii=False) + "\n"
        )
        print(f"wrote {fleet_v2_path} (dynamic fleet, schema v2)")


if __name__ == "__main__":
    main()
