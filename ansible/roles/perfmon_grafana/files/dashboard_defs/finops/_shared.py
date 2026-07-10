"""Shared SQL fragments for FinOps dashboards, to avoid re-deriving the
same CTE skeletons independently per module.
"""

from .._shared import tz_col, tz_from, tz_to

IDLE_DB_EXCLUSIONS = "N'master', N'model', N'msdb', N'tempdb', N'PerformanceMonitor'"


def monthly_cost_params_cte():
    """params CTE parsing the $monthly_cost text variable into a decimal.

    Upstream ref: ServerConnection.MonthlyCostUsd (AddServerDialog.xaml.cs), the
    per-server value FinOps cost-share/savings figures are derived from
    (FinOpsContent.Loaders.cs, DatabaseService.FinOps.Recommendations.cs). Grafana
    has no persisted per-server config table, so a $monthly_cost dashboard variable
    stands in - re-entered per session rather than saved, unlike upstream's dialog.
    Shared by database_sizes.py, optimization.py, recommendations.py.
    """
    return """params AS
    (
        SELECT monthly_cost = ISNULL(TRY_CONVERT(decimal(12,2), NULLIF(N${monthly_cost:sqlstring}, N'')), 0)
    )"""


def monthly_cost_window_budget_cte():
    """window_budget CTE: monthly_cost prorated to the panel's own time range.

    Upstream ref: LoadWaitCategorySummaryAsync/LoadExpensiveQueriesAsync
    (FinOpsContent.Loaders.cs) - windowBudget = monthly_cost * (hoursBack / 730.0),
    where hoursBack comes from a combo box. Grafana has no such combo, so hoursBack
    is derived from the dashboard's own time range instead. Requires
    monthly_cost_params_cte() to precede it in the same WITH clause.
    """
    return f"""window_budget AS
    (
        SELECT window_budget = p.monthly_cost * (DATEDIFF(HOUR, {tz_from()}, {tz_to()}) / 730.0)
        FROM params AS p
    )"""


def idle_database_ctes(include_details=False):
    """Shared idle-database CTEs (db_sizes/db_activity/idle_dbs_all) used by
    optimization.py and recommendations.py, so both stay on the same 7-day
    window and exclusion list.

    Upstream ref: GetFinOpsIdleDatabasesAsync (DatabaseService.FinOps.Storage.cs).
    """
    size_cols = ["total_size_mb = SUM(total_size_mb)"]
    activity_cols = ["total_executions = SUM(execution_count_delta)"]
    idle_cols = ["ds.database_name", "ds.total_size_mb"]
    if include_details:
        size_cols.append("file_count = COUNT(*)")
        activity_cols.append(f"last_execution = {tz_col('MAX(last_execution_time)')}")
        idle_cols += ["ds.file_count", "last_execution = a.last_execution"]

    sep = ",\n            "
    size_select = sep.join(size_cols)
    activity_select = sep.join(activity_cols)
    idle_select = sep.join(idle_cols)
    return f"""db_sizes AS
    (
        SELECT
            database_name,
            {size_select}
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
            {activity_select}
        FROM collect.query_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        AND   execution_count_delta IS NOT NULL
        GROUP BY database_name
    ),
    idle_dbs_all AS
    (
        SELECT
            {idle_select}
        FROM db_sizes AS ds
        LEFT JOIN db_activity AS a ON a.database_name = ds.database_name
        WHERE ISNULL(a.total_executions, 0) = 0
        AND   ds.database_name NOT IN ({IDLE_DB_EXCLUSIONS})
    )"""


