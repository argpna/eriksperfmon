from ._shared import *


# Upstream tab: Resource Metrics (8 sub-tabs: Server Trends, Wait Stats, TempDB Stats,
# File I/O (sub-tabs: File I/O Latency, File I/O Throughput), Perfmon Counters, Session Stats,
# Latch Stats, Spinlock Stats)
def waits():
    reset_id()
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
    # Upstream uses CorrelatedTimelineLanesControl - synchronized lanes with interactive
    # cross-highlighting. Grafana cannot replicate the lane visualization, instead the five
    # metrics are individual panels. dashboard-level graphTooltip=1 (shared crosshair)
    # provides the time-correlation behavior.
    panels.append(row("Server Trends", 0))
    panels.append(
        timeseries(
            "CPU %",
            0,
            1,
            5,
            7,
            [
                target(
                    "SELECT time = cus.sample_time, cpu_pct = ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) FROM collect.cpu_utilization_stats AS cus WHERE $__timeFilter(cus.sample_time) ORDER BY cus.sample_time;"
                )
            ],
            unit="percent",
            max_=100,
        )
    )
    panels.append(
        timeseries(
            "Wait ms/sec",
            5,
            1,
            5,
            7,
            [target("""
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
""")],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Blocking & Deadlocking",
            10,
            1,
            5,
            7,
            [
                target(
                    "SELECT time = bds.collection_time, blocking_events = SUM(bds.blocking_event_count_delta), deadlocks = SUM(bds.deadlock_count_delta) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time) GROUP BY bds.collection_time ORDER BY bds.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Buffer Pool MB",
            15,
            1,
            5,
            7,
            [
                target(
                    "SELECT time = ms.collection_time, buffer_pool_mb = ms.buffer_pool_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
                )
            ],
            unit="decmbytes",
        )
    )
    panels.append(
        timeseries(
            "I/O Latency",
            20,
            1,
            4,
            7,
            [target("""
SELECT
    time = fio.collection_time,
    avg_read_ms = SUM(fio.io_stall_read_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_reads_delta), 0),
    avg_write_ms = SUM(fio.io_stall_write_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_writes_delta), 0)
FROM collect.file_io_stats AS fio
WHERE $__timeFilter(fio.collection_time)
GROUP BY fio.collection_time
ORDER BY fio.collection_time;
""")],
            unit="ms",
        )
    )

    # Upstream sub-tab: Wait Stats
    panels.append(row("Wait Stats", 9))
    panels.append(
        timeseries(
            "Top wait types (ms/sec)",
            0,
            10,
            24,
            9,
            [target("""
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
""")],
            unit="ms",
        )
    )
    panels.append(
        bargauge(
            "Top waits - last hour",
            0,
            19,
            8,
            9,
            "SELECT tw.wait_type, wait_seconds = CONVERT(float, tw.wait_time_sec) FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
        )
    )
    panels.append(
        timeseries(
            "Signal vs resource wait ms/sec (CPU pressure indicator)",
            8,
            19,
            8,
            9,
            [target("""
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
""")],
            unit="ms",
            stacked=True,
        )
    )
    panels.append(
        timeseries(
            "Waiting tasks (per wait type)",
            16,
            19,
            8,
            9,
            [target("""
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
    value = ws.waiting_tasks_count
FROM collect.wait_stats AS ws
WHERE $__timeFilter(ws.collection_time)
    AND ws.wait_type IN (SELECT wait_type FROM top_waits)
ORDER BY ws.collection_time;
""")],
        )
    )
    panels.append(
        table(
            "Wait stats detail (last hour)",
            0,
            28,
            24,
            9,
            "SELECT tw.wait_type, tw.wait_time_ms, wait_time_sec = CONVERT(decimal(19,2), tw.wait_time_sec), tw.waiting_tasks, tw.signal_wait_ms, tw.resource_wait_ms, tw.avg_wait_ms_per_task, tw.last_seen FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
            overrides=[
                col_datalink(
                    "wait_type",
                    "Drill down: queries with this wait",
                    "/d/perfmon-wait-drill-down?${__url_time_range}&var-instance=${instance}&var-wait_type=${__data.fields.wait_type}",
                )
            ],
        )
    )

    # Upstream sub-tab: TempDB Stats
    panels.append(row("TempDB Stats", 37))
    panels.append(
        timeseries(
            "tempdb space usage",
            0,
            38,
            12,
            9,
            [
                target(
                    "SELECT time = ts.collection_time, user_objects = ts.user_object_reserved_mb, internal_objects = ts.internal_object_reserved_mb, version_store = ts.version_store_reserved_mb, unallocated = ts.unallocated_mb FROM collect.tempdb_stats AS ts WHERE $__timeFilter(ts.collection_time) ORDER BY ts.collection_time;"
                )
            ],
            unit="decmbytes",
            stacked=True,
        )
    )
    panels.append(
        timeseries(
            "tempdb sessions",
            12,
            38,
            12,
            9,
            [
                target(
                    "SELECT time = ts.collection_time, sessions_using_tempdb = ts.total_sessions_using_tempdb, top_consumer_mb = ts.top_task_total_mb FROM collect.tempdb_stats AS ts WHERE $__timeFilter(ts.collection_time) ORDER BY ts.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "TempDB contention analysis",
            0,
            47,
            24,
            9,
            """
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
        )
    )

    # Upstream sub-tab: File I/O (sub-tabs: File I/O Latency, File I/O Throughput)
    panels.append(row("File I/O", 56))

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

    panels.append(
        timeseries(
            "IO latency per file (top 10 by volume)",
            0,
            57,
            12,
            9,
            [
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
            ],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "IO throughput",
            12,
            57,
            12,
            9,
            [target("""
SELECT
    time = fio.collection_time,
    read_mb_sec = SUM(CASE
        WHEN ISNULL(fio.sample_ms_delta, 0) > 0
        THEN CONVERT(decimal(19,4), fio.num_of_bytes_read_delta * 1000.0 / fio.sample_ms_delta / 1048576.0)
        ELSE 0
    END),
    write_mb_sec = SUM(CASE
        WHEN ISNULL(fio.sample_ms_delta, 0) > 0
        THEN CONVERT(decimal(19,4), fio.num_of_bytes_written_delta * 1000.0 / fio.sample_ms_delta / 1048576.0)
        ELSE 0
    END)
FROM collect.file_io_stats AS fio
WHERE $__timeFilter(fio.collection_time)
GROUP BY fio.collection_time
ORDER BY fio.collection_time;
""")],
            unit="decmbytes",
        )
    )
    panels.append(
        timeseries(
            "IO read/write counts per file (top 10 by volume)",
            0,
            66,
            24,
            9,
            [
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
            ],
            unit="short",
        )
    )
    panels.append(
        table(
            "Per-file IO latency",
            0,
            75,
            12,
            9,
            """
SELECT
    fil.database_name,
    file_type = fil.file_type,
    fil.file_name,
    avg_read_ms = fil.avg_read_latency_ms,
    avg_write_ms = fil.avg_write_latency_ms,
    reads_15min = fil.reads_last_15min,
    writes_15min = fil.writes_last_15min,
    fil.latency_issue,
    fil.recommendation
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
""",
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
        )
    )
    panels.append(
        table(
            "IO latency & wait correlation",
            12,
            75,
            12,
            9,
            """
SELECT
    fiwc.database_name,
    file_type = fiwc.file_type_desc,
    fiwc.file_name,
    avg_read_ms = fiwc.avg_read_latency_ms,
    avg_write_ms = fiwc.avg_write_latency_ms,
    fiwc.total_reads,
    fiwc.total_writes,
    pageiolatch_sh_ms = fiwc.pageiolatch_sh_ms,
    pageiolatch_ex_ms = fiwc.pageiolatch_ex_ms,
    writelog_ms = fiwc.writelog_ms,
    fiwc.latency_concern,
    fiwc.recommendation
FROM report.file_io_wait_correlation AS fiwc
ORDER BY
    CASE fiwc.latency_concern
        WHEN N'CRITICAL - Read > 50ms' THEN 0
        WHEN N'CRITICAL - Write > 100ms' THEN 1
        ELSE 9
    END;
""",
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
        )
    )

    # Upstream sub-tab: Perfmon Counters
    panels.append(row("Perfmon Counters", 84))
    panels.append(
        timeseries(
            "Perfmon counters per second",
            0,
            85,
            24,
            9,
            [
                target(
                    "SELECT time = ps.collection_time, metric = RTRIM(ps.object_name) + N' • ' + RTRIM(ps.counter_name) + CASE WHEN ps.instance_name = N'' THEN N'' ELSE N' (' + RTRIM(ps.instance_name) + N')' END, value = CONVERT(float, ps.cntr_value_per_second) FROM collect.perfmon_stats AS ps WHERE $__timeFilter(ps.collection_time) AND ps.cntr_value_per_second IS NOT NULL AND ps.cntr_value_per_second >= 0 AND RTRIM(ps.counter_name) IN (${counter:sqlstring}) ORDER BY ps.collection_time;"
                )
            ],
            unit="ops",
        )
    )

    # Upstream sub-tab: Session Stats
    panels.append(row("Session Stats", 94))
    panels.append(
        timeseries(
            "Sessions",
            0,
            95,
            24,
            9,
            [
                target(
                    "SELECT time = ss.collection_time, total = ss.total_sessions, running = ss.running_sessions, sleeping = ss.sleeping_sessions, waiting_for_memory = ss.sessions_waiting_for_memory FROM collect.session_stats AS ss WHERE $__timeFilter(ss.collection_time) ORDER BY ss.collection_time;"
                )
            ],
        )
    )

    # Upstream sub-tab: Latch Stats
    panels.append(row("Latch Stats", 104))
    panels.append(
        timeseries(
            "Latch wait ms/sec (top 10)",
            0,
            105,
            12,
            9,
            [target("""
WITH top_latches AS (
    SELECT TOP (10) latch_class
    FROM collect.latch_stats
    WHERE $__timeFilter(collection_time)
    GROUP BY latch_class
    ORDER BY MAX(wait_time_ms) DESC
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
""")],
            unit="ms",
        )
    )
    panels.append(
        table(
            "Latch contention (raw)",
            12,
            105,
            12,
            9,
            """
SELECT TOP (200)
    ls.collection_id,
    ls.collection_time,
    ls.server_start_time,
    ls.latch_class,
    ls.waiting_requests_count,
    ls.wait_time_ms,
    ls.max_wait_time_ms,
    ls.waiting_requests_count_delta,
    ls.wait_time_ms_delta,
    ls.max_wait_time_ms_delta,
    ls.sample_interval_seconds
FROM collect.latch_stats AS ls
WHERE $__timeFilter(ls.collection_time)
ORDER BY ls.collection_time DESC, ls.wait_time_ms DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Spinlock Stats
    panels.append(row("Spinlock Stats", 114))
    panels.append(
        table(
            "Spinlock contention (raw)",
            0,
            115,
            24,
            9,
            """
SELECT TOP (200)
    ss.collection_id,
    ss.collection_time,
    ss.server_start_time,
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
    ss.sample_interval_seconds
FROM collect.spinlock_stats AS ss
WHERE $__timeFilter(ss.collection_time)
ORDER BY ss.collection_time DESC, ss.spins DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    return dashboard(
        "perfmon-waits",
        "PerfMon · Resource Metrics",
        panels,
        [instance_var(), counter_var],
        # graph_tooltip=2,  # shared tooltip, a bit noisy, but matches erik's co-relation tooltip style
    )
