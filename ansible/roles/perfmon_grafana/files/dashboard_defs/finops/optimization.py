from functools import partial

from .._shared import *
from ._shared import (
    idle_database_ctes,
    monthly_cost_params_cte,
    monthly_cost_window_budget_cte,
)

# Upstream ref: GetFinOpsIdleDatabasesAsync, via idle_database_ctes() (shared with
# recommendations.py's idle-database finding).
_IDLE_DB_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {idle_database_ctes(include_details=True)}
SELECT
    database_name,
    total_size_mb,
    file_count,
    last_execution
FROM idle_dbs_all
ORDER BY total_size_mb DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

# Upstream ref: GetFinOpsTempdbSummaryAsync (DatabaseService.FinOps.Storage.cs)
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

# Upstream ref: GetFinOpsWaitCategorySummaryAsync (DatabaseService.FinOps.Queries.cs) +
# LoadWaitCategorySummaryAsync's post-query MonthlyCostShare calc
# (FinOpsContent.Loaders.cs) - each category's share of the $monthly_cost variable,
# prorated to the dashboard's time range. See monthly_cost_window_budget_cte().
_WAIT_STATS_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {monthly_cost_params_cte()},
    {monthly_cost_window_budget_cte()},
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
    bc.top_wait_time_ms,
    monthly_cost_share =
        CASE
            WHEN wb.window_budget > 0
            THEN CONVERT(decimal(12,2),
                bc.total_wait_time_ms * 1.0 / gt.total * wb.window_budget)
            ELSE NULL
        END
FROM by_category AS bc
CROSS JOIN grand_total AS gt
CROSS JOIN window_budget AS wb
ORDER BY bc.total_wait_time_ms DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

# Upstream ref: GetFinOpsExpensiveQueriesAsync (DatabaseService.FinOps.Queries.cs) +
# LoadExpensiveQueriesAsync's post-query MonthlyCostShare calc
# (FinOpsContent.Loaders.cs) - each query's share of the $monthly_cost variable,
# prorated to the dashboard's time range. Upstream sums TotalCpuMs over the already
# TOP(20)-limited result set, not every query, so top_queries below must be its own
# CTE - a window SUM() in the same SELECT as TOP would total before TOP trims it.
_EXPENSIVE_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {monthly_cost_params_cte()},
    {monthly_cost_window_budget_cte()},
    top_queries AS
    (
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
                LEFT(CONVERT(nvarchar(max), DECOMPRESS(qs.query_text)), 200),
            full_query_text =
                CONVERT(nvarchar(max), DECOMPRESS(qs.query_text))
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
    )
SELECT
    tq.database_name,
    tq.total_cpu_ms,
    tq.avg_cpu_ms_per_exec,
    tq.total_reads,
    tq.avg_reads_per_exec,
    tq.executions,
    tq.query_preview,
    tq.full_query_text,
    monthly_cost_share =
        CASE
            WHEN wb.window_budget > 0
            THEN CONVERT(decimal(12,2),
                tq.total_cpu_ms * 1.0 / NULLIF(SUM(tq.total_cpu_ms) OVER (), 0) * wb.window_budget)
            ELSE NULL
        END
FROM top_queries AS tq
CROSS JOIN window_budget AS wb
ORDER BY tq.total_cpu_ms DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

# Upstream ref: GetFinOpsMemoryGrantEfficiencyAsync (DatabaseService.FinOps.Queries.cs)
_MEMORY_GRANTS_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    day = CONVERT(date, {tz_col('mg.collection_time')}),
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
GROUP BY CONVERT(date, {tz_col('mg.collection_time')})
ORDER BY CONVERT(date, {tz_col('mg.collection_time')})
OPTION(MAXDOP 1, RECOMPILE);
"""


def optimization():
    panels = []
    flow(
        panels,
        0,
        [
            (
                24,
                8,
                partial(
                    table,
                    "Idle Databases (no activity in 7 days)",
                    sql=_IDLE_DB_SQL,
                    sort_by=[{"displayName": "total_size_mb", "desc": True}],
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "TempDB Pressure (peak = last 24h)",
                    sql=_TEMPDB_SQL,
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
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Wait Stats Summary",
                    sql=_WAIT_STATS_SQL,
                    overrides=[
                        col_unit(
                            "monthly_cost_share", "currencyUSD", "Monthly Cost Share"
                        )
                    ],
                    sort_by=[{"displayName": "total_wait_time_ms", "desc": True}],
                ),
            ),
            (
                24,
                10,
                partial(
                    table,
                    "Expensive Queries (Top 20 by CPU)",
                    sql=_EXPENSIVE_SQL,
                    overrides=[
                        col_unit(
                            "monthly_cost_share", "currencyUSD", "Monthly Cost Share"
                        )
                    ],
                    sort_by=[{"displayName": "total_cpu_ms", "desc": True}],
                ),
            ),
            (24, 8, partial(table, "Memory Grant Efficiency", sql=_MEMORY_GRANTS_SQL)),
        ],
    )
    return finops_dashboard(
        "finops-optimization",
        "FinOps · Optimization",
        panels,
        [instance_var(), text_var("monthly_cost", "Monthly Cost (USD)", "0")],
        time_from="now-24h",
        refresh="5m",
    )
