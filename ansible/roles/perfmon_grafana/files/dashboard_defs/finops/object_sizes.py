from .._shared import *
from ._shared import growth_lookback_ctes

# Upstream ref: GetObjectSizeGrowthAsync (DatabaseService.FinOps.IndexObjects.cs).
# boundaries/past_7d/past_30d/oldest CTEs come from growth_lookback_ctes(), shared with
# other finops growth panels.
_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    {growth_lookback_ctes("collect.index_object_stats", ["database_name", "schema_name", "table_name"], "SUM(reserved_mb)", "reserved_mb")},
    latest AS
    (
        SELECT
            database_name,
            schema_name,
            table_name,
            current_reserved_mb = SUM(reserved_mb),
            current_used_mb = SUM(used_mb),
            total_rows = MAX(total_rows),
            index_count = COUNT_BIG(*)
        FROM collect.index_object_stats
        WHERE collection_time = (SELECT latest_time FROM boundaries)
        GROUP BY database_name, schema_name, table_name
    )
SELECT TOP (100)
    l.database_name,
    l.schema_name,
    l.table_name,
    l.current_reserved_mb,
    l.current_used_mb,
    l.total_rows,
    l.index_count,
    growth_7d_mb =
        l.current_reserved_mb -
        COALESCE(p7.reserved_mb, o.reserved_mb, l.current_reserved_mb),
    growth_30d_mb =
        l.current_reserved_mb -
        COALESCE(p30.reserved_mb, p7.reserved_mb, o.reserved_mb, l.current_reserved_mb),
    daily_growth_rate_mb =
        CASE
            WHEN b.days_of_data >= 1
            THEN (l.current_reserved_mb -
                  COALESCE(o.reserved_mb, l.current_reserved_mb)) /
                 CAST(b.days_of_data AS decimal(10,1))
            ELSE 0
        END,
    growth_pct_30d =
        CASE
            WHEN COALESCE(p30.reserved_mb, p7.reserved_mb, o.reserved_mb) > 0
            THEN (l.current_reserved_mb -
                  COALESCE(p30.reserved_mb, p7.reserved_mb, o.reserved_mb)) * 100.0 /
                 COALESCE(p30.reserved_mb, p7.reserved_mb, o.reserved_mb)
            ELSE 0
        END
FROM latest AS l
CROSS JOIN boundaries AS b
LEFT JOIN past_7d AS p7
  ON  p7.database_name = l.database_name
  AND p7.schema_name = l.schema_name
  AND p7.table_name = l.table_name
LEFT JOIN past_30d AS p30
  ON  p30.database_name = l.database_name
  AND p30.schema_name = l.schema_name
  AND p30.table_name = l.table_name
LEFT JOIN oldest AS o
  ON  o.database_name = l.database_name
  AND o.schema_name = l.schema_name
  AND o.table_name = l.table_name
ORDER BY l.current_reserved_mb DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def object_sizes():
    dl_table = col_datalinks(
        "table_name",
        [
            (
                "View index usage",
                "/d/finops-index-usage?${__url_time_range}&var-instance=${instance}"
                "&var-database=${__data.fields.database_name}"
                "&var-table_name=${__data.fields.table_name}",
            ),
            (
                "View locking & contention",
                "/d/finops-locking?${__url_time_range}&var-instance=${instance}"
                "&var-database=${__data.fields.database_name}"
                "&var-table_name=${__data.fields.table_name}",
            ),
        ],
    )
    panels = [
        table(
            "Object Sizes & Growth",
            0,
            0,
            24,
            16,
            _SQL,
            overrides=[dl_table],
            sort_by=[{"displayName": "current_reserved_mb", "desc": True}],
        )
    ]
    return finops_dashboard(
        "finops-object-sizes",
        "FinOps · Object Sizes & Growth",
        panels,
        [instance_var()],
        time_from="now-30d",
        refresh="15m",
    )
