from .._shared import *
from ._shared import monthly_cost_params_cte

# Upstream ref: GetFinOpsDatabaseSizeStatsAsync (DatabaseService.FinOps.Storage.cs) +
# LoadDatabaseSizesAsync's post-query MonthlyCostShare calc (FinOpsContent.Loaders.cs).
# monthly_cost_share is each file's share of total size across every file returned,
# scaled by the $monthly_cost dashboard variable - see monthly_cost_params_cte().
_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {monthly_cost_params_cte()}
SELECT
    dss.database_name,
    file_type_desc = dss.file_type_desc,
    file_name = dss.file_name,
    total_size_mb = dss.total_size_mb,
    used_size_mb = dss.used_size_mb,
    free_space_mb = dss.free_space_mb,
    used_pct = dss.used_pct,
    volume_mount_point = dss.volume_mount_point,
    volume_total_mb = dss.volume_total_mb,
    volume_free_mb = dss.volume_free_mb,
    auto_growth_display =
        CASE
            WHEN dss.is_percent_growth = 1
            THEN CAST(dss.growth_pct AS varchar) + N'%'
            WHEN dss.auto_growth_mb = 0
            THEN N'Disabled'
            ELSE CAST(CAST(dss.auto_growth_mb AS int) AS varchar) + N' MB'
        END,
    max_size_mb = dss.max_size_mb,
    recovery_model_desc = dss.recovery_model_desc,
    vlf_count =
        CASE
            WHEN dss.file_type_desc = N'LOG'
            THEN ISNULL(CAST(dss.vlf_count AS varchar), N'-')
            ELSE N'N/A'
        END,
    state_desc = dss.state_desc,
    monthly_cost_share =
        CASE
            WHEN p.monthly_cost > 0
            THEN CONVERT(decimal(12,2),
                dss.total_size_mb / NULLIF(SUM(dss.total_size_mb) OVER (), 0) * p.monthly_cost)
            ELSE NULL
        END
FROM collect.database_size_stats AS dss
CROSS JOIN params AS p
WHERE dss.collection_time =
(
    SELECT MAX(collection_time)
    FROM collect.database_size_stats
)
ORDER BY
    dss.total_size_mb DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def database_sizes():
    panels = [
        table(
            "Database Sizes",
            0,
            0,
            24,
            14,
            _SQL,
            overrides=[
                status_colors(
                    "state_desc",
                    {
                        "ONLINE": "green",
                        "RESTORING": "orange",
                        "RECOVERING": "orange",
                        "OFFLINE": "red",
                    },
                ),
                col_unit("monthly_cost_share", "currencyUSD", "Monthly Cost Share"),
            ],
        )
    ]
    return finops_dashboard(
        "finops-database-sizes",
        "FinOps · Database Sizes",
        panels,
        [instance_var(), text_var("monthly_cost", "Monthly Cost (USD)", "0")],
        time_from="now-24h",
        refresh="15m",
    )
