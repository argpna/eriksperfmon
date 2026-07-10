from .._shared import *
from ._shared import growth_lookback_ctes

# Upstream ref: GetFinOpsStorageGrowthAsync (DatabaseService.FinOps.Storage.cs).
# boundaries/past_7d/past_30d/oldest CTEs come from growth_lookback_ctes(), shared with
# other finops growth panels.
_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {growth_lookback_ctes("collect.database_size_stats", ["database_name"], "SUM(total_size_mb)", "size_mb")},
    latest AS
    (
        SELECT
            database_name,
            current_size_mb = SUM(total_size_mb)
        FROM collect.database_size_stats
        WHERE collection_time = (SELECT latest_time FROM boundaries)
        GROUP BY database_name
    )
SELECT
    l.database_name,
    l.current_size_mb,
    size_7d_ago_mb = COALESCE(p7.size_mb, o.size_mb),
    size_30d_ago_mb = COALESCE(p30.size_mb, p7.size_mb, o.size_mb),
    growth_7d_mb =
        l.current_size_mb - COALESCE(p7.size_mb, o.size_mb, l.current_size_mb),
    growth_30d_mb =
        l.current_size_mb - COALESCE(p30.size_mb, p7.size_mb, o.size_mb, l.current_size_mb),
    daily_growth_rate_mb =
        CASE
            WHEN b.days_of_data >= 1
            THEN (l.current_size_mb - COALESCE(o.size_mb, l.current_size_mb))
                 / CAST(b.days_of_data AS decimal(10,1))
            ELSE 0
        END,
    growth_pct_30d =
        CASE
            WHEN COALESCE(p30.size_mb, p7.size_mb, o.size_mb) IS NOT NULL
            AND  COALESCE(p30.size_mb, p7.size_mb, o.size_mb) > 0
            THEN (l.current_size_mb - COALESCE(p30.size_mb, p7.size_mb, o.size_mb))
                 * 100.0 / COALESCE(p30.size_mb, p7.size_mb, o.size_mb)
            ELSE 0
        END
FROM latest AS l
CROSS JOIN boundaries AS b
LEFT JOIN past_7d AS p7 ON p7.database_name = l.database_name
LEFT JOIN past_30d AS p30 ON p30.database_name = l.database_name
LEFT JOIN oldest AS o ON o.database_name = l.database_name
ORDER BY
    l.current_size_mb - COALESCE(p30.size_mb, p7.size_mb, o.size_mb, l.current_size_mb) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def storage_growth():
    panels = [
        table(
            "Storage Growth",
            0,
            0,
            24,
            14,
            _SQL,
            sort_by=[{"displayName": "growth_30d_mb", "desc": True}],
        )
    ]
    return finops_dashboard(
        "finops-storage-growth",
        "FinOps · Storage Growth",
        panels,
        [instance_var()],
        time_from="now-30d",
        refresh="15m",
    )
