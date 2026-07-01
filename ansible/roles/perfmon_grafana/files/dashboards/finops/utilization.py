from .._shared import *

_HEALTH_SCORE_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    cpu AS
    (
        SELECT DISTINCT
            avg_cpu = AVG(CONVERT(decimal(5,2), sqlserver_cpu_utilization)) OVER (),
            p95_cpu = CONVERT(decimal(5,2),
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY sqlserver_cpu_utilization) OVER ())
        FROM collect.cpu_utilization_stats
        WHERE $__timeFilter(collection_time)
    ),
    mem AS
    (
        SELECT TOP (1)
            memory_ratio =
                CONVERT(decimal(10,4), ms.total_memory_mb) /
                NULLIF(ms.committed_target_memory_mb, 0)
        FROM collect.memory_stats AS ms
        ORDER BY ms.collection_time DESC
    ),
    storage AS
    (
        SELECT
            free_pct =
                AVG(100.0 - dss.used_pct)
        FROM collect.database_size_stats AS dss
        WHERE dss.collection_time =
        (
            SELECT MAX(collection_time) FROM collect.database_size_stats
        )
    )
SELECT
    health_score = CONVERT(int,
        CASE
            WHEN c.p95_cpu > 85 THEN 0
            WHEN c.avg_cpu < 15 THEN 100 - CONVERT(int, c.avg_cpu)
            ELSE 100 - CONVERT(int, c.p95_cpu)
        END * 0.4
        +
        CASE
            WHEN ISNULL(m.memory_ratio, 0) > 0.95 THEN 0
            ELSE (1 - ISNULL(m.memory_ratio, 0)) * 100
        END * 0.3
        +
        ISNULL(s.free_pct, 100) * 0.3
    )
FROM cpu AS c
CROSS JOIN mem AS m
CROSS JOIN storage AS s
OPTION(MAXDOP 1, RECOMPILE);
"""

_PROVISIONING_STATUS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT provisioning_status FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_AVG_CPU_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT avg_cpu_pct FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_P95_CPU_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT p95_cpu_pct FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_MAX_CPU_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT max_cpu_pct FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_STOLEN_MEM_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    stolen_mem_pct =
        CONVERT(decimal(5,1), total_memory_mb * 100.0 / NULLIF(physical_memory_mb, 0))
FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_BUFFER_POOL_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    buffer_pool_pct =
        CONVERT(decimal(5,1),
        (
            SELECT TOP (1) ms.buffer_pool_mb
            FROM collect.memory_stats AS ms
            ORDER BY ms.collection_time DESC
        ) * 100.0 / NULLIF(physical_memory_mb, 0))
FROM report.finops_utilization_efficiency
OPTION(MAXDOP 1, RECOMPILE);
"""

_INFRA_DETAILS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    cpu_count          = fue.cpu_count,
    workers_current    = fue.worker_threads_current,
    workers_max        = fue.worker_threads_max,
    cpu_samples_24h    = fue.cpu_samples,
    physical_memory_mb = fue.physical_memory_mb,
    target_memory_mb   = fue.target_memory_mb,
    total_memory_mb    = ms.total_memory_mb,
    buffer_pool_mb     = ms.buffer_pool_mb
FROM report.finops_utilization_efficiency AS fue
CROSS JOIN
(
    SELECT TOP (1)
        total_memory_mb,
        buffer_pool_mb
    FROM collect.memory_stats
    ORDER BY collection_time DESC
) AS ms
OPTION(MAXDOP 1, RECOMPILE);
"""

_TREND_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    daily_cpu AS
    (
        SELECT DISTINCT
            day = CONVERT(date, cu.collection_time),
            avg_cpu_pct =
                AVG(CONVERT(decimal(5,2), cu.sqlserver_cpu_utilization))
                OVER (PARTITION BY CONVERT(date, cu.collection_time)),
            max_cpu_pct =
                MAX(cu.sqlserver_cpu_utilization)
                OVER (PARTITION BY CONVERT(date, cu.collection_time)),
            p95_cpu_pct =
                CONVERT(decimal(5,2),
                    PERCENTILE_CONT(0.95)
                    WITHIN GROUP (ORDER BY cu.sqlserver_cpu_utilization)
                    OVER (PARTITION BY CONVERT(date, cu.collection_time)))
        FROM collect.cpu_utilization_stats AS cu
        WHERE cu.collection_time >= DATEADD(DAY, -7, SYSDATETIME())
    ),
    daily_mem AS
    (
        SELECT
            day = CONVERT(date, ms.collection_time),
            avg_memory_ratio =
                AVG(CONVERT(decimal(10,4), ms.total_memory_mb) /
                    NULLIF(ms.committed_target_memory_mb, 0))
        FROM collect.memory_stats AS ms
        WHERE ms.collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        GROUP BY CONVERT(date, ms.collection_time)
    )
SELECT
    time = DATEADD(MINUTE, DATEDIFF(MINUTE, GETDATE(), GETUTCDATE()),
                   CONVERT(datetime2, c.day)),
    c.avg_cpu_pct,
    c.p95_cpu_pct,
    c.max_cpu_pct
FROM daily_cpu AS c
LEFT JOIN daily_mem AS m ON m.day = c.day
ORDER BY c.day
OPTION(MAXDOP 1, RECOMPILE);
"""

