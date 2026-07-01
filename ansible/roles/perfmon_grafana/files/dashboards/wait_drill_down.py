from ._shared import *


# Wait type drill-down - query snapshots for a single wait type.
# Navigated to from the Wait Stats table on the Resource Metrics dashboard via data
# link on the wait_type column.
# Mirrors the upstream WaitDrillDownWindow (DatabaseService.QueryPerformance.Snapshots.cs
# GetQuerySnapshotsByWaitTypeAsync): shows all query snapshots whose wait_info contains
# the selected wait type, using sp_WhoIsActive's '%)WAIT_TYPE%' LIKE pattern.
# Only the Filtered path from WaitDrillDownHelper.Classify is implemented. Correlated
# (SOS_SCHEDULER_YIELD, WRITELOG, CXPACKET, CXCONSUMER, RESOURCE_SEMAPHORE, LATCH_EX,
# LATCH_UP, THREADPOOL, PAGEIOLATCH_*) and Chain (LCK_M_* blocking chain walk) are not
# implemented.
def wait_drill_down():
    reset_id()
    panels = []

    snap_sql = """
SELECT TOP (500)
    qs.collection_time,
    duration = qs.[dd hh:mm:ss.mss],
    qs.session_id,
    qs.status,
    qs.wait_info,
    qs.blocking_session_id,
    qs.blocked_session_count,
    qs.database_name,
    qs.login_name,
    qs.host_name,
    qs.program_name,
    sql_text = REPLACE(REPLACE(CONVERT(nvarchar(max), qs.sql_text), N'<?query --' + CHAR(13) + CHAR(10), N''), CHAR(13) + CHAR(10) + N'--?>', N''),
    sql_command = REPLACE(REPLACE(CONVERT(nvarchar(max), qs.sql_command), N'<?query --' + CHAR(13) + CHAR(10), N''), CHAR(13) + CHAR(10) + N'--?>', N''),
    cpu_ms = qs.CPU,
    qs.reads,
    qs.writes,
    qs.physical_reads,
    qs.context_switches,
    used_memory_mb = qs.used_memory,
    tempdb_current_mb = qs.tempdb_current,
    tempdb_allocations_mb = qs.tempdb_allocations,
    qs.tran_log_writes,
    qs.open_tran_count,
    qs.percent_complete,
    qs.start_time,
    qs.tran_start_time,
    qs.request_id,
    additional_info = CONVERT(nvarchar(max), qs.additional_info)
FROM report.query_snapshots AS qs
WHERE $__timeFilter(qs.collection_time)
AND ('${wait_type}' = '' OR CONVERT(nvarchar(max), qs.wait_info) LIKE N'%)' + '${wait_type}' + N'%')
ORDER BY qs.collection_time DESC, qs.session_id;
"""

    panels.append(row("Wait Stats Over Time", 0))
    panels.append(
        timeseries(
            "Wait ms/sec",
            0,
            1,
            12,
            8,
            [target("""
WITH wait_data AS (
    SELECT
        ws.collection_time,
        ws.wait_time_ms_delta,
        interval_seconds = DATEDIFF(SECOND,
            LAG(ws.collection_time) OVER (ORDER BY ws.collection_time),
            ws.collection_time)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time) AND ws.wait_type = N'${wait_type}'
)
SELECT
    time = collection_time,
    wait_ms_sec = CONVERT(decimal(18,4), CAST(wait_time_ms_delta AS decimal(19,4)) / interval_seconds)
FROM wait_data
WHERE interval_seconds > 0
ORDER BY collection_time;
""")],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Waiting tasks count",
            12,
            1,
            12,
            8,
            [target("""
SELECT time = ws.collection_time, waiting_tasks = ws.waiting_tasks_count
FROM collect.wait_stats AS ws
WHERE $__timeFilter(ws.collection_time) AND ws.wait_type = N'${wait_type}'
ORDER BY ws.collection_time;
""")],
        )
    )

    qs_link = col_datalink(
        "query_hash",
        "View query history",
        "/d/perfmon-query-history?${__url_time_range}&var-instance=${instance}"
        "&var-database=${__data.fields.database_name}&var-query_hash=${__data.fields.query_hash}",
    )
    panels.append(row("Query Stats", 9))
    panels.append(
        table(
            "Queries seen with ${wait_type} (grouped by query hash)",
            0,
            10,
            24,
            14,
            """
WITH snap AS (
    SELECT
        plan_str         = CONVERT(nvarchar(max), qs.query_plan),
        qs.database_name,
        cpu              = TRY_CAST(qs.cpu AS bigint),
        reads            = TRY_CAST(qs.reads AS bigint),
        writes           = TRY_CAST(qs.writes AS bigint),
        sql_text_preview = LEFT(
            REPLACE(REPLACE(CONVERT(nvarchar(max), qs.sql_text),
                N'<?query --' + CHAR(13) + CHAR(10), N''),
                CHAR(13) + CHAR(10) + N'--?>', N''),
            300)
    FROM report.query_snapshots AS qs
    WHERE $__timeFilter(qs.collection_time)
        AND ('${wait_type}' = '' OR CONVERT(nvarchar(max), qs.wait_info) LIKE N'%)' + '${wait_type}' + N'%')
        AND qs.query_plan IS NOT NULL
),
with_hash AS (
    SELECT
        query_hash = SUBSTRING(s.plan_str, CHARINDEX('QueryHash="', s.plan_str) + 11, 18),
        s.database_name,
        s.cpu,
        s.reads,
        s.writes,
        s.sql_text_preview
    FROM snap AS s
    WHERE s.plan_str LIKE '%QueryHash="%'
)
SELECT TOP (100)
    wh.query_hash,
    wh.database_name,
    appearances = COUNT(*),
    avg_cpu_ms  = AVG(wh.cpu),
    max_cpu_ms  = MAX(wh.cpu),
    avg_reads   = AVG(wh.reads),
    max_reads   = MAX(wh.reads),
    avg_writes  = AVG(wh.writes),
    sql_text    = MAX(wh.sql_text_preview)
FROM with_hash AS wh
GROUP BY wh.query_hash, wh.database_name
ORDER BY COUNT(*) DESC;
""",
            overrides=[qs_link],
            sort_by=[{"displayName": "appearances", "desc": True}],
            description=(
                "Grouped by query hash extracted from the execution plan XML. "
                "Appearances = sp_WhoIsActive snapshots where the query was active with this wait type. "
                "Only covers snapshots where a plan was captured; plan-less rows are excluded."
            ),
        )
    )

    panels.append(row("Query Snapshots", 24))
    panels.append(
        table(
            "Sessions with ${wait_type} in wait_info (top 500)",
            0,
            25,
            24,
            16,
            snap_sql,
            sort_by=[{"displayName": "collection_time", "desc": True}],
            description=("Rows where wait_info contains the selected wait type. "),
        )
    )

    return detail_dashboard(
        "perfmon-wait-drill-down",
        "PerfMon · Wait Drill-Down",
        panels,
        [
            instance_var(),
            text_var("wait_type", "Wait Type"),
        ],
        time_from="now-3h",
    )
