from .._shared import *

_IDLE_DB_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    db_sizes AS
    (
        SELECT
            database_name,
            total_size_mb = SUM(total_size_mb),
            file_count = COUNT(*)
        FROM collect.database_size_stats
        WHERE collection_time =
        (
            SELECT MAX(collection_time) FROM collect.database_size_stats
        )
        GROUP BY database_name
    ),
    db_activity AS
    (
        SELECT
            database_name,
            total_executions = SUM(execution_count_delta),
            last_execution = MAX(last_execution_time)
        FROM collect.query_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        AND   execution_count_delta IS NOT NULL
        GROUP BY database_name
    )
SELECT
    ds.database_name,
    ds.total_size_mb,
    ds.file_count,
    last_execution = a.last_execution
FROM db_sizes AS ds
LEFT JOIN db_activity AS a ON a.database_name = ds.database_name
WHERE ISNULL(a.total_executions, 0) = 0
AND   ds.database_name NOT IN
      (N'master', N'model', N'msdb', N'tempdb', N'PerformanceMonitor')
ORDER BY ds.total_size_mb DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

_TEMPDB_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    latest AS
    (
        SELECT TOP (1)
            user_object_reserved_mb,
            internal_object_reserved_mb,
            version_store_reserved_mb,
            total_reserved_mb
        FROM collect.tempdb_stats
        ORDER BY collection_time DESC
    ),
    peak AS
    (
        SELECT
            max_user_mb = MAX(user_object_reserved_mb),
            max_internal_mb = MAX(internal_object_reserved_mb),
            max_version_store_mb = MAX(version_store_reserved_mb),
            max_total_mb = MAX(total_reserved_mb)
        FROM collect.tempdb_stats
        WHERE collection_time >= DATEADD(HOUR, -24, SYSDATETIME())
    )
SELECT
    metric = N'User Objects',
    current_mb = l.user_object_reserved_mb,
    peak_24h_mb = p.max_user_mb,
    warning =
        CASE
            WHEN p.max_user_mb > 1024
            THEN N'High user object usage'
            ELSE N''
        END
FROM latest AS l
CROSS JOIN peak AS p
UNION ALL
SELECT
    N'Internal Objects',
    l.internal_object_reserved_mb,
    p.max_internal_mb,
    CASE
        WHEN p.max_internal_mb > 1024
        THEN N'High internal object usage (sorts/hashes)'
        ELSE N''
    END
FROM latest AS l
CROSS JOIN peak AS p
UNION ALL
SELECT
    N'Version Store',
    l.version_store_reserved_mb,
    p.max_version_store_mb,
    CASE
        WHEN p.max_version_store_mb > 2048
        THEN N'Version store pressure - check long-running transactions'
        ELSE N''
    END
FROM latest AS l
CROSS JOIN peak AS p
UNION ALL
SELECT
    N'Total Reserved',
    l.total_reserved_mb,
    p.max_total_mb,
    N''
