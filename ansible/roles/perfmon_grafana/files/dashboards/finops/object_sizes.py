from .._shared import *

_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    boundaries AS
    (
        SELECT
            latest_time = MAX(collection_time),
            earliest_time = MIN(collection_time),
            days_of_data = DATEDIFF(DAY, MIN(collection_time), MAX(collection_time))
        FROM collect.index_object_stats
    ),
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
    ),
    past_7d AS
    (
        SELECT
            database_name,
            schema_name,
            table_name,
            reserved_mb = SUM(reserved_mb)
        FROM collect.index_object_stats
        WHERE collection_time =
        (
            SELECT MAX(collection_time)
            FROM collect.index_object_stats
            WHERE collection_time <= DATEADD(DAY, -7, SYSDATETIME())
        )
        GROUP BY database_name, schema_name, table_name
    ),
    past_30d AS
    (
        SELECT
            database_name,
            schema_name,
            table_name,
            reserved_mb = SUM(reserved_mb)
        FROM collect.index_object_stats
        WHERE collection_time =
        (
            SELECT MAX(collection_time)
            FROM collect.index_object_stats
            WHERE collection_time <= DATEADD(DAY, -30, SYSDATETIME())
        )
        GROUP BY database_name, schema_name, table_name
    ),
    oldest AS
    (
        SELECT
            database_name,
            schema_name,
            table_name,
            reserved_mb = SUM(reserved_mb)
        FROM collect.index_object_stats
        WHERE collection_time = (SELECT earliest_time FROM boundaries)
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
    reset_id()
    panels = [
        table(
            "Object Sizes & Growth",
            0,
            0,
            24,
            16,
            _SQL,
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
