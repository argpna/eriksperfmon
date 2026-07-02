from ._shared import *


def instance_overview():
    reset_id()
    panels = []
    th_cpu = thresholds(("green", None), ("yellow", 70), ("red", 90))
    th_blk = thresholds(("green", None), ("yellow", 1), ("red", 10))
    th_dlk = thresholds(("green", None), ("yellow", 1), ("red", 5))
    th_col = thresholds(("green", None), ("yellow", 1), ("red", 3))
    th_none = thresholds(("blue", None))

    # Upstream tab: Overview, sub-tab: Resource Overview
    panels.append(row("Resource Overview", 0))
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
            "SELECT deadlocks = ISNULL(SUM(bds.deadlock_count_delta), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Collectors unhealthy",
            "short",
            th_col,
            "SELECT unhealthy = COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR';",
        ),
    ]
    for i, (title, unit, th, sql) in enumerate(stats):
        panels.append(stat(title, i * 4, 1, 4, 4, sql, unit, th))

    panels.append(
        table(
            "Server info",
            0,
            5,
            24,
            4,
            "SELECT si.server_name, si.sql_version, si.edition, si.cpu_count, si.physical_memory_mb, si.environment_type, si.uptime_days, si.sqlserver_start_time FROM config.server_info AS si;",
        )
    )

    panels.append(
        timeseries(
            "CPU utilization",
            0,
            9,
            12,
            9,
            [
                target(
                    "SELECT time = cus.sample_time, sql_server = cus.sqlserver_cpu_utilization, other_processes = cus.other_process_cpu_utilization, total_cpu = ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) FROM collect.cpu_utilization_stats AS cus WHERE $__timeFilter(cus.sample_time) ORDER BY cus.sample_time;"
                )
            ],
            unit="percent",
            stacked=True,
            max_=100,
        )
    )
    panels.append(
        timeseries(
            "Memory utilization",
            12,
            9,
            12,
            9,
            [target("""
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
        )
    )
    panels.append(
        timeseries(
            "Scheduler pressure",
            0,
            18,
            12,
            8,
            [
                target(
                    "SELECT time = css.collection_time, avg_runnable_tasks = css.avg_runnable_tasks_count, blocked_tasks = css.total_blocked_task_count, queued_requests = css.total_queued_request_count FROM collect.cpu_scheduler_stats AS css WHERE $__timeFilter(css.collection_time) ORDER BY css.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Sessions",
            12,
            18,
            12,
            8,
            [
                target(
                    "SELECT time = ss.collection_time, total = ss.total_sessions, running = ss.running_sessions, sleeping = ss.sleeping_sessions, waiting_for_memory = ss.sessions_waiting_for_memory FROM collect.session_stats AS ss WHERE $__timeFilter(ss.collection_time) ORDER BY ss.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Top wait types",
            0,
            26,
            12,
            9,
            [target("""
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
        )
    )
    panels.append(
        bargauge(
            "Top waits (last hour)",
            12,
            26,
            12,
            9,
            "SELECT tw.wait_type, wait_seconds = CONVERT(float, tw.wait_time_sec) FROM report.top_waits_last_hour AS tw ORDER BY tw.wait_time_ms DESC;",
        )
    )
    panels.append(
        timeseries(
            "Blocking events & deadlocks",
            0,
            35,
            12,
            8,
            [
                target(
                    "SELECT time = bds.collection_time, blocking_events = SUM(bds.blocking_event_count_delta), deadlocks = SUM(bds.deadlock_count_delta) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time) GROUP BY bds.collection_time ORDER BY bds.collection_time;"
                )
            ],
            bars=True,
        )
    )
    panels.append(
        table(
            "File IO latency by database",
            12,
            35,
            12,
            8,
            """
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
        )
    )
    # No direct upstream sub-tab equivalent. CPU spike and scheduler analysis folded into Resource Overview for practical value
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
    panels.append(
        table(
            "CPU spike events (last 48h)",
            0,
            43,
            12,
            9,
            """
SELECT TOP (50)
    cs.event_time,
    cs.sql_server_cpu,
    cs.other_process_cpu,
    cs.total_cpu,
    cs.severity,
    window_from_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, -20, cs.event_time)),
    window_to_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, 20, cs.event_time))
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
        )
    )
    panels.append(
        table(
            "Scheduler CPU analysis",
            12,
            43,
            12,
            9,
            """
SELECT
    sca.collection_time,
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
        )
    )

    # Upstream tab: Overview, sub-tab: Daily Summary
    panels.append(row("Daily Summary", 52))
    panels.append(
        table(
            "Daily summary",
            0,
            53,
            24,
            9,
            "SELECT ds.sort_order, ds.metric_name, metric_value = CONVERT(nvarchar(500), ds.metric_value) FROM report.daily_summary_v2 AS ds ORDER BY ds.sort_order;",
        )
    )

    pnl_server_cfg_changes = table(
        "Server configuration changes",
        0,
        103,
        24,
        9,
        """
SELECT TOP (100)
    scc.change_time,
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
        112,
        12,
        9,
        """
SELECT TOP (100)
    dcc.change_time,
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

    # Upstream tab: Overview, sub-tab: Recommendations
    panels.append(row("Recommendations", 62))
    dl_problem_area = col_datalink(
        "problem_area",
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
    panels.append(
        table(
            "Critical issues",
            0,
            63,
            24,
            9,
            f"""
SELECT TOP (100)
    ci.issue_id,
    ci.log_date,
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
    window_from_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, -20, ci.log_date)),
    window_to_ms = DATEDIFF_BIG(MILLISECOND, '19700101', DATEADD(MINUTE, 20, ci.log_date))
