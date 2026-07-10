from .._shared import *

# Upstream ref: GetFinOpsDatabaseResourceUsageAsync (DatabaseService.FinOps.Workload.cs)
_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    workload_stats AS
    (
        SELECT
            database_name = qs.database_name,
            cpu_time_ms =
                SUM(qs.total_worker_time_delta) / 1000,
            logical_reads =
                SUM(qs.total_logical_reads_delta),
            physical_reads =
                SUM(qs.total_physical_reads_delta),
            logical_writes =
                SUM(qs.total_logical_writes_delta),
            execution_count =
                SUM(qs.execution_count_delta)
        FROM collect.query_stats AS qs
        WHERE $__timeFilter(collection_time)
        AND   qs.total_worker_time_delta IS NOT NULL
        GROUP BY
            qs.database_name
    ),
    io_stats AS
    (
        SELECT
            database_name = fio.database_name,
            io_read_bytes =
                SUM(fio.num_of_bytes_read_delta),
            io_write_bytes =
                SUM(fio.num_of_bytes_written_delta),
            io_stall_ms =
                SUM(fio.io_stall_ms_delta)
        FROM collect.file_io_stats AS fio
        WHERE $__timeFilter(collection_time)
        AND   fio.num_of_bytes_read_delta IS NOT NULL
        GROUP BY
            fio.database_name
    ),
    totals AS
    (
        SELECT
            total_cpu_ms =
                NULLIF(SUM(ws.cpu_time_ms), 0),
            total_io_bytes =
                NULLIF
                (
                    SUM(ios.io_read_bytes) +
                    SUM(ios.io_write_bytes),
                    0
                )
        FROM workload_stats AS ws
        FULL JOIN io_stats AS ios
          ON ios.database_name = ws.database_name
    )
SELECT
    database_name =
        COALESCE(ws.database_name, ios.database_name),
    cpu_time_ms =
        ISNULL(ws.cpu_time_ms, 0),
    logical_reads =
        ISNULL(ws.logical_reads, 0),
    physical_reads =
        ISNULL(ws.physical_reads, 0),
    logical_writes =
        ISNULL(ws.logical_writes, 0),
    execution_count =
        ISNULL(ws.execution_count, 0),
    io_read_mb =
        CONVERT(decimal(19,2), ISNULL(ios.io_read_bytes, 0) / 1048576.0),
    io_write_mb =
        CONVERT(decimal(19,2), ISNULL(ios.io_write_bytes, 0) / 1048576.0),
    io_stall_ms =
        ISNULL(ios.io_stall_ms, 0),
    pct_cpu_share =
        CONVERT(decimal(5,2), ISNULL(ws.cpu_time_ms, 0) * 100.0 / t.total_cpu_ms),
    pct_io_share =
        CONVERT
        (
            decimal(5,2),
            (ISNULL(ios.io_read_bytes, 0) + ISNULL(ios.io_write_bytes, 0)) * 100.0 /
              t.total_io_bytes
        )
FROM workload_stats AS ws
FULL JOIN io_stats AS ios
  ON ios.database_name = ws.database_name
CROSS JOIN totals AS t
ORDER BY
    ISNULL(ws.cpu_time_ms, 0) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def database_resources():
    panels = [
        table(
            "Database Resource Usage",
            0,
            0,
            24,
            14,
            _SQL,
            sort_by=[{"displayName": "pct_cpu_share", "desc": True}],
        )
    ]
    return finops_dashboard(
        "finops-database-resources",
        "FinOps · Database Resources",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
