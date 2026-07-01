from .._shared import *

_SERVER_PROPS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

-- sys.dm_os_sys_info and sys.master_files require VIEW SERVER STATE.
-- Read from collect.server_properties (daily snapshot) and
-- config.server_info_history (captured on each restart) instead.
SELECT TOP (1)
    edition            = sp.edition,
    sql_version        =
        sp.product_version
        + N' - '
        + ISNULL(sp.product_update_level, sp.product_level),
    cpu_count          = sp.cpu_count,
    physical_memory_mb = sp.physical_memory_mb,
    socket_count       = sp.socket_count,
    cores_per_socket   = sp.cores_per_socket,
    is_hadr_enabled    =
        CASE WHEN sp.is_hadr_enabled = 1 THEN N'Yes' ELSE N'No' END,
    is_clustered       =
        CASE WHEN sp.is_clustered = 1 THEN N'Yes' ELSE N'No' END,
    sqlserver_start_time = sih.sqlserver_start_time,
    storage_total_gb   =
        CONVERT(decimal(10,2),
        (
            SELECT SUM(CAST(ds.total_size_mb AS bigint)) / 1024.0
            FROM collect.database_size_stats AS ds
            WHERE ds.collection_time =
            (
                SELECT MAX(ds2.collection_time)
                FROM collect.database_size_stats AS ds2
            )
        ))
FROM collect.server_properties AS sp
CROSS JOIN
(
    SELECT TOP (1)
        sqlserver_start_time
    FROM config.server_info_history
    ORDER BY collection_time DESC
) AS sih
ORDER BY sp.collection_time DESC
OPTION(MAXDOP 1);
"""

_UPTIME_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

-- sys.dm_os_sys_info requires VIEW SERVER STATE; use config.server_info_history instead.
SELECT TOP (1)
    uptime_hours =
        DATEDIFF(HOUR, sih.sqlserver_start_time, SYSDATETIME())
FROM config.server_info_history AS sih
ORDER BY sih.collection_time DESC
OPTION(MAXDOP 1);
"""

_METRICS_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    cpu_24h AS
    (
        SELECT DISTINCT
            avg_cpu_pct =
                AVG(CONVERT(decimal(5,2), cu.sqlserver_cpu_utilization)) OVER (),
            max_cpu_pct = MAX(cu.sqlserver_cpu_utilization) OVER (),
            p95_cpu_pct =
                CONVERT(decimal(5,2),
                    PERCENTILE_CONT(0.95)
                    WITHIN GROUP (ORDER BY cu.sqlserver_cpu_utilization)
                    OVER ())
        FROM collect.cpu_utilization_stats AS cu
        WHERE cu.collection_time >= DATEADD(HOUR, -24, SYSDATETIME())
    ),
    mem_latest AS
    (
        SELECT TOP (1)
            memory_ratio =
                CONVERT(decimal(10,4), ms.total_memory_mb) /
                NULLIF(ms.committed_target_memory_mb, 0)
        FROM collect.memory_stats AS ms
        ORDER BY ms.collection_time DESC
    ),
    storage_total AS
    (
        SELECT
            storage_total_gb =
                SUM(ds.total_size_mb) / 1024.0
        FROM collect.database_size_stats AS ds
        WHERE ds.collection_time =
        (
            SELECT MAX(ds2.collection_time)
            FROM collect.database_size_stats AS ds2
        )
    ),
    idle_dbs AS
    (
        SELECT
            idle_db_count = COUNT(DISTINCT d.database_name)
        FROM
        (
            SELECT DISTINCT ds.database_name
            FROM collect.database_size_stats AS ds
            WHERE ds.collection_time =
            (
                SELECT MAX(ds2.collection_time)
                FROM collect.database_size_stats AS ds2
            )
            AND ds.database_name NOT IN (N'master', N'model', N'msdb', N'tempdb')
            EXCEPT
            SELECT DISTINCT qs.database_name
            FROM collect.query_stats AS qs
            WHERE qs.collection_time >= DATEADD(DAY, -7, SYSDATETIME())
            AND   qs.execution_count_delta > 0
        ) AS d
    )
