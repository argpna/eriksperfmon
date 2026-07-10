from functools import partial

from ._shared import *

# Upstream ref: Collection Health / Running Jobs section of the Overview tab
# (DatabaseService.cs, DatabaseService.Overview.cs), broken out as its own dashboard.


def collection():
    th_col = thresholds(("green", None), ("yellow", 1), ("red", 3))
    th_age = thresholds(("green", None), ("yellow", 5), ("red", 15))
    panels = []
    # No upstream ref: stat tiles are local rollups over config.collection_log, no
    # single upstream method. "Collectors unhealthy" deviates from upstream's
    # health_status-based collectors_failing count and instead flags recent
    # (5-minute) errors, since the full health_status breakdown is shown per-collector
    # in the table below.
    stats = [
        (
            "Collectors reporting",
            "short",
            thresholds(("blue", None)),
            "SELECT v = COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(DAY, -7, SYSDATETIME());",
        ),
        (
            "Collectors unhealthy",
            "short",
            th_col,
            "SELECT v = COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR';",
        ),
        (
            "Collection age",
            "m",
            th_age,
            "SELECT v = DATEDIFF(MINUTE, MAX(cl.collection_time), SYSDATETIME()) FROM config.collection_log AS cl;",
        ),
        (
            "Rows collected (range)",
            "short",
            thresholds(("blue", None)),
            "SELECT v = ISNULL(SUM(CONVERT(bigint, cl.rows_collected)), 0) FROM config.collection_log AS cl WHERE $__timeFilter(cl.collection_time) AND cl.collection_status = N'SUCCESS';",
        ),
    ]

    # Upstream ref: GetCollectionHealthAsync (DatabaseService.cs)
    collector_health_sql = f"""
SELECT
    ch.collector_name,
    ch.health_status,
    last_success_time = {tz_col('ch.last_success_time')},
    ch.hours_since_success,
    ch.failure_rate_percent,
    ch.total_runs_7d,
    ch.failed_runs_7d,
    ch.avg_duration_ms,
    ch.total_rows_collected_7d
FROM report.collection_health AS ch
ORDER BY
    CASE ch.health_status
        WHEN N'FAILING' THEN 0
        WHEN N'STALE' THEN 1
        WHEN N'WARNING' THEN 2
        WHEN N'HEALTHY' THEN 3
        WHEN N'NEVER_RUN' THEN 4
        ELSE 5
    END,
    ch.collector_name;
"""
    # Upstream ref: GetCollectionDurationLogsAsync (DatabaseService.cs). Deviation:
    # scoped here to the 10 slowest collectors by avg duration - upstream returns
    # every collector as a scrollable WPF grid, which would be an unreadable timeseries.
    collector_duration_sql = """
SELECT
    time = cl.collection_time,
    metric = cl.collector_name,
    value = CONVERT(float, cl.duration_ms)
FROM config.collection_log AS cl
WHERE $__timeFilter(cl.collection_time)
    AND cl.collection_status = N'SUCCESS'
    AND cl.collector_name IN (
        SELECT TOP (10)
            c2.collector_name
        FROM config.collection_log AS c2
        WHERE $__timeFilter(c2.collection_time)
            AND c2.collection_status = N'SUCCESS'
        GROUP BY c2.collector_name
        ORDER BY AVG(c2.duration_ms * 1.0) DESC
    )
ORDER BY cl.collection_time;
"""
    # Upstream ref: GetRunningJobsAsync (DatabaseService.Overview.cs)
    running_jobs_sql = f"""
SELECT
    collection_time = {tz_col('rj.collection_time')},
    rj.job_name,
    rj.job_id,
    rj.job_enabled,
    start_time = {tz_col('rj.start_time')},
    current_duration = rj.current_duration_formatted,
    rj.current_duration_seconds,
    avg_duration = rj.avg_duration_formatted,
    rj.avg_duration_seconds,
    p95_duration = rj.p95_duration_formatted,
    rj.p95_duration_seconds,
    rj.successful_run_count,
    rj.is_running_long,
    pct_of_avg = rj.percent_of_average,
    rj.duration_status
FROM report.running_jobs AS rj
ORDER BY
    CASE rj.duration_status
        WHEN N'LONG RUNNING' THEN 0
        WHEN N'ABOVE AVERAGE' THEN 1
        ELSE 3
    END,
    rj.current_duration_seconds DESC;
"""

    y = flow(
        panels,
        0,
        [
            (6, 4, partial(stat, title, sql=sql, unit=unit, th=th))
            for title, unit, th, sql in stats
        ]
        + [
            (
                24,
                11,
                partial(
                    table,
                    "Collector health (last 7 days)",
                    sql=collector_health_sql,
                    overrides=[status_colors("health_status", HEALTH_STATUS_COLORS)],
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Collector duration (10 slowest)",
                    targets=[target(collector_duration_sql)],
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Rows collected (5m buckets)",
                    # No upstream ref: upstream only shows total_rows_collected_7d as a
                    # rolled-up number; this is a Grafana-native trend over the same
                    # config.collection_log.rows_collected data.
                    targets=[
                        target(
                            "SELECT time = $__timeGroup(cl.collection_time, '5m'), total_rows = SUM(CONVERT(bigint, cl.rows_collected)) FROM config.collection_log AS cl WHERE $__timeFilter(cl.collection_time) AND cl.collection_status = N'SUCCESS' GROUP BY $__timeGroup(cl.collection_time, '5m') ORDER BY 1;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Recent collector errors",
                    # No upstream ref: upstream has no dedicated error-browsing panel,
                    # only the aggregate failure_rate_percent in the health grid above.
                    sql=f"SELECT TOP (100) collection_time = {tz_col('cl.collection_time')}, cl.collector_name, cl.error_message FROM config.collection_log AS cl WHERE cl.collection_status = N'ERROR' AND $__timeFilter(cl.collection_time) ORDER BY cl.collection_time DESC;",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream ref: GetRunningJobsAsync (DatabaseService.Overview.cs)
    subtab(
        panels,
        "SQL Agent jobs",
        y,
        [
            (
                24,
                9,
                partial(
                    table,
                    "Running SQL Agent jobs",
                    sql=running_jobs_sql,
                    overrides=[
                        status_colors("duration_status", DURATION_STATUS_COLORS)
                    ],
                ),
            )
        ],
    )

    return dashboard(
        "perfmon-collection",
        "PerfMon · Collection Health",
        panels,
        [instance_var()],
        time_from="now-6h",
    )
