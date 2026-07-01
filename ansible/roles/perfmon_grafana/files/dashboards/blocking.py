from ._shared import *


# Upstream tab: Locking
# 4 sub-tabs: Blocking/Deadlock Trends, Current Waits, Blocking, Deadlocks
def blocking():
    reset_id()
    th_blk = thresholds(("green", None), ("yellow", 1), ("red", 10))
    th_dlk = thresholds(("green", None), ("yellow", 1), ("red", 5))
    panels = []

    # Upstream sub-tab: Blocking/Deadlock Trends
    panels.append(row("Blocking/Deadlock Trends", 0))
    stats = [
        (
            "Blocking events (range)",
            "short",
            th_blk,
            "SELECT v = ISNULL(SUM(bds.blocking_event_count_delta), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Deadlocks (range)",
            "short",
            th_dlk,
            "SELECT v = ISNULL(SUM(bds.deadlock_count_delta), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Deadlock victims (range)",
            "short",
            th_dlk,
            "SELECT v = ISNULL(SUM(bds.victim_count_delta), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Max blocking duration (range)",
            "s",
            thresholds(("green", None), ("yellow", 30), ("red", 120)),
            "SELECT v = ISNULL(MAX(bds.max_blocking_duration_ms), 0) / 1000.0 FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
    ]
    for i, (title, unit, th, sql) in enumerate(stats):
        panels.append(stat(title, i * 6, 1, 6, 4, sql, unit, th))
    panels.append(
        timeseries(
            "Blocking events & deadlocks",
            0,
            5,
            12,
            9,
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
            "Blocking & deadlocks by database",
            12,
            5,
            12,
            9,
            """
SELECT
    database_name = bds.database_name,
    blocking_events = SUM(bds.blocking_event_count_delta),
    total_blocked_seconds = SUM(bds.total_blocking_duration_ms_delta) / 1000,
    deadlocks = SUM(bds.deadlock_count_delta),
    victims = SUM(bds.victim_count_delta)
FROM collect.blocking_deadlock_stats AS bds
WHERE $__timeFilter(bds.collection_time)
GROUP BY bds.database_name
HAVING SUM(bds.blocking_event_count_delta) > 0 OR SUM(bds.deadlock_count_delta) > 0
ORDER BY SUM(bds.blocking_event_count_delta) DESC;
""",
        )
    )

    # Upstream sub-tab: Current Waits
    panels.append(row("Current Waits", 14))
    panels.append(
        timeseries(
            "Wait duration by wait type",
            0,
            15,
            12,
            9,
            [
                target(
                    "SELECT time = wt.collection_time, metric = wt.wait_type, value = SUM(CONVERT(float, wt.wait_duration_ms)) FROM collect.waiting_tasks AS wt WHERE $__timeFilter(wt.collection_time) AND wt.wait_type NOT IN (N'SLEEP', N'WAITFOR', N'BROKER_TO_FLUSH') GROUP BY wt.collection_time, wt.wait_type ORDER BY wt.collection_time;"
                )
            ],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Blocked sessions by database",
            12,
            15,
            12,
            9,
            [
                target(
                    "SELECT time = wt.collection_time, metric = wt.database_name, value = COUNT_BIG(*) FROM collect.waiting_tasks AS wt WHERE $__timeFilter(wt.collection_time) AND wt.blocking_session_id <> 0 GROUP BY wt.collection_time, wt.database_name ORDER BY wt.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "Waiting tasks",
            0,
            24,
            24,
            9,
            """
SELECT TOP (200)
    wt.collection_time,
    wt.session_id,
    wt.blocking_session_id,
    wt.wait_type,
    wait_seconds = wt.wait_duration_ms / 1000.0,
    wt.resource_description,
    wt.database_name,
    wt.command,
    wt.cpu_time_ms,
    wt.logical_reads,
    query_text = CONVERT(nvarchar(300), LEFT(wt.query_text, 300))
FROM collect.waiting_tasks AS wt
WHERE $__timeFilter(wt.collection_time)
ORDER BY wt.collection_time DESC, wt.wait_duration_ms DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Blocking
    panels.append(row("Blocking", 33))
    panels.append(
        table(
            "Blocked process reports",
            0,
            34,
            24,
            11,
            """
SELECT TOP (100)
    bpr.event_time,
    bpr.collection_time,
    bpr.database_name,
    bpr.currentdbname,
    bpr.contentious_object,
    bpr.activity,
    bpr.blocking_tree,
    blocked_spid = bpr.spid,
    bpr.ecid,
    wait_seconds = bpr.wait_time_ms / 1000.0,
    bpr.status,
    bpr.isolation_level,
    bpr.lock_mode,
    bpr.resource_owner_type,
    bpr.wait_resource,
    bpr.transaction_count,
    bpr.transaction_name,
    bpr.last_transaction_started,
    bpr.last_transaction_completed,
    bpr.priority,
    bpr.log_used,
    bpr.client_app,
    bpr.host_name,
    bpr.login_name,
    bpr.transaction_id,
    bpr.client_option_1,
    bpr.client_option_2,
    query_text = CONVERT(nvarchar(500), LEFT(CONVERT(nvarchar(max), bpr.query_text), 500)),
    blocked_process_report_xml = CONVERT(nvarchar(max), bpr.blocked_process_report_xml)
FROM collect.blocking_BlockedProcessReport AS bpr
WHERE $__timeFilter(bpr.collection_time)
ORDER BY bpr.event_time DESC;
""",
            sort_by=[{"displayName": "event_time", "desc": True}],
        )
    )
    panels.append(
        table(
            "Blocking chains (waiting tasks with head blockers)",
            0,
            43,
            24,
            9,
            """
SELECT TOP (100)
    wt.collection_time,
    wt.session_id,
    wt.blocking_session_id,
    wt.wait_type,
    wait_seconds = wt.wait_duration_ms / 1000.0,
    wt.database_name,
    wt.command,
    wt.request_status,
    wt.cpu_time_ms,
    total_elapsed_sec = wt.total_elapsed_time_ms / 1000.0,
    wt.logical_reads,
    wt.writes,
    wt.row_count,
    wt.resource_description,
    query_text = CONVERT(nvarchar(300), LEFT(wt.query_text, 300))
FROM collect.waiting_tasks AS wt
WHERE $__timeFilter(wt.collection_time)
    AND wt.blocking_session_id <> 0
ORDER BY wt.collection_time DESC, wt.wait_duration_ms DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )
    panels.append(
        table(
            "Blocking hierarchy",
            0,
            52,
            24,
            10,
            """
SELECT TOP (100)
    bca.collection_time,
    bca.session_id,
    bca.blocking_session_id,
    bca.blocking_chain_position,
    sessions_blocked = bca.sessions_blocked,
    bca.wait_type,
    wait_sec = CONVERT(decimal(19,2), bca.wait_duration_sec),
    bca.database_name,
    bca.command,
    bca.cpu_time_ms,
    bca.logical_reads,
    bca.severity,
    bca.recommendation,
    query_text = LEFT(bca.query_text, 300)
FROM report.blocking_chain_analysis AS bca
WHERE $__timeFilter(bca.collection_time)
ORDER BY bca.collection_time DESC, bca.sessions_blocked DESC;
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
                status_colors(
                    "blocking_chain_position",
                    {
                        "HEAD BLOCKER": "red",
                        "INTERMEDIATE": "orange",
                        "BLOCKED": "blue",
                    },
                ),
            ],
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Deadlocks
    panels.append(row("Deadlocks", 64))
    panels.append(
        table(
            "Deadlock participants",
            0,
            65,
            24,
            11,
            """
SELECT TOP (100)
    d.event_date,
    d.collection_time,
    d.database_name,
    d.ServerName,
    d.deadlock_type,
    d.spid,
    d.deadlock_group,
    d.status,
    d.isolation_level,
    d.owner_mode,
    d.waiter_mode,
    d.lock_mode,
    d.transaction_count,
    wait_ms = d.wait_time,
    d.wait_resource,
    d.priority,
    d.log_used,
    d.login_name,
    d.host_name,
    d.client_app,
    d.last_tran_started,
    d.last_batch_started,
    d.last_batch_completed,
    d.transaction_name,
    d.owner_waiter_type,
    d.owner_activity,
    d.owner_waiter_activity,
    d.owner_merging,
    d.owner_spilling,
    d.owner_waiting_to_close,
    d.waiter_waiter_type,
    d.waiter_owner_activity,
    d.waiter_waiter_activity,
    d.waiter_merging,
    d.waiter_spilling,
    d.waiter_waiting_to_close,
    d.client_option_1,
    d.client_option_2,
    query = CONVERT(nvarchar(500), LEFT(CONVERT(nvarchar(max), d.query), 500)),
    object_names = CONVERT(nvarchar(500), LEFT(CONVERT(nvarchar(max), d.object_names), 500))
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
ORDER BY d.event_date DESC;
""",
            sort_by=[{"displayName": "event_date", "desc": True}],
        )
    )
    # deadlock_group values contain '#' which browsers treat as a URL fragment delimiter,
    # truncating the query string. Use deadlock_id as the link key instead.
    dl_deadlock_id = col_datalink(
        "deadlock_group",
        "View deadlock detail",
        "/d/perfmon-deadlock-detail?${__url_time_range}&var-instance=${instance}"
        "&var-deadlock_id=${__data.fields.deadlock_id}",
    )
    hide_deadlock_id = {
        "matcher": {"id": "byName", "options": "deadlock_id"},
        "properties": [{"id": "custom.hidden", "value": True}],
    }
    panels.append(
        table(
            "Deadlock events",
            0,
            76,
            24,
            8,
            """
SELECT TOP (50)
    d.event_date,
    d.database_name,
    d.deadlock_group,
    d.deadlock_id
FROM (
    SELECT
        event_date,
        database_name,
        deadlock_group,
        deadlock_id,
        rn = ROW_NUMBER() OVER (PARTITION BY deadlock_group ORDER BY deadlock_id)
    FROM collect.deadlocks
    WHERE $__timeFilter(collection_time)
) AS d
WHERE d.rn = 1
ORDER BY d.event_date DESC;
""",
            overrides=[dl_deadlock_id, hide_deadlock_id],
            sort_by=[{"displayName": "event_date", "desc": True}],
            description="One row per unique deadlock event. Click a deadlock_group value to open the full participant detail and XDL graph.",
        )
    )

    return dashboard("perfmon-blocking", "PerfMon · Locking", panels, [instance_var()])
