from .._shared import *

# Upstream ref: GetFinOpsApplicationResourceUsageAsync (DatabaseService.FinOps.Workload.cs)
_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    application_name = fau.application_name,
    avg_connections = fau.avg_connections,
    max_connections = fau.max_connections,
    sample_count = fau.sample_count,
    first_seen = {tz_col('fau.first_seen')},
    last_seen = {tz_col('fau.last_seen')}
FROM report.finops_application_resource_usage AS fau
ORDER BY
    fau.max_connections DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def application_connections():
    panels = [
        table(
            "Application Connections (24h)",
            0,
            0,
            24,
            12,
            _SQL,
            sort_by=[{"displayName": "max_connections", "desc": True}],
        )
    ]
    return finops_dashboard(
        "finops-application-connections",
        "FinOps · Application Connections",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
