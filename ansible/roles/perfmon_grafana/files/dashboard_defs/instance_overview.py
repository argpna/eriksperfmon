from functools import partial

from ._shared import *


def instance_overview():
    panels = []
    th_cpu = thresholds(("green", None), ("yellow", 70), ("red", 90))
    th_blk = thresholds(("green", None), ("yellow", 1), ("red", 10))
    th_dlk = thresholds(("green", None), ("yellow", 1), ("red", 5))
    th_col = thresholds(("green", None), ("yellow", 1), ("red", 3))
    th_none = thresholds(("blue", None))

    stats = [
        (
            "CPU (now)",
            "percent",
            th_cpu,
            "SELECT TOP (1) cpu = ISNULL(total_cpu_utilization, sqlserver_cpu_utilization) FROM collect.cpu_utilization_stats ORDER BY collection_time DESC;",
        ),
        (
            "Buffer pool % of target (now)",
            "percent",
            th_none,
            "SELECT TOP (1) mem = buffer_pool_mb * 100.0 / NULLIF(committed_target_memory_mb, 0) FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "Sessions (running)",
            "short",
            th_none,
            "SELECT TOP (1) running = running_sessions FROM collect.session_stats ORDER BY collection_time DESC;",
        ),
        (
            "Blocked tasks (now)",
            "short",
            th_blk,
            "SELECT TOP (1) blocked = total_blocked_task_count FROM collect.cpu_scheduler_stats ORDER BY collection_time DESC;",
        ),
        (
            "Deadlocks (range)",
            "short",
            th_dlk,
            "SELECT deadlocks = ISNULL(SUM(bds.deadlock_count), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Collectors unhealthy",
            "short",
            th_col,
            "SELECT unhealthy = COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR';",
        ),
    ]

    # No upstream ref: no direct upstream sub-tab equivalent. CPU spike and scheduler
    # analysis folded into Resource Overview for practical value.
    dl_cpu_spike = col_datalink(
        "event_time",
        "Drill down: queries +/-20min around this spike",
        "/d/perfmon-queries?from=${__data.fields.window_from_ms}&to=${__data.fields.window_to_ms}"
        "&var-instance=${instance}",
    )
    hide_window_from = {
        "matcher": {"id": "byName", "options": "window_from_ms"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    hide_window_to = {
        "matcher": {"id": "byName", "options": "window_to_ms"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }

    # Upstream ref: GetNocHealthStatusAsync (DatabaseService.NocHealth.cs)
    y = subtab(
        panels,
        "Resource Overview",
        0,
        [
            (4, 4, partial(stat, title, sql=sql, unit=unit, th=th))
            for title, unit, th, sql in stats
        ]
        + [
            (
                24,
                4,
                partial(
                    table,
                    "Server info",
                    sql=f"SELECT si.server_name, si.sql_version, si.edition, si.cpu_count, si.physical_memory_mb, si.environment_type, si.uptime_days, sqlserver_start_time = {tz_col('si.sqlserver_start_time')} FROM config.server_info AS si;",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "CPU utilization",
                    targets=[
                        target(
                            "SELECT time = cus.sample_time, sql_server = cus.sqlserver_cpu_utilization, other_processes = cus.other_process_cpu_utilization, total_cpu = ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) FROM collect.cpu_utilization_stats AS cus WHERE $__timeFilter(cus.sample_time) ORDER BY cus.sample_time;"
                        )
                    ],
                    unit="percent",
                    stacked=True,
                    max_=100,
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Memory utilization",
                    targets=[target("""
SELECT
    time = ms.collection_time,
    buffer_pool = ms.buffer_pool_mb,
    plan_cache = ms.plan_cache_mb,
    other = ms.other_memory_mb,
    available_os_memory = ms.available_physical_memory_mb,
    granted_memory_mb = ISNULL((
        SELECT TOP (1)
            SUM(mgs.granted_memory_mb)
        FROM collect.memory_grant_stats AS mgs
        WHERE mgs.collection_time >= DATEADD(MINUTE, -5, ms.collection_time)
            AND mgs.collection_time <= DATEADD(MINUTE, 5, ms.collection_time)
        GROUP BY mgs.collection_time
        ORDER BY ABS(DATEDIFF(SECOND, mgs.collection_time, ms.collection_time)) ASC
    ), 0)
FROM collect.memory_stats AS ms
WHERE $__timeFilter(ms.collection_time)
ORDER BY ms.collection_time;
""")],
                    unit="decmbytes",
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Scheduler pressure",
                    targets=[
                        target(
                            "SELECT time = css.collection_time, avg_runnable_tasks = css.avg_runnable_tasks_count, blocked_tasks = css.total_blocked_task_count, queued_requests = css.total_queued_request_count FROM collect.cpu_scheduler_stats AS css WHERE $__timeFilter(css.collection_time) ORDER BY css.collection_time;"
                        )
                    ],
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Sessions",
                    targets=[
                        target(
                            "SELECT time = ss.collection_time, total = ss.total_sessions, running = ss.running_sessions, sleeping = ss.sleeping_sessions, waiting_for_memory = ss.sessions_waiting_for_memory FROM collect.session_stats AS ss WHERE $__timeFilter(ss.collection_time) ORDER BY ss.collection_time;"
                        )
                    ],
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Top wait types",
                    targets=[target("""
SELECT
    time = ws.collection_time,
    metric = ws.wait_type,
    value = CONVERT(float, ws.wait_time_ms_delta)
FROM collect.wait_stats AS ws
WHERE $__timeFilter(ws.collection_time)
    AND ws.wait_time_ms_delta > 0
    AND ws.wait_type IN (
        SELECT TOP (8)
            ws2.wait_type
        FROM collect.wait_stats AS ws2
        WHERE $__timeFilter(ws2.collection_time)
        GROUP BY ws2.wait_type
        ORDER BY SUM(ws2.wait_time_ms_delta) DESC
    )
ORDER BY ws.collection_time;
""")],
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    bargauge,
                    "Top waits (last hour)",
                    sql="SELECT tw.wait_type, wait_seconds = CONVERT(float, tw.wait_time_sec) FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Blocking events & deadlocks (1m buckets)",
                    targets=[target(blocking_deadlock_1m_bucket_sql())],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    table,
                    "File IO latency by database",
                    sql="""
SELECT TOP (20)
    database_name = fio.database_name,
    file_type = fio.file_type_desc,
    reads = SUM(fio.num_of_reads_delta),
    avg_read_ms = CONVERT(decimal(19,2), SUM(fio.io_stall_read_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_reads_delta), 0)),
    writes = SUM(fio.num_of_writes_delta),
    avg_write_ms = CONVERT(decimal(19,2), SUM(fio.io_stall_write_ms_delta) * 1.0 / NULLIF(SUM(fio.num_of_writes_delta), 0))
FROM collect.file_io_stats AS fio
WHERE $__timeFilter(fio.collection_time)
GROUP BY fio.database_name, fio.file_type_desc
ORDER BY SUM(fio.io_stall_ms_delta) DESC;
""",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "CPU spike events (last 48h)",
                    sql=f"""
SELECT TOP (50)
    event_time = {tz_col('cs.event_time')},
    cs.sql_server_cpu,
    cs.other_process_cpu,
    cs.total_cpu,
    cs.severity,
    window_from_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, -20, {tz_col('cs.event_time')})),
    window_to_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, 20, {tz_col('cs.event_time')}))
FROM report.cpu_spikes AS cs
WHERE cs.event_time >= DATEADD(HOUR, -48, SYSDATETIME())
    AND cs.sql_server_cpu >= 60
ORDER BY cs.event_time DESC;
""",
                    overrides=[
                        status_colors(
                            "severity",
                            {
                                "CRITICAL": "red",
                                "HIGH": "red",
                                "MEDIUM": "orange",
                                "LOW": "green",
                            },
                        ),
                        dl_cpu_spike,
                        hide_window_from,
                        hide_window_to,
                    ],
                    sort_by=[{"displayName": "event_time", "desc": True}],
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Scheduler CPU analysis",
                    sql=f"""
SELECT
    collection_time = {tz_col('sca.collection_time')},
    sca.scheduler_count,
    sca.cpu_count,
    sca.total_current_workers_count,
    sca.max_workers_count,
    worker_utilization_percent = sca.worker_utilization_percent,
    sca.total_runnable_tasks_count,
    sca.avg_runnable_tasks_count,
    sca.total_blocked_task_count,
    avg_runnable_tasks_1h = sca.avg_runnable_tasks,
    max_runnable_tasks_1h = sca.max_runnable_tasks,
    sca.cpu_pressure_level,
    sca.recommendation
FROM report.scheduler_cpu_analysis AS sca;
""",
                    overrides=[
                        status_colors(
                            "cpu_pressure_level",
                            {
                                "CRITICAL - Worker thread exhaustion detected": "red",
                                "CRITICAL - High runnable task queue": "red",
                                "HIGH - Frequent runnable task warnings": "red",
                                "HIGH - Sustained runnable task queue": "orange",
                                "MEDIUM - Blocked tasks": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
        ],
    )

    # Upstream ref: GetDailySummaryAsync (DatabaseService.Overview.cs)
    y = subtab(
        panels,
        "Daily Summary",
        y,
        [
            (
                24,
                9,
                partial(
                    table,
                    "Daily summary",
                    sql="SELECT ds.sort_order, ds.metric_name, metric_value = CONVERT(nvarchar(500), ds.metric_value) FROM report.daily_summary_v2 AS ds ORDER BY ds.sort_order;",
                ),
            ),
        ],
    )

    # pnl_server_cfg_changes/pnl_db_cfg_changes are built and appended here (not
    # via the flow() call for their own "Configuration Changes" row further down)
    # so their nid()-assigned ids exist before the Critical issues table's
    # problem_area_qs CASE expression below embeds them in a viewPanel=<id> deep
    # link. reflow(..., appended=True) only updates their gridPos once the
    # Configuration Changes row is reached, without re-appending them.
    pnl_server_cfg_changes = table(
        "Server configuration changes",
        0,
        0,
        24,
        9,
        f"""
SELECT TOP (100)
    change_time = {tz_col('scc.change_time')},
    scc.configuration_name,
    scc.old_value_configured,
    scc.new_value_configured,
    scc.old_value_in_use,
    scc.new_value_in_use,
    scc.requires_restart,
    scc.is_dynamic,
    scc.is_advanced,
    scc.description,
    scc.change_description
FROM report.server_configuration_changes AS scc
WHERE $__timeFilter(scc.change_time)
ORDER BY scc.change_time DESC;
""",
        sort_by=[{"displayName": "change_time", "desc": True}],
    )
    pnl_db_cfg_changes = table(
        "Database configuration changes",
        0,
        0,
        12,
        9,
        f"""
SELECT TOP (100)
    change_time = {tz_col('dcc.change_time')},
    dcc.database_name,
    dcc.setting_type,
    dcc.setting_name,
    dcc.old_value,
    dcc.new_value,
    dcc.change_description
FROM report.database_configuration_changes AS dcc
WHERE $__timeFilter(dcc.change_time)
ORDER BY dcc.change_time DESC;
""",
        sort_by=[{"displayName": "change_time", "desc": True}],
    )
    panels.append(pnl_server_cfg_changes)
    panels.append(pnl_db_cfg_changes)

    dl_log_date = col_datalink(
        "log_date",
        "Investigate in dashboard",
        "${__data.fields.problem_area_url}?${__data.fields.problem_area_qs}"
        "from=${__data.fields.window_from_ms}&to=${__data.fields.window_to_ms}"
        "&var-instance=${instance}",
    )
    hide_problem_area_url = {
        "matcher": {"id": "byName", "options": "problem_area_url"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    hide_problem_area_qs = {
        "matcher": {"id": "byName", "options": "problem_area_qs"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    hide_problem_area_window_from = {
        "matcher": {"id": "byName", "options": "window_from_ms"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    hide_problem_area_window_to = {
        "matcher": {"id": "byName", "options": "window_to_ms"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    # Upstream ref: GetRecommendationsAsync (RecommendationsReader.cs)
    y = subtab(
        panels,
        "Recommendations",
        y,
        [
            (
                24,
                9,
                partial(
                    table,
                    "Critical issues",
                    sql=f"""
SELECT TOP (100)
    ci.issue_id,
    log_date = {tz_col('ci.log_date')},
    ci.severity,
    ci.problem_area,
    ci.source_collector,
    ci.affected_database,
    ci.message,
    ci.investigate_query,
    ci.threshold_value,
    ci.threshold_limit,
    problem_area_url =
        CASE ci.problem_area
            WHEN N'Blocking' THEN N'/d/perfmon-blocking'
            WHEN N'Deadlocking' THEN N'/d/perfmon-blocking'
            WHEN N'Blocking and Deadlocking' THEN N'/d/perfmon-blocking'
            WHEN N'Query Store Configuration' THEN N'/d/perfmon-queries'
            WHEN N'Memory Clerk Growth' THEN N'/d/perfmon-memory'
            WHEN N'SQL Server Stability' THEN N'/d/perfmon-system-events'
            WHEN N'Database Configuration' THEN N'/d/perfmon-instance'
            WHEN N'Server Configuration' THEN N'/d/perfmon-instance'
            ELSE N'/d/perfmon-instance'
        END,
    problem_area_qs =
        CASE ci.problem_area
            WHEN N'Database Configuration' THEN N'viewPanel={pnl_db_cfg_changes["id"]}&'
            WHEN N'Server Configuration' THEN N'viewPanel={pnl_server_cfg_changes["id"]}&'
            ELSE N''
        END,
    window_from_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, -20, {tz_col('ci.log_date')})),
    window_to_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, 20, {tz_col('ci.log_date')}))
FROM config.critical_issues AS ci
WHERE ci.log_date >= DATEADD(HOUR, -24, SYSDATETIME())
ORDER BY ci.log_date DESC;
""",
                    overrides=[
                        status_colors(
                            "severity",
                            {"CRITICAL": "red", "WARNING": "orange", "INFO": "blue"},
                        ),
                        dl_log_date,
                        hide_problem_area_url,
                        hide_problem_area_qs,
                        hide_problem_area_window_from,
                        hide_problem_area_window_to,
                    ],
                    sort_by=[{"displayName": "log_date", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream ref: GetDefaultTraceEventsAsync (DatabaseService.SystemEvents.cs)
    y = subtab(
        panels,
        "Default Trace",
        y,
        [
            (
                24,
                9,
                partial(
                    table,
                    "Default trace events",
                    sql=f"""
SELECT TOP (100)
    dte.event_id,
    collection_time = {tz_col('dte.collection_time')},
    event_time = {tz_col('dte.event_time')},
    dte.event_class,
    dte.event_name,
    dte.spid,
    dte.database_name,
    dte.database_id,
    dte.login_name,
    dte.session_login_name,
    dte.host_name,
    dte.application_name,
    dte.server_name,
    dte.object_name,
    dte.filename,
    dte.integer_data,
    dte.integer_data_2,
    dte.text_data,
    dte.error_number,
    dte.severity,
    dte.state,
    dte.event_sequence,
    dte.is_system,
    dte.request_id,
    dte.duration_us,
    end_time = {tz_col('dte.end_time')}
FROM collect.default_trace_events AS dte
WHERE dte.collection_time >= DATEADD(MINUTE, -10, {tz_from()})
    AND dte.collection_time <= DATEADD(MINUTE, 10, {tz_to()})
    AND $__timeFilter(dte.event_time)
ORDER BY dte.event_time DESC;
""",
                    sort_by=[{"displayName": "event_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream ref: GetCurrentServerConfigAsync / GetCurrentDatabaseConfigAsync /
    # GetCurrentTraceFlagsAsync (DatabaseService.SystemEvents.cs)
    y = subtab(
        panels,
        "Current Configuration",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Server configuration (latest values)",
                    sql=f"""
SELECT
    x.configuration_name,
    x.value_configured,
    x.value_in_use,
    x.value_minimum,
    x.value_maximum,
    x.is_dynamic,
    x.is_advanced,
    x.description,
    collection_time = {tz_col('x.collection_time')}
FROM (
    SELECT
        sch.*,
        rn = ROW_NUMBER() OVER (PARTITION BY sch.configuration_id ORDER BY sch.collection_time DESC)
    FROM config.server_configuration_history AS sch
) AS x
WHERE x.rn = 1
ORDER BY x.configuration_name;
""",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Database configuration (latest values)",
                    sql=f"""
SELECT
    x.database_name,
    x.setting_type,
    x.setting_name,
    x.setting_value,
    collection_time = {tz_col('x.collection_time')}
FROM (
    SELECT
        dch.*,
        rn = ROW_NUMBER() OVER (PARTITION BY dch.database_name, dch.setting_type, dch.setting_name ORDER BY dch.collection_time DESC)
    FROM config.database_configuration_history AS dch
) AS x
WHERE x.rn = 1
ORDER BY x.database_name, x.setting_type, x.setting_name;
""",
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Trace flags (latest values)",
                    sql=f"""
SELECT
    x.trace_flag,
    x.status,
    x.is_global,
    x.is_session,
    collection_time = {tz_col('x.collection_time')}
FROM (
    SELECT
        tfh.*,
        rn = ROW_NUMBER() OVER (PARTITION BY tfh.trace_flag ORDER BY tfh.collection_time DESC)
    FROM config.trace_flags_history AS tfh
) AS x
WHERE x.rn = 1
ORDER BY x.trace_flag;
""",
                ),
            ),
        ],
    )

    # Upstream ref: GetServerConfigChangesAsync / GetDatabaseConfigChangesAsync /
    # GetTraceFlagChangesAsync (DatabaseService.SystemEvents.cs)
    y = subtab(
        panels,
        "Configuration Changes",
        y,
        [
            (24, 9, reflow(pnl_server_cfg_changes, appended=True)),
            (12, 9, reflow(pnl_db_cfg_changes, appended=True)),
            (
                12,
                9,
                partial(
                    table,
                    "Trace flag changes",
                    sql=f"""
SELECT TOP (100)
    change_time = {tz_col('tfc.change_time')},
    tfc.trace_flag,
    tfc.previous_status,
    tfc.new_status,
    tfc.is_global,
    tfc.is_session,
    tfc.scope,
    tfc.change_description
FROM report.trace_flag_changes AS tfc
WHERE $__timeFilter(tfc.change_time)
ORDER BY tfc.change_time DESC;
""",
                    sort_by=[{"displayName": "change_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream ref: GetCollectionHealthAsync / GetCollectionDurationLogsAsync (DatabaseService.cs)
    y = subtab(
        panels,
        "Collection Health",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Collection health",
                    sql=f"""
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
""",
                    overrides=[status_colors("health_status", HEALTH_STATUS_COLORS)],
                ),
            ),
            (
                24,
                9,
                partial(
                    timeseries,
                    "Duration Trends",
                    targets=[
                        target(
                            "SELECT time = cl.collection_time, metric = cl.collector_name, value = CONVERT(float, cl.duration_ms) FROM config.collection_log AS cl WHERE cl.collection_status = N'SUCCESS' AND cl.duration_ms IS NOT NULL AND $__timeFilter(cl.collection_time) ORDER BY cl.collection_time;"
                        )
                    ],
                    unit="ms",
                ),
            ),
        ],
    )

    # Upstream ref: GetRunningJobsAsync (DatabaseService.Overview.cs)
    subtab(
        panels,
        "Running Jobs",
        y,
        [
            (
                24,
                9,
                partial(
                    table,
                    "Running SQL Agent jobs",
                    sql=f"""
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
""",
                    overrides=[
                        status_colors("duration_status", DURATION_STATUS_COLORS)
                    ],
                ),
            ),
        ],
    )

    return dashboard("perfmon-instance", "PerfMon · Overview", panels, [instance_var()])