_TOP_BY_TOTAL_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    workload AS
    (
        SELECT
            database_name,
            cpu_time_ms = SUM(qs.total_worker_time_delta) / 1000,
            execution_count = SUM(qs.execution_count_delta)
        FROM collect.query_stats AS qs
        WHERE $__timeFilter(collection_time)
        AND   qs.total_worker_time_delta IS NOT NULL
        GROUP BY qs.database_name
    ),
    io AS
    (
        SELECT
            database_name,
            io_total_bytes =
                SUM(fio.num_of_bytes_read_delta + fio.num_of_bytes_written_delta)
        FROM collect.file_io_stats AS fio
        WHERE $__timeFilter(collection_time)
        AND   fio.num_of_bytes_read_delta IS NOT NULL
        GROUP BY fio.database_name
    ),
    combined AS
    (
        SELECT
            database_name = COALESCE(w.database_name, i.database_name),
            cpu_time_ms = ISNULL(w.cpu_time_ms, 0),
            execution_count = ISNULL(w.execution_count, 0),
            io_total_mb =
                CONVERT(decimal(19,2), ISNULL(i.io_total_bytes, 0) / 1048576.0)
        FROM workload AS w
        FULL JOIN io AS i ON i.database_name = w.database_name
    ),
    totals AS
    (
        SELECT
            total_cpu = NULLIF(SUM(cpu_time_ms), 0),
            total_io = NULLIF(SUM(io_total_mb), 0)
        FROM combined
    )
SELECT TOP (5)
    c.database_name,
    cpu_time_ms = c.cpu_time_ms,
    pct_cpu = CONVERT(decimal(5,2), c.cpu_time_ms * 100.0 / t.total_cpu),
    pct_io = CONVERT(decimal(5,2), c.io_total_mb * 100.0 / t.total_io),
    execution_count = c.execution_count
FROM combined AS c
CROSS JOIN totals AS t
ORDER BY c.cpu_time_ms DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

_TOP_BY_AVG_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    workload AS
    (
        SELECT
            database_name,
            cpu_time_ms = SUM(qs.total_worker_time_delta) / 1000,
            execution_count = SUM(qs.execution_count_delta)
        FROM collect.query_stats AS qs
        WHERE $__timeFilter(collection_time)
        AND   qs.total_worker_time_delta IS NOT NULL
        GROUP BY qs.database_name
        HAVING SUM(qs.execution_count_delta) > 0
    ),
    io AS
    (
        SELECT
            database_name,
            io_total_mb =
                SUM(fio.num_of_bytes_read_delta + fio.num_of_bytes_written_delta) / 1048576.0
        FROM collect.file_io_stats AS fio
        WHERE $__timeFilter(collection_time)
        AND   fio.num_of_bytes_read_delta IS NOT NULL
        GROUP BY fio.database_name
    )
SELECT TOP (5)
    w.database_name,
    avg_cpu_ms =
        CONVERT(decimal(19,2), w.cpu_time_ms * 1.0 / w.execution_count),
    avg_io_mb =
        CONVERT(decimal(19,4), ISNULL(i.io_total_mb, 0) * 1.0 / w.execution_count),
    execution_count = w.execution_count