SELECT
    avg_cpu_pct = c.avg_cpu_pct,
    storage_total_gb = CONVERT(decimal(10,2), st.storage_total_gb),
    idle_db_count = id.idle_db_count,
    provisioning_status =
        CASE
            WHEN c.avg_cpu_pct < 15
            AND  c.max_cpu_pct < 40
            AND  ISNULL(m.memory_ratio, 0) < 0.5
            THEN N'OVER_PROVISIONED'
            WHEN c.p95_cpu_pct > 85
            OR   ISNULL(m.memory_ratio, 0) > 0.95
            THEN N'UNDER_PROVISIONED'
            ELSE N'RIGHT_SIZED'
        END
FROM (SELECT 1 AS x) AS anchor
LEFT JOIN (SELECT TOP (1) avg_cpu_pct, max_cpu_pct, p95_cpu_pct FROM cpu_24h) AS c ON 1 = 1
LEFT JOIN mem_latest AS m ON 1 = 1
LEFT JOIN storage_total AS st ON 1 = 1
LEFT JOIN idle_dbs AS id ON 1 = 1
OPTION(MAXDOP 1, RECOMPILE);
"""

_HEALTH_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    cpu AS
    (
        SELECT DISTINCT
            avg_cpu = AVG(CONVERT(decimal(5,2), sqlserver_cpu_utilization)) OVER (),
            p95_cpu =
                CONVERT(decimal(5,2),
                    PERCENTILE_CONT(0.95)
                    WITHIN GROUP (ORDER BY sqlserver_cpu_utilization)
                    OVER ())
        FROM collect.cpu_utilization_stats
        WHERE collection_time >= DATEADD(HOUR, -24, SYSDATETIME())
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
            free_pct = AVG(100.0 - dss.used_pct)
        FROM collect.database_size_stats AS dss
        WHERE dss.collection_time =
        (
            SELECT MAX(collection_time) FROM collect.database_size_stats
        )
    )
SELECT
    health_score = CONVERT(int,
        CASE WHEN c.p95_cpu > 85 THEN 0
             WHEN c.avg_cpu < 15 THEN 100 - CONVERT(int, c.avg_cpu)
             ELSE 100 - CONVERT(int, c.p95_cpu)
        END * 0.4
        +
        CASE WHEN ISNULL(m.memory_ratio, 0) > 0.95 THEN 0
             ELSE (1 - ISNULL(m.memory_ratio, 0)) * 100
        END * 0.3
        + ISNULL(s.free_pct, 100) * 0.3
    )
FROM cpu AS c
CROSS JOIN mem AS m
CROSS JOIN storage AS s
OPTION(MAXDOP 1, RECOMPILE);
"""


def server_inventory():
    reset_id()
    panels = [
        table(
            "Server Properties",
            0,
            0,
            24,
            6,
            _SERVER_PROPS_SQL,
        ),
        stat(
            "Uptime (hours)",
            0,
            6,
            6,
            4,
            _UPTIME_SQL,
            "h",
            thresholds(("green", None)),
        ),
        stat(
            "Health Score (last 24h)",
            6,
            6,
            6,
            4,
            _HEALTH_SQL,
            "short",
            thresholds(("red", None), ("yellow", 60), ("green", 80)),
        ),
        table(
            "Collected Metrics (CPU: last 24h | idle DBs: last 7d)",
            12,
            6,
            12,
            4,
            _METRICS_SQL,
            overrides=[
                status_colors(
                    "provisioning_status",
                    {
                        "OVER_PROVISIONED": "blue",
                        "RIGHT_SIZED": "green",
                        "UNDER_PROVISIONED": "red",
                    },
                )
            ],
        ),
    ]
    return finops_dashboard(
        "finops-server-inventory",
        "FinOps · Server Inventory",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
