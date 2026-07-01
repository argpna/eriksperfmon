from .._shared import *

_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT TOP (200)
    ios.database_name,
    ios.schema_name,
    ios.table_name,
    index_name = ISNULL(ios.index_name, N'(heap)'),
    row_lock_wait_count = ISNULL(ios.row_lock_wait_count, 0),
    row_lock_wait_in_ms = ISNULL(ios.row_lock_wait_in_ms, 0),
    page_lock_wait_in_ms = ISNULL(ios.page_lock_wait_in_ms, 0),
    index_lock_promotion_count = ISNULL(ios.index_lock_promotion_count, 0),
    page_latch_wait_in_ms = ISNULL(ios.page_latch_wait_in_ms, 0),
    page_io_latch_wait_in_ms = ISNULL(ios.page_io_latch_wait_in_ms, 0)
FROM collect.index_object_stats AS ios
WHERE ios.collection_time =
(
    SELECT MAX(collection_time)
    FROM collect.index_object_stats
)
AND
(
    ISNULL(ios.row_lock_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_lock_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_latch_wait_in_ms, 0) > 0
    OR ISNULL(ios.page_io_latch_wait_in_ms, 0) > 0
    OR ISNULL(ios.index_lock_promotion_count, 0) > 0
)
ORDER BY
    ISNULL(ios.row_lock_wait_in_ms, 0)
    + ISNULL(ios.page_lock_wait_in_ms, 0)
    + ISNULL(ios.page_latch_wait_in_ms, 0)
    + ISNULL(ios.page_io_latch_wait_in_ms, 0) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def locking():
    reset_id()
    panels = [
        table(
            "Locking & Contention",
            0,
            0,
            24,
            16,
            _SQL,
            description="Cumulative since last restart; top contended objects first",
        )
    ]
    return finops_dashboard(
        "finops-locking",
        "FinOps · Locking & Contention",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="15m",
    )