FROM config.critical_issues AS ci
WHERE ci.log_date >= DATEADD(HOUR, -24, SYSDATETIME())
ORDER BY ci.log_date DESC;
""",
            overrides=[
                status_colors(
                    "severity", {"CRITICAL": "red", "WARNING": "orange", "INFO": "blue"}
                ),
                dl_problem_area,
                hide_problem_area_url,
                hide_problem_area_qs,
                hide_problem_area_window_from,
                hide_problem_area_window_to,
            ],
            sort_by=[{"displayName": "log_date", "desc": True}],
        )
    )

    # Upstream tab: Overview, sub-tab: Default Trace
    panels.append(row("Default Trace", 72))
    panels.append(
        table(
            "Default trace events",
            0,
            73,
            24,
            9,
            """
SELECT TOP (100)
    dte.event_id,
    dte.collection_time,
    dte.event_time,
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
    dte.end_time
FROM collect.default_trace_events AS dte
WHERE dte.collection_time >= DATEADD(MINUTE, -10, CONVERT(datetime2, $__timeFrom()))
    AND dte.collection_time <= DATEADD(MINUTE, 10, CONVERT(datetime2, $__timeTo()))
    AND $__timeFilter(dte.event_time)
ORDER BY dte.event_time DESC;
""",
            sort_by=[{"displayName": "event_time", "desc": True}],
        )
    )

    # Upstream tab: Overview, sub-tab: Current Configuration
    panels.append(row("Current Configuration", 82))
    panels.append(
        table(
            "Server configuration (latest values)",
            0,
            83,
            24,
            10,
            """
SELECT
    x.configuration_name,
    x.value_configured,
    x.value_in_use,
    x.value_minimum,
    x.value_maximum,
    x.is_dynamic,
    x.is_advanced,
    x.description,
    x.collection_time
FROM (
    SELECT
        sch.*,
        rn = ROW_NUMBER() OVER (PARTITION BY sch.configuration_id ORDER BY sch.collection_time DESC)
    FROM config.server_configuration_history AS sch
) AS x
WHERE x.rn = 1
ORDER BY x.configuration_name;
""",
        )
    )
    panels.append(
        table(
            "Database configuration (latest values)",
            0,
            93,
            12,
            9,
            """
SELECT
    x.database_name,
    x.setting_type,
    x.setting_name,
    x.setting_value,
    x.collection_time
FROM (
    SELECT
        dch.*,
        rn = ROW_NUMBER() OVER (PARTITION BY dch.database_name, dch.setting_type, dch.setting_name ORDER BY dch.collection_time DESC)
    FROM config.database_configuration_history AS dch
) AS x
WHERE x.rn = 1
ORDER BY x.database_name, x.setting_type, x.setting_name;
""",
        )
    )
    panels.append(
        table(
            "Trace flags (latest values)",
            12,
            93,
            12,
            9,
            """
SELECT
    x.trace_flag,
    x.status,
    x.is_global,
    x.is_session,
    x.collection_time
FROM (
    SELECT
        tfh.*,
        rn = ROW_NUMBER() OVER (PARTITION BY tfh.trace_flag ORDER BY tfh.collection_time DESC)
    FROM config.trace_flags_history AS tfh
) AS x
WHERE x.rn = 1
ORDER BY x.trace_flag;
""",
        )
    )

    # Upstream tab: Overview, sub-tab: Configuration Changes
    panels.append(row("Configuration Changes", 102))
    panels.append(
        table(
            "Trace flag changes",
            12,
            112,
            12,
            9,
            """
SELECT TOP (100)
    tfc.change_time,
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
        )
    )

    # Upstream tab: Overview, sub-tab: Collection Health
    panels.append(row("Collection Health", 121))
    panels.append(
        table(
            "Collection health",
            0,
            122,
            24,
            10,
            """
SELECT
    ch.collector_name,
    ch.health_status,
    ch.last_success_time,
    ch.hours_since_success,
    ch.failure_rate_percent,
    ch.avg_duration_ms
FROM report.collection_health AS ch
ORDER BY
    CASE ch.health_status
        WHEN N'FAILING' THEN 0
        WHEN N'STALE' THEN 1
        WHEN N'NEVER_RUN' THEN 2
        ELSE 3
    END,
    ch.collector_name;
""",
            overrides=[
                status_colors(
                    "health_status",
                    {
                        "FAILING": "red",
                        "STALE": "orange",
                        "NEVER_RUN": "yellow",
                        "OK": "green",
                    },
                )
            ],
        )
    )

    panels.append(
        timeseries(
            "Duration Trends",
            0,
            132,
            24,
            9,
            [
                target(
                    "SELECT time = cl.collection_time, metric = cl.collector_name, value = CONVERT(float, cl.duration_ms) FROM config.collection_log AS cl WHERE cl.collection_status = N'SUCCESS' AND cl.duration_ms IS NOT NULL AND $__timeFilter(cl.collection_time) ORDER BY cl.collection_time;"
                )
            ],
            unit="ms",
        )
    )

    # Upstream tab: Overview, sub-tab: Running Jobs
    panels.append(row("Running Jobs", 141))
    panels.append(
        table(
            "Running SQL Agent jobs",
            0,
            142,
            24,
            9,
            """
SELECT
    rj.job_name,
    rj.job_id,
    rj.job_enabled,
    rj.start_time,
    current_duration = rj.current_duration_formatted,
    rj.current_duration_seconds,
    avg_duration = rj.avg_duration_formatted,
    rj.avg_duration_seconds,
    p95_duration = rj.p95_duration_formatted,
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

    return dashboard("perfmon-instance", "PerfMon · Overview", panels, [instance_var()])