FROM workload AS w
LEFT JOIN io AS i ON i.database_name = w.database_name
ORDER BY avg_cpu_ms DESC
OPTION(MAXDOP 1, RECOMPILE);
"""

_DB_SIZES_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT TOP (10)
    dss.database_name,
    used_pct =
        CONVERT(decimal(5,1),
            SUM(dss.used_size_mb) * 100.0 / NULLIF(SUM(dss.total_size_mb), 0)),
    used_mb  = SUM(dss.used_size_mb),
    total_mb = SUM(dss.total_size_mb)
FROM collect.database_size_stats AS dss
WHERE dss.collection_time =
(
    SELECT MAX(collection_time) FROM collect.database_size_stats
)
GROUP BY dss.database_name
ORDER BY SUM(dss.total_size_mb) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def utilization():
    reset_id()
    panels = []

    # Stats row 1: y=0
    panels.append(
        stat(
            "Health Score",
            0,
            0,
            4,
            4,
            _HEALTH_SCORE_SQL,
            "short",
            thresholds(("red", None), ("yellow", 60), ("green", 80)),
        )
    )
    panels.append(
        stat(
            "Provisioning Status",
            4,
            0,
            5,
            4,
            _PROVISIONING_STATUS_SQL,
            "string",
            thresholds(("green", None)),
            mappings={
                "OVER_PROVISIONED": "blue",
                "RIGHT_SIZED": "green",
                "UNDER_PROVISIONED": "red",
            },
            show_values=True,
            fields="/.*/",
        )
    )
    panels.append(
        stat(
            "Avg CPU %",
            9,
            0,
            4,
            4,
            _AVG_CPU_SQL,
            "percent",
            thresholds(("green", None), ("yellow", 70), ("red", 85)),
            decimals=1,
        )
    )
    panels.append(
        stat(
            "P95 CPU %",
            13,
            0,
            4,
            4,
            _P95_CPU_SQL,
            "percent",
            thresholds(("green", None), ("yellow", 70), ("red", 85)),
            decimals=1,
        )
    )
    panels.append(
        stat(
            "Max CPU %",
            17,
            0,
            4,
            4,
            _MAX_CPU_SQL,
            "percent",
            thresholds(("green", None), ("yellow", 70), ("red", 85)),
        )
    )

    # Stats row 2: y=4
    panels.append(
        stat(
            "Stolen Mem %",
            0,
            4,
            4,
            4,
            _STOLEN_MEM_SQL,
            "percent",
            thresholds(("green", None), ("yellow", 70), ("red", 90)),
            decimals=1,
        )
    )
    panels.append(
        stat(
            "Buffer Pool % of Physical RAM",
            4,
            4,
            4,
            4,
            _BUFFER_POOL_SQL,
            "percent",
            thresholds(("green", None), ("yellow", 80), ("red", 95)),
            decimals=1,
        )
    )
    panels.append(
        stat(
            "Infrastructure",
            8,
            4,
            16,
            4,
            _INFRA_DETAILS_SQL,
            "decmbytes",
            thresholds(("green", None)),
            overrides=[
                field_unit("cpu_count", "short"),
                field_unit("workers_current", "short"),
                field_unit("workers_max", "short"),
                field_unit("cpu_samples_24h", "short"),
            ],
        )
    )

    # 7-day trend time series: y=8
    panels.append(
        timeseries(
            "7-Day Provisioning Trend",
            0,
            8,
            24,
            8,
            [target(_TREND_SQL, "time_series")],
            unit="percent",
        )
    )

    # Top consumers tables: y=16
    panels.append(
        table(
            "Top Databases by Total CPU",
            0,
            16,
            12,
            8,
            _TOP_BY_TOTAL_SQL,
            sort_by=[{"displayName": "cpu_time_ms", "desc": True}],
        )
    )
    panels.append(
        table(
            "Top Databases by Avg CPU / Execution",
            12,
            16,
            12,
            8,
            _TOP_BY_AVG_SQL,
            sort_by=[{"displayName": "avg_cpu_ms", "desc": True}],
        )
    )

    # Database size table: y=24
    panels.append(
        table(
            "Database Sizes - Allocated vs Used",
            0,
            24,
            24,
            8,
            _DB_SIZES_SQL,
            overrides=[
                col_gauge_bar("used_pct"),
                field_unit("used_mb", "decmbytes"),
                field_unit("total_mb", "decmbytes"),
            ],
            sort_by=[{"displayName": "total_mb", "desc": True}],
        )
    )

    return finops_dashboard(
        "finops-utilization",
        "FinOps · Utilization",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