FROM latest AS l
CROSS JOIN peak AS p
OPTION(MAXDOP 1, RECOMPILE);
"""

_WAIT_STATS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    categorized AS
    (
        SELECT
            category =
                CASE
                    WHEN wait_type IN
                         (N'SOS_SCHEDULER_YIELD', N'CXPACKET', N'CXCONSUMER',
                          N'CXSYNC_PORT', N'CXSYNC_CONSUMER')
                    THEN N'CPU'
                    WHEN wait_type LIKE N'PAGEIOLATCH%'
                    OR   wait_type IN
                         (N'WRITELOG', N'IO_COMPLETION', N'ASYNC_IO_COMPLETION')
                    THEN N'Storage'
                    WHEN wait_type IN
                         (N'RESOURCE_SEMAPHORE', N'RESOURCE_SEMAPHORE_QUERY_COMPILE',
                          N'CMEMTHREAD')
                    THEN N'Memory'
                    WHEN wait_type = N'ASYNC_NETWORK_IO'
                    THEN N'Network'
                    WHEN wait_type LIKE N'LCK_M_%'
                    THEN N'Locks'
                    ELSE N'Other'
                END,
            wait_type,
            wait_time_ms = SUM(wait_time_ms_delta),
            waiting_tasks = SUM(waiting_tasks_count_delta)
        FROM collect.wait_stats
        WHERE $__timeFilter(collection_time)
        AND   wait_time_ms_delta IS NOT NULL
        AND   wait_time_ms_delta > 0
        GROUP BY
            CASE
                WHEN wait_type IN
                     (N'SOS_SCHEDULER_YIELD', N'CXPACKET', N'CXCONSUMER',
                      N'CXSYNC_PORT', N'CXSYNC_CONSUMER')
                THEN N'CPU'
                WHEN wait_type LIKE N'PAGEIOLATCH%'
                OR   wait_type IN
                     (N'WRITELOG', N'IO_COMPLETION', N'ASYNC_IO_COMPLETION')
                THEN N'Storage'
                WHEN wait_type IN
                     (N'RESOURCE_SEMAPHORE', N'RESOURCE_SEMAPHORE_QUERY_COMPILE',
                      N'CMEMTHREAD')
                THEN N'Memory'
                WHEN wait_type = N'ASYNC_NETWORK_IO'
                THEN N'Network'
                WHEN wait_type LIKE N'LCK_M_%'
                THEN N'Locks'
                ELSE N'Other'
            END,
            wait_type
    ),
    by_category AS
    (
        SELECT
            category,
            total_wait_time_ms = SUM(wait_time_ms),
            total_waiting_tasks = SUM(waiting_tasks),
            top_wait_type =
                MAX(CASE WHEN rn = 1 THEN wait_type END),
            top_wait_time_ms =
                MAX(CASE WHEN rn = 1 THEN wait_time_ms END)
        FROM
        (
            SELECT *,
                rn = ROW_NUMBER() OVER
                     (PARTITION BY category ORDER BY wait_time_ms DESC)
            FROM categorized
        ) AS ranked
        GROUP BY category
    ),
    grand_total AS
    (
        SELECT total = NULLIF(SUM(total_wait_time_ms), 0)
        FROM by_category
    )
SELECT
    bc.category,
    bc.total_wait_time_ms,
    bc.total_waiting_tasks,
    pct_of_total =
        CONVERT(decimal(5,1), bc.total_wait_time_ms * 100.0 / gt.total),
    bc.top_wait_type,
    bc.top_wait_time_ms
FROM by_category AS bc
CROSS JOIN grand_total AS gt
ORDER BY bc.total_wait_time_ms DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

_EXPENSIVE_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT TOP (20)
    qs.database_name,
    total_cpu_ms =
        SUM(qs.total_worker_time_delta) / 1000,
    avg_cpu_ms_per_exec =
        CONVERT(decimal(19,2),
            SUM(qs.total_worker_time_delta) / 1000.0 /
            NULLIF(SUM(qs.execution_count_delta), 0)),
    total_reads =
        SUM(qs.total_logical_reads_delta),
    avg_reads_per_exec =
        CONVERT(decimal(19,0),
            SUM(qs.total_logical_reads_delta) * 1.0 /
            NULLIF(SUM(qs.execution_count_delta), 0)),
    executions =
        SUM(qs.execution_count_delta),
    query_preview =
        LEFT(CONVERT(nvarchar(max), DECOMPRESS(qs.query_text)), 200)
FROM collect.query_stats AS qs
WHERE $__timeFilter(collection_time)
AND   qs.total_worker_time_delta IS NOT NULL
AND   qs.total_worker_time_delta > 0
GROUP BY
    qs.database_name,
    qs.sql_handle,
    qs.statement_start_offset,
    qs.statement_end_offset,
    qs.query_text
ORDER BY SUM(qs.total_worker_time_delta) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

_MEMORY_GRANTS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    day = CONVERT(date, mg.collection_time),
    avg_granted_mb = AVG(mg.granted_memory_mb),
    avg_used_mb = AVG(mg.used_memory_mb),
    efficiency_pct =
        CONVERT(decimal(5,1),
            AVG(mg.used_memory_mb) * 100.0 /
            NULLIF(AVG(mg.granted_memory_mb), 0)),
    peak_granted_mb = MAX(mg.granted_memory_mb),
    wasted_mb =
        CONVERT(decimal(19,2), AVG(mg.granted_memory_mb) - AVG(mg.used_memory_mb)),
    total_grantees = SUM(mg.grantee_count),
    total_waiters = SUM(mg.waiter_count),
    timeout_errors = SUM(mg.timeout_error_count_delta),
    forced_grants = SUM(mg.forced_grant_count_delta)
FROM collect.memory_grant_stats AS mg
WHERE $__timeFilter(collection_time)
GROUP BY CONVERT(date, mg.collection_time)
ORDER BY CONVERT(date, mg.collection_time)
OPTION(MAXDOP 1, RECOMPILE);
"""


def optimization():
    reset_id()
    panels = [
        table(
            "Idle Databases (no activity in 7 days)",
            0,
            0,
            24,
            8,
            _IDLE_DB_SQL,
            sort_by=[{"displayName": "total_size_mb", "desc": True}],
        ),
        table(
            "TempDB Pressure (peak = last 24h)",
            0,
            8,
            24,
            8,
            _TEMPDB_SQL,
            overrides=[
                status_colors(
                    "warning",
                    {
                        "": "transparent",
                        "High user object usage": "orange",
                        "High internal object usage (sorts/hashes)": "orange",
                        "Version store pressure - check long-running transactions": "orange",
                    },
                )
            ],
        ),
        table(
            "Wait Stats Summary",
            0,
            16,
            24,
            8,
            _WAIT_STATS_SQL,
            sort_by=[{"displayName": "total_wait_time_ms", "desc": True}],
        ),
        table(
            "Expensive Queries (Top 20 by CPU)",
            0,
            24,
            24,
            10,
            _EXPENSIVE_SQL,
            sort_by=[{"displayName": "total_cpu_ms", "desc": True}],
        ),
        table(
            "Memory Grant Efficiency",
            0,
            34,
            24,
            8,
            _MEMORY_GRANTS_SQL,
        ),
    ]
    return finops_dashboard(
        "finops-optimization",
        "FinOps · Optimization",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
