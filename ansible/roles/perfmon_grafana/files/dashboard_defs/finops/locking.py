from .._shared import *

# Upstream ref: GetIndexLockingAsync (DatabaseService.FinOps.IndexObjects.cs)
_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    latest AS
    (
        SELECT
            database_name,
            latest_time = MAX(collection_time)
        FROM collect.index_object_stats
        GROUP BY
            database_name
    )
SELECT TOP (200)
    ios.database_name,
    ios.schema_name,
    ios.table_name,
    index_name = ISNULL(ios.index_name, N'(heap)'),
    ios.index_type_desc,
    ios.reserved_mb,
    ios.total_rows,
    row_lock_count = ISNULL(ios.row_lock_count, 0),
    row_lock_wait_count = ISNULL(ios.row_lock_wait_count, 0),
    row_lock_wait_in_ms = ISNULL(ios.row_lock_wait_in_ms, 0),
    page_lock_count = ISNULL(ios.page_lock_count, 0),
    page_lock_wait_count = ISNULL(ios.page_lock_wait_count, 0),
    page_lock_wait_in_ms = ISNULL(ios.page_lock_wait_in_ms, 0),
    index_lock_promotion_count = ISNULL(ios.index_lock_promotion_count, 0),
    page_latch_wait_count = ISNULL(ios.page_latch_wait_count, 0),
    page_latch_wait_in_ms = ISNULL(ios.page_latch_wait_in_ms, 0),
    page_io_latch_wait_count = ISNULL(ios.page_io_latch_wait_count, 0),
    page_io_latch_wait_in_ms = ISNULL(ios.page_io_latch_wait_in_ms, 0)
FROM collect.index_object_stats AS ios
JOIN latest AS l
  ON  l.database_name = ios.database_name
  AND l.latest_time = ios.collection_time
WHERE
(
    ISNULL(ios.row_lock_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_lock_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_latch_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_io_latch_wait_in_ms, 0) > 0
    OR ISNULL(ios.index_lock_promotion_count, 0) > 0
)
AND (${database:sqlstring} = '*' OR ${database:sqlstring} = '' OR ios.database_name = ${database:sqlstring})
AND (${table_name:sqlstring} = '*' OR ${table_name:sqlstring} = '' OR ios.table_name = ${table_name:sqlstring})
ORDER BY
    ISNULL(ios.row_lock_wait_in_ms, 0)
    + ISNULL(ios.page_lock_wait_in_ms, 0)
    + ISNULL(ios.page_latch_wait_in_ms, 0)
    + ISNULL(ios.page_io_latch_wait_in_ms, 0) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def locking():
    panels = [
        table(
            "Locking & Contention",
            0,
            0,
            24,
            16,
            _SQL,
            description="Cumulative since last restart; each database's latest snapshot; top contended objects first",
        )
    ]
    return finops_dashboard(
        "finops-locking",
        "FinOps · Locking & Contention",
        panels,
        [
            instance_var(),
            text_var("database", "Database", "*"),
            text_var("table_name", "Table", "*"),
        ],
        time_from="now-24h",
        refresh="15m",
    )