def growth_lookback_ctes(table, group_cols, metric_sql, metric_alias):
    """boundaries/past_7d/past_30d/oldest CTE chain shared by any
    "current vs N days ago" growth panel (storage_growth.py, object_sizes.py).
    metric_sql is the aggregate expression e.g. SUM(reserved_mb) computed
    per group_cols at each lookback point, aliased to metric_alias.

    Upstream ref: GetFinOpsStorageGrowthAsync / GetObjectSizeGrowthAsync
    (DatabaseService.FinOps.Storage.cs / .IndexObjects.cs)
    """
    group_by = ", ".join(group_cols)
    select_cols = ",\n            ".join(group_cols)
    return f"""boundaries AS
    (
        SELECT
            latest_time = MAX(collection_time),
            earliest_time = MIN(collection_time),
            days_of_data = DATEDIFF(DAY, MIN(collection_time), MAX(collection_time))
        FROM {table}
    ),
    past_7d AS
    (
        SELECT
            {select_cols},
            {metric_alias} = {metric_sql}
        FROM {table}
        WHERE collection_time =
        (
            SELECT MAX(collection_time)
            FROM {table}
            WHERE collection_time <= DATEADD(DAY, -7, SYSDATETIME())
        )
        GROUP BY {group_by}
    ),
    past_30d AS
    (
        SELECT
            {select_cols},
            {metric_alias} = {metric_sql}
        FROM {table}
        WHERE collection_time =
        (
            SELECT MAX(collection_time)
            FROM {table}
            WHERE collection_time <= DATEADD(DAY, -30, SYSDATETIME())
        )
        GROUP BY {group_by}
    ),
    oldest AS
    (
        SELECT
            {select_cols},
            {metric_alias} = {metric_sql}
        FROM {table}
        WHERE collection_time = (SELECT earliest_time FROM boundaries)
        GROUP BY {group_by}
    )"""


# Upstream ref: FinOpsHealthCalculator (CpuScore/MemoryScore/StorageScore/Overall,
# DatabaseService.FinOps.Models.cs). The score CASE bodies and 0.40/0.30/0.30 weighting
# are shared verbatim by server_inventory.py and utilization.py; only the
# source expressions for CPU/memory/storage inputs differ depending on the call.

STORAGE_FREE_PCT_CTE = """storage AS
    (
        SELECT
            free_pct =
                SUM(dss.free_space_mb) * 100.0 / NULLIF(SUM(dss.total_size_mb), 0)
        FROM collect.database_size_stats AS dss
        WHERE dss.collection_time =
        (
            SELECT MAX(collection_time) FROM collect.database_size_stats
        )
    )"""


def health_score_cases(cpu_expr, mem_expr, storage_expr):
    return f"""            cpu_score =
                CONVERT(int,
                    CASE
                        WHEN ISNULL({cpu_expr}, 0) <= 70
                            THEN 100 - ISNULL({cpu_expr}, 0) * 50 / 70
                        WHEN 50 - ({cpu_expr} - 70) * 50 / 30 < 0 THEN 0
                        ELSE 50 - ({cpu_expr} - 70) * 50 / 30
                    END),
            mem_score =
                CONVERT(int,
                    CASE
                        WHEN ISNULL({mem_expr}, 0) <= 0.30 THEN 60
                        WHEN {mem_expr} <= 0.85 THEN 100
                        WHEN {mem_expr} <= 0.95
                            THEN 100 - ({mem_expr} - 0.85) * 800
                        WHEN 20 - ({mem_expr} - 0.95) * 400 < 0 THEN 0
                        ELSE 20 - ({mem_expr} - 0.95) * 400
                    END),
            storage_score =
                CONVERT(int,
                    CASE
                        WHEN ISNULL({storage_expr}, 100) >= 30 THEN 100
                        WHEN {storage_expr} >= 10 THEN 50 + ({storage_expr} - 10) * 2.5
                        ELSE ISNULL({storage_expr}, 100) * 5
                    END)"""


HEALTH_SCORE_FINAL_SELECT = """SELECT
    health_score = CONVERT(int, cpu_score * 0.4 + mem_score * 0.3 + storage_score * 0.3)
FROM scores
OPTION(MAXDOP 1, RECOMPILE);
"""
