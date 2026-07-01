from .._shared import *

_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT TOP (200)
    ios.database_name,
    ios.schema_name,
    ios.table_name,
    index_name = ISNULL(ios.index_name, N'(heap)'),
    classification =
        CASE
            WHEN ios.total_reads = 0 AND ISNULL(ios.user_updates, 0) = 0
            THEN N'Unused'
            WHEN ios.total_reads = 0 AND ISNULL(ios.user_updates, 0) > 0
            THEN N'Write-only'
            ELSE N'Active'
        END,
    ios.index_type_desc,
    reserved_mb = ios.reserved_mb,
    total_rows = ios.total_rows,
    user_seeks = ISNULL(ios.user_seeks, 0),
    user_scans = ISNULL(ios.user_scans, 0),
    user_lookups = ISNULL(ios.user_lookups, 0),
    total_reads = ios.total_reads,
    user_updates = ISNULL(ios.user_updates, 0),
    last_user_access =
    (
        SELECT MAX(v)
        FROM (VALUES
            (ios.last_user_seek),
            (ios.last_user_scan),
            (ios.last_user_lookup),
            (ios.last_user_update)
        ) AS x (v)
    )
FROM collect.index_object_stats AS ios
WHERE ios.collection_time =
(
    SELECT MAX(collection_time)
    FROM collect.index_object_stats
)
ORDER BY
    CASE WHEN ios.total_reads = 0 THEN 0 ELSE 1 END,
    ios.reserved_mb DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def index_usage():
    reset_id()
    panels = [
        table(
            "Index Usage",
            0,
            0,
            24,
            18,
            _SQL,
            overrides=[
                status_colors(
                    "classification",
                    {"Unused": "red", "Write-only": "orange", "Active": "green"},
                )
            ],
            description="Unused / write-only indexes listed first",
        )
    ]
    return finops_dashboard(
        "finops-index-usage",
        "FinOps · Index Usage",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="15m",
    )
