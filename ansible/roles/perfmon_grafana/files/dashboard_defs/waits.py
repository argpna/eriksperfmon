from functools import partial

from ._shared import *


# Upstream tab: Resource Metrics (8 sub-tabs: Server Trends, Wait Stats, TempDB Stats,
# File I/O (sub-tabs: File I/O Latency, File I/O Throughput), Perfmon Counters, Session Stats,
# Latch Stats, Spinlock Stats)
def waits():
    counter_var = {
        "name": "counter",
        "label": "Perfmon counter",
        "type": "query",
        "datasource": DS,
        "query": "SELECT DISTINCT RTRIM(counter_name) FROM collect.perfmon_stats WHERE collection_time >= DATEADD(DAY, -1, SYSDATETIME()) ORDER BY 1;",
        "refresh": 2,
        "multi": False,
        "includeAll": True,
        "hide": 0,
        "options": [],
        "regex": "",
        "current": {"text": "Batch Requests/sec", "value": "Batch Requests/sec"},
        "sort": 1,
        "definition": "SELECT DISTINCT RTRIM(counter_name) FROM collect.perfmon_stats WHERE collection_time >= DATEADD(DAY, -1, SYSDATETIME()) ORDER BY 1;",
        "description": "Pick a counter for the Perfmon chart, or select 'All' to overlay every counter.",
    }
    panels = []

    # Upstream sub-tab: Server Trends
    # Grafana limitation: upstream uses CorrelatedTimelineLanesControl - synchronized
    # lanes with interactive cross-highlighting. Grafana cannot replicate the lane
    # visualization, instead the five metrics are individual panels. dashboard-level
    # graphTooltip=1 (shared crosshair) provides the time-correlation behavior.
    # graphTooltip=2 (sets shared tooltip), but can result in sub-par user experience
    # if the screen is cramped and tooltips overlaps each other.
    wait_ms_sec_sql = """
WITH agg AS (
    SELECT
        ws.collection_time,
        total_wait_ms_delta = SUM(ws.wait_time_ms_delta)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
    GROUP BY ws.collection_time
),
timed AS (
    SELECT
        collection_time,
        total_wait_ms_delta,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    wait_ms_sec = CONVERT(decimal(18,4), CAST(total_wait_ms_delta AS decimal(19,4)) / interval_seconds)
FROM timed
WHERE interval_seconds > 0
ORDER BY collection_time;
"""
    io_latency_sql = """
SELECT
    time = fio.collection_time,
    avg_read_ms = SUM(fio.io_stall_read_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_reads_delta), 0),
    avg_write_ms = SUM(fio.io_stall_write_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_writes_delta), 0)
FROM collect.file_io_stats AS fio
WHERE $__timeFilter(fio.collection_time)
GROUP BY fio.collection_time
ORDER BY fio.collection_time;
"""

    # Upstream ref: RefreshServerTrendsAsync (ResourceMetricsContent.xaml.cs)
    y = subtab(
        panels,
        "Server Trends",
        0,
        [
            (
                5,
                7,
                partial(
                    timeseries,
                    "CPU %",
                    targets=[
                        target(
                            "SELECT time = cus.sample_time, cpu_pct = ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) FROM collect.cpu_utilization_stats AS cus WHERE $__timeFilter(cus.sample_time) ORDER BY cus.sample_time;"
                        )
                    ],
                    unit="percent",
                    max_=100,
                ),
            ),
            (
                5,
                7,
                partial(
                    timeseries,
                    "Wait ms/sec",
                    targets=[target(wait_ms_sec_sql)],
                    unit="ms",
                ),
            ),
            (
                5,
                7,
                partial(
                    timeseries,
                    "Blocking & Deadlocking",
                    targets=[
                        target(
                            "SELECT time = bds.collection_time, blocking_events = SUM(bds.blocking_event_count_delta), deadlocks = SUM(bds.deadlock_count_delta) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time) GROUP BY bds.collection_time ORDER BY bds.collection_time;"
                        )
                    ],
                ),
            ),
            (
                5,
                7,
                partial(
                    timeseries,
                    "Buffer Pool MB",
                    targets=[
                        target(
                            "SELECT time = ms.collection_time, buffer_pool_mb = ms.buffer_pool_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
                        )
                    ],
                    unit="decmbytes",
                ),
            ),
            (
                4,
                7,
                partial(
                    timeseries,
                    "I/O Latency",
                    targets=[target(io_latency_sql)],
                    unit="ms",
                ),
            ),
        ],
    )

    # 1-row gap here (hand-rolled in the pre-flow() version too, since Server Trends'
    # panels are h=7 not the usual h=9) - preserved rather than tightened, since closing
    # it is an unrelated layout change outside this refactor's scope.
    top_wait_types_sql = """
WITH top_waits AS (
    SELECT TOP (10) wait_type
    FROM collect.wait_stats
    WHERE $__timeFilter(collection_time)
    GROUP BY wait_type
    ORDER BY MAX(wait_time_ms) DESC
),
wait_rates AS (
    SELECT
        ws.collection_time,
        ws.wait_type,
        ws.wait_time_ms_delta,
        interval_seconds = DATEDIFF(SECOND,
            LAG(ws.collection_time) OVER (PARTITION BY ws.wait_type ORDER BY ws.collection_time),
            ws.collection_time)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
        AND ws.wait_type IN (SELECT wait_type FROM top_waits)
)
SELECT
    time = collection_time,
    metric = wait_type,
    value = CASE
        WHEN interval_seconds > 0
        THEN CONVERT(decimal(18,4), CAST(wait_time_ms_delta AS decimal(19,4)) / interval_seconds)
        ELSE 0
    END
FROM wait_rates
WHERE wait_time_ms_delta >= 0
ORDER BY collection_time;
"""
    signal_resource_wait_sql = """
WITH agg AS (
    SELECT
        ws.collection_time,
        signal_ms = SUM(ws.signal_wait_time_ms_delta),
        resource_ms = SUM(ws.wait_time_ms_delta) - SUM(ws.signal_wait_time_ms_delta)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
    GROUP BY ws.collection_time
),
timed AS (
    SELECT
        collection_time,
        signal_ms,
        resource_ms,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    signal_wait_ms_sec = CONVERT(decimal(18,4), CAST(signal_ms AS decimal(19,4)) / interval_seconds),
    resource_wait_ms_sec = CONVERT(decimal(18,4), CAST(resource_ms AS decimal(19,4)) / interval_seconds)
FROM timed
WHERE interval_seconds > 0
ORDER BY collection_time;
"""
    waiting_tasks_per_type_sql = """
WITH top_waits AS (
    SELECT TOP (10) wait_type
    FROM collect.wait_stats
    WHERE $__timeFilter(collection_time)
    GROUP BY wait_type
    ORDER BY MAX(wait_time_ms) DESC
)
SELECT
    time = ws.collection_time,
    metric = ws.wait_type,
    value = ws.waiting_tasks_count_delta
FROM collect.wait_stats AS ws
WHERE $__timeFilter(ws.collection_time)
    AND ws.wait_type IN (SELECT wait_type FROM top_waits)
ORDER BY ws.collection_time;
"""

    # Upstream ref: RefreshWaitStatsDetailTabAsync (ResourceMetricsContent.WaitStatsDetail.cs)
    y = subtab(
        panels,
        "Wait Stats",
        y + 1,
        [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Top wait types (ms/sec)",
                    targets=[target(top_wait_types_sql)],
                    unit="ms",
                ),
            ),
            (
                8,
                9,
                partial(
                    bargauge,
                    "Top waits - last hour",
                    sql="SELECT tw.wait_type, wait_seconds = CONVERT(float, tw.wait_time_sec) FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
                ),
            ),
            (
                8,
                9,
                partial(
                    timeseries,
                    "Signal vs resource wait ms/sec (CPU pressure indicator)",
                    targets=[target(signal_resource_wait_sql)],
                    unit="ms",
                    stacked=True,
                ),
            ),
            (
                8,
                9,
                partial(
                    timeseries,
                    "Waiting tasks (per wait type)",
                    targets=[target(waiting_tasks_per_type_sql)],
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Wait stats detail (last hour)",
                    sql=f"SELECT tw.wait_type, tw.wait_time_ms, wait_time_sec = CONVERT(decimal(19,2), tw.wait_time_sec), tw.waiting_tasks, tw.signal_wait_ms, tw.resource_wait_ms, tw.avg_wait_ms_per_task, last_seen = {tz_col('tw.last_seen')} FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
                    overrides=[
                        col_datalink(
                            "wait_type",
                            "Drill down: queries with this wait",
                            "/d/perfmon-wait-drill-down?${__url_time_range}&var-instance=${instance}&var-wait_type=${__data.fields.wait_type}",
                        )
                    ],
                ),
            ),
        ],
    )

    tempdb_io_latency_sql = """
SELECT
    time = fio.collection_time,
    avg_read_ms = SUM(fio.io_stall_read_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_reads_delta), 0),
    avg_write_ms = SUM(fio.io_stall_write_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_writes_delta), 0)
FROM collect.file_io_stats AS fio
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name = N'tempdb'
GROUP BY fio.collection_time
ORDER BY fio.collection_time;
"""

    # Upstream ref: RefreshTempdbStatsAsync (ResourceMetricsContent.TempdbStats.cs)
    y = subtab(
        panels,
        "TempDB Stats",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "tempdb space usage",
                    targets=[
                        target(
                            # Upstream ref: LoadTempdbStatsChart (ResourceMetricsContent.
                            # TempdbStats.cs). Deviation: unallocated_mb intentionally
                            # excluded, it is almost always the largest value and flattens
                            # the usage series into an unreadable sliver.
                            "SELECT time = ts.collection_time, user_objects = ts.user_object_reserved_mb, internal_objects = ts.internal_object_reserved_mb, version_store = ts.version_store_reserved_mb FROM collect.tempdb_stats AS ts WHERE $__timeFilter(ts.collection_time) ORDER BY ts.collection_time;"
                        )
                    ],
                    unit="decmbytes",
                    stacked=True,
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "tempdb sessions",
                    targets=[
                        target(
                            "SELECT time = ts.collection_time, sessions_using_tempdb = ts.total_sessions_using_tempdb, top_consumer_mb = ts.top_task_total_mb FROM collect.tempdb_stats AS ts WHERE $__timeFilter(ts.collection_time) ORDER BY ts.collection_time;"
                        )
                    ],
                ),
            ),
            (
                24,
                9,
                partial(
                    timeseries,
                    "tempdb file I/O latency",
                    targets=[target(tempdb_io_latency_sql)],
                    unit="ms",
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "TempDB contention analysis",
                    sql="""
SELECT
    tca.collection_time,
    user_objects_mb = tca.user_object_reserved_mb,
    internal_objects_mb = tca.internal_object_reserved_mb,
    version_store_mb = tca.version_store_reserved_mb,
    total_mb = tca.total_reserved_mb,
    unallocated_mb = tca.unallocated_mb,
    sessions_in_tempdb = tca.total_sessions_using_tempdb,
    top_session_id = tca.top_task_session_id,
    top_session_mb = tca.top_task_total_mb,
    pagelatch_up_ms = tca.pagelatch_up_ms,
    pagelatch_ex_ms = tca.pagelatch_ex_ms,
    alloc_extent_cache_ms = tca.alloc_extent_cache_ms,
    tca.contention_level,
    tca.recommendation
FROM report.tempdb_contention_analysis AS tca;
""",
                    overrides=[
                        status_colors(
                            "contention_level",
                            {
                                "CRITICAL - Allocation contention detected": "red",
                                "HIGH - Version store pressure": "red",
                                "HIGH - Version store > 5GB": "red",
                                "MEDIUM - PAGELATCH_UP contention": "orange",
                                "MEDIUM - Low free space": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
        ],
    )

    # File I/O merges upstream's File I/O Latency and File I/O Throughput sub-tabs into one.
    # Shared CTE for the top 10 most-accessed files, reused across IO latency panels.
    top_files_cte = """\
WITH top_files AS (
    SELECT TOP (10)
        fio.database_name,
        fio.file_name,
        fio.file_type_desc
    FROM collect.file_io_stats AS fio
    WHERE $__timeFilter(fio.collection_time)
        AND fio.database_name IS NOT NULL
        AND fio.database_name <> N'tempdb'
    GROUP BY fio.database_name, fio.file_name, fio.file_type_desc
    HAVING SUM(ISNULL(fio.num_of_reads_delta, 0)) + SUM(ISNULL(fio.num_of_writes_delta, 0)) > 0
    ORDER BY SUM(ISNULL(fio.num_of_reads_delta, 0)) + SUM(ISNULL(fio.num_of_writes_delta, 0)) DESC
)"""

    io_latency_per_file_targets = [
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' read',
    value = CASE
        WHEN ISNULL(fio.num_of_reads_delta, 0) > 0
        THEN CONVERT(decimal(19,2), fio.io_stall_read_ms_delta * 1.0 / fio.num_of_reads_delta)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="A",
        ),
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' write',
    value = CASE
        WHEN ISNULL(fio.num_of_writes_delta, 0) > 0
        THEN CONVERT(decimal(19,2), fio.io_stall_write_ms_delta * 1.0 / fio.num_of_writes_delta)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="B",
        ),
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' read_queued',
    value = CASE
        WHEN ISNULL(fio.num_of_reads_delta, 0) > 0
        THEN CONVERT(decimal(19,2), ISNULL(fio.io_stall_queued_read_ms_delta, 0) * 1.0 / fio.num_of_reads_delta)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="C",
        ),
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' write_queued',
    value = CASE
        WHEN ISNULL(fio.num_of_writes_delta, 0) > 0
        THEN CONVERT(decimal(19,2), ISNULL(fio.io_stall_queued_write_ms_delta, 0) * 1.0 / fio.num_of_writes_delta)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="D",
        ),
    ]
    io_throughput_per_file_targets = [
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' read',
    value = CASE
        WHEN ISNULL(fio.sample_ms_delta, 0) > 0
        THEN CONVERT(decimal(19,4), fio.num_of_bytes_read_delta * 1000.0 / fio.sample_ms_delta / 1048576.0)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="A",
        ),
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' write',
    value = CASE
        WHEN ISNULL(fio.sample_ms_delta, 0) > 0
        THEN CONVERT(decimal(19,4), fio.num_of_bytes_written_delta * 1000.0 / fio.sample_ms_delta / 1048576.0)
        ELSE 0
    END
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="B",
        ),
    ]
    io_counts_per_file_targets = [
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' reads',
    value = CONVERT(float, ISNULL(fio.num_of_reads_delta, 0))
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="A",
        ),
        target(
            f"""
{top_files_cte}
SELECT
    time = fio.collection_time,
    metric = fio.database_name + N' ' + fio.file_type_desc + N' writes',
    value = CONVERT(float, ISNULL(fio.num_of_writes_delta, 0))
FROM collect.file_io_stats AS fio
JOIN top_files AS tf ON tf.database_name = fio.database_name AND tf.file_name = fio.file_name
WHERE $__timeFilter(fio.collection_time)
    AND fio.database_name IS NOT NULL
    AND fio.database_name <> N'tempdb'
ORDER BY fio.collection_time;
""",
            ref="B",
        ),
    ]
    per_file_io_latency_sql = f"""
SELECT
    fil.database_name,
    file_type = fil.file_type,
    fil.file_name,
    fil.latency_issue,
    avg_read_ms = fil.avg_read_latency_ms,
    avg_write_ms = fil.avg_write_latency_ms,
    reads_15min = fil.reads_last_15min,
    writes_15min = fil.writes_last_15min,
    fil.recommendation,
    last_seen = {tz_col('fil.last_seen')}
FROM report.file_io_latency AS fil
WHERE fil.reads_last_15min > 0 OR fil.writes_last_15min > 0
ORDER BY
    CASE fil.latency_issue
        WHEN N'CRITICAL - Read latency > 50ms' THEN 0
        WHEN N'CRITICAL - Write latency > 100ms' THEN 1
        WHEN N'HIGH - Read latency > 20ms' THEN 2
        WHEN N'HIGH - Write latency > 50ms' THEN 3
        ELSE 9
    END;
"""
    io_wait_correlation_sql = """
SELECT
    fiwc.database_name,
    file_type = fiwc.file_type_desc,
    fiwc.file_name,
    fiwc.latency_concern,
    avg_read_ms = fiwc.avg_read_latency_ms,
    avg_write_ms = fiwc.avg_write_latency_ms,
    fiwc.total_reads,
    fiwc.total_writes,
    pageiolatch_sh_ms = fiwc.pageiolatch_sh_ms,
    pageiolatch_ex_ms = fiwc.pageiolatch_ex_ms,
    writelog_ms = fiwc.writelog_ms,
    fiwc.recommendation
FROM report.file_io_wait_correlation AS fiwc
ORDER BY
    CASE fiwc.latency_concern
        WHEN N'CRITICAL - Read > 50ms' THEN 0
        WHEN N'CRITICAL - Write > 100ms' THEN 1
        ELSE 9
    END;
"""

    # Upstream ref: LoadFileIoLatencyChartsAsync / LoadFileIoThroughputChartsAsync (ResourceMetricsContent.FileIoLatency.cs)
    y = subtab(
        panels,
        "File I/O",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "IO latency per file (top 10 by volume)",
                    targets=io_latency_per_file_targets,
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "IO throughput per file (top 10 by volume)",
                    targets=io_throughput_per_file_targets,
                    unit="decmbytes",
                ),
            ),
            (
                24,
                9,
                partial(
                    timeseries,
                    "IO read/write counts per file (top 10 by volume)",
                    targets=io_counts_per_file_targets,
                    unit="short",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Per-file IO latency",
                    sql=per_file_io_latency_sql,
                    overrides=[
                        status_colors(
                            "latency_issue",
                            {
                                "CRITICAL - Read latency > 50ms": "red",
                                "CRITICAL - Write latency > 100ms": "red",
                                "HIGH - Read latency > 20ms": "red",
                                "HIGH - Write latency > 50ms": "red",
                                "MEDIUM - Read latency > 10ms": "orange",
                                "MEDIUM - Write latency > 20ms": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "IO latency & wait correlation",
                    sql=io_wait_correlation_sql,
                    overrides=[
                        status_colors(
                            "latency_concern",
                            {
                                "CRITICAL - Read > 50ms": "red",
                                "CRITICAL - Write > 100ms": "red",
                                "HIGH - Read > 20ms": "red",
                                "HIGH - Write > 50ms": "red",
                                "MEDIUM - Read > 10ms": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
        ],
    )

    # Upstream ref: RefreshPerfmonCountersTabAsync (ResourceMetricsContent.PerfmonCounters.cs)
    y = subtab(
        panels,
        "Perfmon Counters",
        y,
        [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Perfmon counters per second",
                    targets=[
                        target(
                            "SELECT time = ps.collection_time, metric = RTRIM(ps.object_name) + N' • ' + RTRIM(ps.counter_name) + CASE WHEN ps.instance_name = N'' THEN N'' ELSE N' (' + RTRIM(ps.instance_name) + N')' END, value = CONVERT(float, ps.cntr_value_per_second) FROM collect.perfmon_stats AS ps WHERE $__timeFilter(ps.collection_time) AND ps.cntr_value_per_second IS NOT NULL AND ps.cntr_value_per_second >= 0 AND RTRIM(ps.counter_name) IN (${counter:sqlstring}) ORDER BY ps.collection_time;"
                        )
                    ],
                    unit="ops",
                ),
            )
        ],
    )

    session_top_consumers_sql = f"""
SELECT TOP (1)
    collection_time = {tz_col('ss.collection_time')},
    ss.databases_with_connections,
    ss.top_application_name,
    ss.top_application_connections,
    ss.top_host_name,
    ss.top_host_connections
FROM collect.session_stats AS ss
ORDER BY ss.collection_time DESC;
"""
    # Upstream ref: RefreshSessionStatsAsync (ResourceMetricsContent.SessionStats.cs)
    y = subtab(
        panels,
        "Session Stats",
        y,
        [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Sessions",
                    targets=[
                        target(
                            "SELECT time = ss.collection_time, total = ss.total_sessions, running = ss.running_sessions, sleeping = ss.sleeping_sessions, background = ss.background_sessions, dormant = ss.dormant_sessions, idle_over_30min = ss.idle_sessions_over_30min, waiting_for_memory = ss.sessions_waiting_for_memory FROM collect.session_stats AS ss WHERE $__timeFilter(ss.collection_time) ORDER BY ss.collection_time;"
                        )
                    ],
                ),
            ),
            (
                24,
                5,
                partial(
                    table,
                    "Session top consumers (latest)",
                    sql=session_top_consumers_sql,
                ),
            ),
        ],
    )

    latch_wait_ms_sec_sql = """
WITH top_latches AS (
    SELECT TOP (5) latch_class
    FROM collect.latch_stats
    WHERE $__timeFilter(collection_time)
        AND wait_time_ms_delta IS NOT NULL
    GROUP BY latch_class
    ORDER BY SUM(wait_time_ms_delta) DESC
)
SELECT
    time = ls.collection_time,
    metric = ls.latch_class,
    value = CASE
        WHEN ISNULL(ls.sample_interval_seconds, 0) > 0
        THEN CONVERT(decimal(18,4), CAST(ls.wait_time_ms_delta AS decimal(19,4)) / ls.sample_interval_seconds)
        ELSE 0
    END
FROM collect.latch_stats AS ls
WHERE $__timeFilter(ls.collection_time)
    AND ls.latch_class IN (SELECT latch_class FROM top_latches)
ORDER BY ls.collection_time;
"""
    latch_contention_sql = f"""
SELECT TOP (200)
    ls.collection_id,
    collection_time = {tz_col('ls.collection_time')},
    server_start_time = {tz_col('ls.server_start_time')},
    ls.latch_class,
    ls.waiting_requests_count,
    ls.wait_time_ms,
    ls.max_wait_time_ms,
    ls.waiting_requests_count_delta,
    ls.wait_time_ms_delta,
    ls.max_wait_time_ms_delta,
    ls.sample_interval_seconds,
    severity =
        CASE
            WHEN ISNULL(ls.wait_time_ms_delta, 0) > 10000 THEN N'HIGH'
            WHEN ISNULL(ls.wait_time_ms_delta, 0) > 5000 THEN N'MEDIUM'
            ELSE N'LOW'
        END,
    latch_description =
        CASE ls.latch_class
            WHEN N'BUFFER' THEN N'Synchronize short term access to database pages.'
            WHEN N'BUFFER_POOL_GROW' THEN N'Buffer pool grow operations.'
            WHEN N'DATABASE_CHECKPOINT' THEN N'Serialize checkpoints within a database.'
            WHEN N'FCB' THEN N'Synchronize access to the file control block.'
            WHEN N'FGCB_ADD_REMOVE' THEN N'Synchronize file add/drop/grow/shrink operations.'
            WHEN N'LOG_MANAGER' THEN N'Transaction log manager synchronization.'
            ELSE N'Internal SQL Server synchronization.'
        END,
    recommendation =
        CASE
            WHEN ls.latch_class LIKE N'PAGEIOLATCH%' THEN N'I/O bottleneck - check disk latency, add memory'
            WHEN ls.latch_class LIKE N'PAGELATCH%' THEN N'Page contention - check for hot pages, tempdb issues'
            WHEN ls.latch_class = N'BUFFER' THEN N'Buffer pool contention - check for memory pressure'
            WHEN ls.latch_class LIKE N'ACCESS_METHODS%' THEN N'Index/heap access contention'
            WHEN ls.latch_class LIKE N'ALLOC%' THEN N'Allocation contention - consider pre-sizing files'
            WHEN ls.latch_class IN (N'LOG_MANAGER', N'LOGCACHE_ACCESS') THEN N'Log contention - check log disk'
            ELSE N'Review latch class documentation'
        END
FROM collect.latch_stats AS ls
WHERE $__timeFilter(ls.collection_time)
ORDER BY ls.collection_time DESC, ls.wait_time_ms DESC;
"""
    # Upstream ref: RefreshLatchStatsAsync (ResourceMetricsContent.LatchStats.cs)
    y = subtab(
        panels,
        "Latch Stats",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Latch wait ms/sec (top 5)",
                    targets=[target(latch_wait_ms_sec_sql)],
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Latch contention (raw)",
                    sql=latch_contention_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    spinlock_collisions_sql = """
WITH top_spinlocks AS (
    SELECT TOP (5) spinlock_name
    FROM collect.spinlock_stats
    WHERE $__timeFilter(collection_time)
        AND collisions_delta IS NOT NULL
    GROUP BY spinlock_name
    ORDER BY SUM(collisions_delta) DESC
)
SELECT
    time = ss.collection_time,
    metric = ss.spinlock_name,
    value = CASE
        WHEN ISNULL(ss.sample_interval_seconds, 0) > 0
        THEN CONVERT(decimal(18,4), CAST(ss.collisions_delta AS decimal(19,4)) / ss.sample_interval_seconds)
        ELSE 0
    END
FROM collect.spinlock_stats AS ss
WHERE $__timeFilter(ss.collection_time)
    AND ss.spinlock_name IN (SELECT spinlock_name FROM top_spinlocks)
ORDER BY ss.collection_time;
"""
    spinlock_contention_sql = f"""
SELECT TOP (200)
    ss.collection_id,
    collection_time = {tz_col('ss.collection_time')},
    server_start_time = {tz_col('ss.server_start_time')},
    ss.spinlock_name,
    ss.collisions,
    ss.spins,
    ss.spins_per_collision,
    ss.sleep_time,
    ss.backoffs,
    ss.collisions_delta,
    ss.spins_delta,
    ss.sleep_time_delta,
    ss.backoffs_delta,
    ss.sample_interval_seconds,
    spinlock_description =
        CASE ss.spinlock_name
            WHEN N'BACKUP_CTX' THEN N'Page I/O during backup - high spins during checkpoint/lazywriter.'
            WHEN N'DBTABLE' THEN N'In-memory data structure access for database properties.'
            WHEN N'DP_LIST' THEN N'Dirty page list with indirect checkpoint enabled.'
            WHEN N'LOCK_HASH' THEN N'Lock manager hash table access.'
            WHEN N'LOCK_RW_SECURITY_CACHE' THEN N'Security token and access check cache.'
            WHEN N'SOS_CACHESTORE' THEN N'Various in-memory caches (plan cache, temp tables).'
            ELSE N'Internal use only.'
        END
FROM collect.spinlock_stats AS ss
WHERE $__timeFilter(ss.collection_time)
ORDER BY ss.collection_time DESC, ss.spins DESC;
"""
    # Upstream ref: RefreshSpinlockStatsAsync (ResourceMetricsContent.SpinlockStats.cs)
    subtab(
        panels,
        "Spinlock Stats",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Spinlock collisions/sec (top 5)",
                    targets=[target(spinlock_collisions_sql)],
                    unit="ops",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Spinlock contention (raw)",
                    sql=spinlock_contention_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    return dashboard(
        "perfmon-waits",
        "PerfMon · Resource Metrics",
        panels,
        [instance_var(), counter_var],
        graph_tooltip=1,
    )
