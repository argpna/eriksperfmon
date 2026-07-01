from ._shared import *


def collection():
    reset_id()
    th_col = thresholds(("green", None), ("yellow", 1), ("red", 3))
    th_age = thresholds(("green", None), ("yellow", 5), ("red", 15))
    panels = []
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
    for i, (title, unit, th, sql) in enumerate(stats):
        panels.append(stat(title, i * 6, 0, 6, 4, sql, unit, th))

    panels.append(
        table(
            "Collector health (last 7 days)",
            0,
            4,
            24,
            11,
            """
SELECT
    ch.collector_name,
    ch.health_status,
    ch.last_success_time,
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
        WHEN N'NEVER_RUN' THEN 1
        WHEN N'STALE' THEN 2
        WHEN N'WARNING' THEN 3
        ELSE 4
    END,
    ch.collector_name;
""",
            overrides=[
                status_colors(
                    "health_status",
                    {
                        "HEALTHY": "green",
                        "WARNING": "yellow",
                        "FAILING": "red",
                        "STALE": "orange",
                        "NEVER_RUN": "red",
                    },
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Collector duration (10 slowest)",
            0,
            15,
            12,
            9,
            [
                target(
                    """
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
                )
            ],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Rows collected (5m buckets)",
            12,
            15,
            12,
            9,
            [
                target(
                    "SELECT time = $__timeGroup(cl.collection_time, '5m'), total_rows = SUM(CONVERT(bigint, cl.rows_collected)) FROM config.collection_log AS cl WHERE $__timeFilter(cl.collection_time) AND cl.collection_status = N'SUCCESS' GROUP BY $__timeGroup(cl.collection_time, '5m') ORDER BY 1;"
                )
            ],
            bars=True,
        )
    )
    panels.append(
        table(
            "Recent collector errors",
            0,
            24,
            24,
            9,
            "SELECT TOP (100) cl.collection_time, cl.collector_name, cl.error_message FROM config.collection_log AS cl WHERE cl.collection_status = N'ERROR' AND $__timeFilter(cl.collection_time) ORDER BY cl.collection_time DESC;",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    panels.append(row("SQL Agent jobs", 33))
    panels.append(
        table(
            "Running SQL Agent jobs",
            0,
            34,
            24,
            9,
            """
SELECT
    rj.job_name,
    rj.start_time,
    current_duration = rj.current_duration_formatted,
    avg_duration = rj.avg_duration_formatted,
    p95_duration = rj.p95_duration_formatted,
    rj.successful_run_count,
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
""",
            overrides=[
                status_colors(
                    "duration_status",
                    {
                        "LONG RUNNING": "red",
                        "ABOVE AVERAGE": "orange",
                        "NO HISTORY": "yellow",
                        "NORMAL": "green",
                    },
                )
            ],
        )
    )

    return dashboard(
        "perfmon-collection",
        "PerfMon · Collection Health",
        panels,
        [instance_var()],
        time_from="now-6h",
    )
