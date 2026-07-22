from functools import partial

from ._shared import *


# Upstream tab: Locking
# 4 sub-tabs: Blocking/Deadlock Trends, Current Waits, Blocking, Deadlocks
def blocking():
    th_blk = thresholds(("green", None), ("yellow", 1), ("red", 10))
    th_dlk = thresholds(("green", None), ("yellow", 1), ("red", 5))
    panels = []

    # Upstream sub-tab: Blocking/Deadlock Trends
    # No upstream ref: range-total stat panels, Grafana-native.
    # collect.blocking_deadlock_stats.blocking_event_count/total_blocking_duration_ms are
    # re-aggregated every collection cycle over a trailing 1-hour rolling window, so the
    # same events are counted in up to ~60 consecutive rows - SUM() or MAX() over that
    # table can't give a true range total. Query the raw collect.blocking_BlockedProcessReport
    # event rows directly instead, so the range genuinely follows the selected time range
    # rather than being fixed to a trailing hour. deadlock_count/victim_count are already
    # bounded to only-new-since-last-collection and don't have the overlap problem, so SUM()
    # directly from blocking_deadlock_stats is correct for those.
    stats = [
        (
            "Blocking events (range)",
            "short",
            th_blk,
            f"SELECT v = ISNULL(COUNT_BIG(*), 0) FROM collect.blocking_BlockedProcessReport AS bg WHERE {tz_prefilter('bg.collection_time')} AND $__timeFilter(bg.event_time);",
        ),
        (
            "Deadlocks (range)",
            "short",
            th_dlk,
            "SELECT v = ISNULL(SUM(bds.deadlock_count), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Deadlock victims (range)",
            "short",
            th_dlk,
            "SELECT v = ISNULL(SUM(bds.victim_count), 0) FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
        (
            "Max blocking duration (range)",
            "s",
            thresholds(("green", None), ("yellow", 30), ("red", 120)),
            "SELECT v = ISNULL(MAX(bds.max_blocking_duration_ms), 0) / 1000.0 FROM collect.blocking_deadlock_stats AS bds WHERE $__timeFilter(bds.collection_time);",
        ),
    ]
    # Upstream ref: GetLockWaitStatsAsync (DatabaseService.QueryPerformance.Blocking.cs)
    lock_wait_sql = """
WITH lock_rates AS (
    SELECT
        ws.collection_time,
        ws.wait_type,
        ws.wait_time_ms_delta,
        interval_seconds = DATEDIFF(SECOND,
            LAG(ws.collection_time) OVER (PARTITION BY ws.wait_type ORDER BY ws.collection_time),
            ws.collection_time)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
        AND ws.wait_type LIKE N'LCK%'
)
SELECT
    time = collection_time,
    metric = wait_type,
    value = CASE
        WHEN interval_seconds > 0
        THEN CONVERT(decimal(18,4), CAST(wait_time_ms_delta AS decimal(19,4)) / interval_seconds)
        ELSE 0
    END
FROM lock_rates
WHERE wait_time_ms_delta >= 0
ORDER BY collection_time;
"""
    # No upstream ref: companion to blocking_deadlock_1m_bucket_sql (_shared.py) -
    # same raw-event-table bucketing approach, duration/wait-time instead of counts.
    blocking_duration_deadlock_wait_sql = f"""
WITH distinct_deadlocks AS (
    SELECT
        event_date,
        wait_ms = MAX(wait_time)
    FROM collect.deadlocks
    WHERE $__timeFilter(collection_time)
    GROUP BY event_date
),
blocking_buckets AS (
    SELECT
        time = $__timeGroup(bg.event_time, '1m'),
        blocking_duration_ms = SUM(bg.wait_time_ms)
    FROM collect.blocking_BlockedProcessReport AS bg
    WHERE {tz_prefilter('bg.collection_time')} AND $__timeFilter(bg.event_time)
    GROUP BY $__timeGroup(bg.event_time, '1m')
),
deadlock_buckets AS (
    SELECT
        time = $__timeGroup(dd.event_date, '1m'),
        deadlock_wait_ms = SUM(dd.wait_ms)
    FROM distinct_deadlocks AS dd
    GROUP BY $__timeGroup(dd.event_date, '1m')
)
SELECT
    time = COALESCE(bb.time, db.time),
    blocking_duration_ms = ISNULL(bb.blocking_duration_ms, 0),
    deadlock_wait_ms = ISNULL(db.deadlock_wait_ms, 0)
FROM blocking_buckets AS bb
FULL OUTER JOIN deadlock_buckets AS db
    ON bb.time = db.time
ORDER BY time;
"""
    # No upstream ref: Grafana-native per-database rollup of the same raw event tables.
    blocking_by_db_sql = f"""
WITH blocking_by_db AS (
    SELECT
        database_name = ISNULL(bg.database_name, N'UNKNOWN'),
        blocking_events = COUNT_BIG(*),
        blocked_seconds = SUM(bg.wait_time_ms) / 1000
    FROM collect.blocking_BlockedProcessReport AS bg
    WHERE {tz_prefilter('bg.collection_time')} AND $__timeFilter(bg.event_time)
    GROUP BY ISNULL(bg.database_name, N'UNKNOWN')
),
deadlock_by_db AS (
    SELECT
        database_name = bds.database_name,
        deadlocks = SUM(bds.deadlock_count),
        victims = SUM(bds.victim_count)
    FROM collect.blocking_deadlock_stats AS bds
    WHERE $__timeFilter(bds.collection_time)
    GROUP BY bds.database_name
)
SELECT
    database_name = COALESCE(b.database_name, d.database_name),
    blocking_events = ISNULL(b.blocking_events, 0),
    blocked_seconds = ISNULL(b.blocked_seconds, 0),
    deadlocks = ISNULL(d.deadlocks, 0),
    victims = ISNULL(d.victims, 0)
FROM blocking_by_db AS b
FULL OUTER JOIN deadlock_by_db AS d
    ON b.database_name = d.database_name
WHERE ISNULL(b.blocking_events, 0) > 0 OR ISNULL(d.deadlocks, 0) > 0
ORDER BY ISNULL(b.blocking_events, 0) DESC;
"""

    # Upstream ref: GetBlockingDeadlockStatsAsync / GetLockWaitStatsAsync (DatabaseService.QueryPerformance.Blocking.cs)
    y = subtab(
        panels,
        "Blocking/Deadlock Trends",
        0,
        [
            (6, 4, partial(stat, title, sql=sql, unit=unit, th=th))
            for title, unit, th, sql in stats
        ]
        + [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Lock Wait Stats (LCK%)",
                    targets=[target(lock_wait_sql)],
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Blocking events & deadlocks (1m buckets)",
                    targets=[target(blocking_deadlock_1m_bucket_sql())],
                    bars=True,
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Blocking duration & deadlock wait time (1m buckets)",
                    targets=[target(blocking_duration_deadlock_wait_sql)],
                    unit="ms",
                    bars=True,
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Blocking & deadlocks by database",
                    sql=blocking_by_db_sql,
                ),
            ),
        ],
    )

    # Upstream sub-tab: Current Waits
    # Upstream ref: GetWaitingTaskTrendAsync source table (collect.waiting_tasks,
    # DatabaseService.QueryPerformance.Blocking.cs) - raw grid rather than the
    # trend's aggregated shape.
    waiting_tasks_sql = f"""
SELECT TOP (200)
    collection_time = {tz_col('wt.collection_time')},
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
"""
    # Upstream ref: GetWaitingTaskTrendAsync / GetBlockedSessionTrendAsync (DatabaseService.QueryPerformance.Blocking.cs)
    y = subtab(
        panels,
        "Current Waits",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Wait duration by wait type",
                    targets=[
                        target(
                            "SELECT time = wt.collection_time, metric = wt.wait_type, value = SUM(CONVERT(float, wt.wait_duration_ms)) FROM collect.waiting_tasks AS wt WHERE $__timeFilter(wt.collection_time) AND wt.wait_type NOT IN (N'SLEEP', N'WAITFOR', N'BROKER_TO_FLUSH') GROUP BY wt.collection_time, wt.wait_type ORDER BY wt.collection_time;"
                        )
                    ],
                    unit="ms",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Blocked sessions by database",
                    targets=[
                        target(
                            "SELECT time = wt.collection_time, metric = wt.database_name, value = COUNT_BIG(*) FROM collect.waiting_tasks AS wt WHERE $__timeFilter(wt.collection_time) AND wt.blocking_session_id > 0 AND wt.database_name IS NOT NULL GROUP BY wt.collection_time, wt.database_name ORDER BY wt.collection_time;"
                        )
                    ],
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Waiting tasks",
                    sql=waiting_tasks_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Blocking
    # Upstream ref: GetBlockingEventsAsync (DatabaseService.QueryPerformance.Blocking.cs).
    # Deviation: blocking_spid/blocking_last_tran_started exist on our pinned
    # perfmon_version (v3.0.0) and are added below. Two further upstream fields are
    # NOT available at this pin and are intentionally omitted rather than breaking
    # every install: `monitor_loop` (column added in v3.1.0) and the
    # collect.dmv_blocking_snapshots fallback merge (table added in v3.1.0) that
    # GetBlockingEventsAsync appends so the grid stays populated when the
    # blocked-process-report XE captures nothing. Revisit both when perfmon_version
    # is bumped past v3.1.0.
    blocked_process_reports_sql = f"""
SELECT TOP (100)
    event_time = {tz_col('bpr.event_time')},
    collection_time = {tz_col('bpr.collection_time')},
    bpr.database_name,
    bpr.currentdbname,
    bpr.contentious_object,
    bpr.activity,
    bpr.blocking_tree,
    blocked_spid = bpr.spid,
    bpr.ecid,
    bpr.blocking_spid,
    blocking_last_tran_started = {tz_col('bpr.blocking_last_tran_started')},
    wait_seconds = bpr.wait_time_ms / 1000.0,
    bpr.status,
    bpr.isolation_level,
    bpr.lock_mode,
    bpr.resource_owner_type,
    bpr.wait_resource,
    bpr.transaction_count,
    bpr.transaction_name,
    last_transaction_started = {tz_col('bpr.last_transaction_started')},
    last_transaction_completed = {tz_col('bpr.last_transaction_completed')},
    bpr.priority,
    bpr.log_used,
    bpr.client_app,
    bpr.host_name,
    bpr.login_name,
    bpr.transaction_id,
    bpr.client_option_1,
    bpr.client_option_2,
    query_text = CONVERT(nvarchar(max), bpr.query_text),
    blocked_process_report_xml = CONVERT(nvarchar(max), bpr.blocked_process_report_xml)
FROM collect.blocking_BlockedProcessReport AS bpr
WHERE $__timeFilter(bpr.collection_time)
ORDER BY bpr.event_time DESC
OPTION(RECOMPILE);
"""
    # No upstream ref: collect.waiting_tasks filtered to blocked sessions, a Grafana-
    # native complement to the blocked_process_reports_sql grid above.
    blocking_chains_sql = f"""
SELECT TOP (100)
    collection_time = {tz_col('wt.collection_time')},
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
"""
    # Upstream ref: report.blocking_chain_analysis view backs this panel directly.
    # Deviation: the WPF block-chain viewer instead reconstructs the tree client-side
    # in BlockingChainReconstructor from raw pair rows (GetBlockingPairRowsAsync,
    # DatabaseService.QueryPerformance.Blocking.cs); this reads the server-side view's
    # precomputed blocking_chain_position/severity/recommendation instead.
    blocking_hierarchy_sql = f"""
SELECT TOP (100)
    collection_time = {tz_col('bca.collection_time')},
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
    bca.query_text,
    bca.statement_text
FROM report.blocking_chain_analysis AS bca
WHERE $__timeFilter(bca.collection_time)
ORDER BY bca.collection_time DESC, bca.sessions_blocked DESC;
"""

    # Upstream ref: GetBlockingEventsAsync (DatabaseService.QueryPerformance.Blocking.cs)
    y = subtab(
        panels,
        "Blocking",
        y,
        [
            (
                24,
                11,
                partial(
                    table,
                    "Blocked process reports",
                    sql=blocked_process_reports_sql,
                    sort_by=[{"displayName": "event_time", "desc": True}],
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Blocking chains (waiting tasks with head blockers)",
                    sql=blocking_chains_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
            (
                24,
                10,
                partial(
                    table,
                    "Blocking hierarchy",
                    sql=blocking_hierarchy_sql,
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
                ),
            ),
        ],
    )

    # Upstream sub-tab: Deadlocks
    # Upstream ref: GetDeadlocksAsync (DatabaseService.QueryPerformance.Blocking.cs)
    deadlock_participants_sql = f"""
SELECT TOP (100)
    event_date = {tz_col('d.event_date')},
    collection_time = {tz_col('d.collection_time')},
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
    last_tran_started = {tz_col('d.last_tran_started')},
    last_batch_started = {tz_col('d.last_batch_started')},
    last_batch_completed = {tz_col('d.last_batch_completed')},
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
    query = {strip_blitzlock_query_wrapper('CONVERT(nvarchar(max), d.query)')},
    object_names = {strip_blitzlock_object_names_wrapper('CONVERT(nvarchar(max), d.object_names)')}
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
ORDER BY d.event_date DESC
OPTION(RECOMPILE);
"""
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
    # No upstream ref: dedupes collect.deadlocks to one row per event (upstream's grid
    # shows one row per participant, both sides of each deadlock).
    deadlock_events_sql = f"""
SELECT TOP (50)
    event_date = {tz_col('d.event_date')},
    d.database_name,
    d.deadlock_group,
    d.deadlock_id
FROM (
    SELECT
        event_date,
        database_name,
        deadlock_group,
        deadlock_id,
        /*
          deadlock_group is a descriptive label ("Deadlock #1, Query #1 - VICTIM"), not a unique
          event id - the same query pair deadlocking repeatedly produces identical text every
          time, so partitioning by it collapses distinct recurrences of the same pattern into
          one row. event_date is the real per-event key.
          Prefer a non-victim participant as the representative row so the label shown here
          doesn't read as "the event was a victim".
        */
        rn = ROW_NUMBER() OVER (
            PARTITION BY event_date, database_name
            ORDER BY
                CASE WHEN deadlock_group LIKE N'%- VICTIM' THEN 1 ELSE 0 END,
                deadlock_id
        )
    FROM collect.deadlocks
    WHERE $__timeFilter(collection_time)
) AS d
WHERE d.rn = 1
ORDER BY d.event_date DESC;
"""

    # Upstream ref: GetDeadlocksAsync (DatabaseService.QueryPerformance.Blocking.cs)
    subtab(
        panels,
        "Deadlocks",
        y,
        [
            (
                24,
                11,
                partial(
                    table,
                    "Deadlock participants",
                    sql=deadlock_participants_sql,
                    sort_by=[{"displayName": "event_date", "desc": True}],
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Deadlock events",
                    sql=deadlock_events_sql,
                    overrides=[dl_deadlock_id, hide_deadlock_id],
                    sort_by=[{"displayName": "event_date", "desc": True}],
                    description="One row per unique deadlock event. Click a deadlock_group value to open the full participant detail and XDL graph.",
                ),
            ),
        ],
    )

    return dashboard("perfmon-blocking", "PerfMon · Locking", panels, [instance_var()])
