from functools import partial

from .._shared import *
from ._shared import STORAGE_FREE_PCT_CTE, health_score_cases, HEALTH_SCORE_FINAL_SELECT

# Upstream ref: GetServerPropertiesLiveAsync (DatabaseService.FinOps.Inventory.cs) for
# the server-property columns; license_warning below ports the FinOpsServerInventory.
# LicenseWarning computed property (DatabaseService.FinOps.Models.cs). Deviation:
# upstream's Azure SQL DB edition special-case and host_os column are omitted - this
# targets on-prem/IaaS SQL Server instances, not Azure SQL Database.
_SERVER_PROPS_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    server_name        = CONVERT(nvarchar(256), SERVERPROPERTY('ServerName')),
    edition            = CONVERT(nvarchar(256), SERVERPROPERTY('Edition')),
    sql_version        =
        CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion'))
        + N' - '
        + ISNULL(
            CONVERT(nvarchar(128), SERVERPROPERTY('ProductUpdateLevel')),
            CONVERT(nvarchar(128), SERVERPROPERTY('ProductLevel'))
          ),
    cpu_count          = si.cpu_count,
    physical_memory_mb = si.physical_memory_kb / 1024,
    socket_count       = si.socket_count,
    cores_per_socket   = si.cores_per_socket,
    is_hadr_enabled    =
        CASE WHEN CONVERT(int, SERVERPROPERTY('IsHadrEnabled')) = 1 THEN N'Yes' ELSE N'No' END,
    is_clustered       =
        CASE WHEN CONVERT(int, SERVERPROPERTY('IsClustered')) = 1 THEN N'Yes' ELSE N'No' END,
    sqlserver_start_time = {tz_col('si.sqlserver_start_time')},
    storage_total_gb   =
        (
            SELECT CONVERT(decimal(10,2), SUM(CAST(mf.size AS bigint)) * 8.0 / 1024.0 / 1024.0)
            FROM sys.master_files AS mf
        ),
    /* FinOpsServerInventory.LicenseWarning: Standard edition only, flags
      core/RAM counts above Standard's licensing ceiling (24 cores / 128 GB). */
    license_warning    =
        CASE
            WHEN CONVERT(nvarchar(256), SERVERPROPERTY('Edition')) NOT LIKE N'%Standard%' THEN NULL
            ELSE NULLIF(
                lw.cpu_warn
                + CASE WHEN lw.cpu_warn <> N'' AND lw.mem_warn <> N'' THEN N'; ' ELSE N'' END
                + lw.mem_warn,
                N''
            )
        END
FROM sys.dm_os_sys_info AS si
CROSS APPLY
(
    SELECT
        cpu_warn =
            CASE WHEN si.cpu_count > 24
                 THEN N'CPU: ' + CONVERT(nvarchar(20), si.cpu_count) + N' cores (Standard limited to 24)'
                 ELSE N''
            END,
        mem_warn =
            CASE WHEN si.physical_memory_kb / 1024 > 131072
                 THEN N'RAM: ' + CONVERT(nvarchar(20), si.physical_memory_kb / 1024 / 1024) + N'GB (Standard limited to 128GB)'
                 ELSE N''
            END
) AS lw
OPTION(MAXDOP 1);
"""

# No upstream ref: upstream derives UptimeDisplay in C# from SqlServerStartTime
# (FinOpsServerInventory.UptimeDisplay); this recomputes the same hours in SQL.
_UPTIME_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    uptime_hours = DATEDIFF(HOUR, si.sqlserver_start_time, SYSDATETIME())
FROM sys.dm_os_sys_info AS si
OPTION(MAXDOP 1);
"""

# Upstream ref: GetServerMetricsAsync (DatabaseService.FinOps.Inventory.cs)
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

_HEALTH_SQL = f"""
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* Upstream ref: FinOpsHealthCalculator (CpuScore/MemoryScore/StorageScore/Overall,
DatabaseService.FinOps.Models.cs). Deviation: real per-instance P95 CPU, buffer-pool
ratio, and size-weighted free space are used here rather than the WPF Server Inventory
tab's hardcoded fallbacks (AvgCpuPct, memory=80, storage=100). */
WITH
    cpu AS
    (
        SELECT DISTINCT
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
            buffer_pool_ratio =
                CONVERT(decimal(10,4), ms.buffer_pool_mb) /
                NULLIF(ms.total_physical_memory_mb, 0)
        FROM collect.memory_stats AS ms
        ORDER BY ms.collection_time DESC
    ),
    {STORAGE_FREE_PCT_CTE},
    scores AS
    (
        SELECT
{health_score_cases("c.p95_cpu", "m.buffer_pool_ratio", "s.free_pct")}
        FROM cpu AS c
        CROSS JOIN mem AS m
        CROSS JOIN storage AS s
    )
{HEALTH_SCORE_FINAL_SELECT}"""


def server_inventory():
    panels = []
    flow(
        panels,
        0,
        [
            (24, 6, partial(table, "Server Properties", sql=_SERVER_PROPS_SQL)),
            (
                6,
                4,
                partial(
                    stat,
                    "Uptime (hours)",
                    sql=_UPTIME_SQL,
                    unit="h",
                    th=thresholds(("green", None)),
                ),
            ),
            (
                6,
                4,
                partial(
                    stat,
                    "Health Score (last 24h)",
                    sql=_HEALTH_SQL,
                    unit="short",
                    th=thresholds(("red", None), ("yellow", 60), ("green", 80)),
                ),
            ),
            (
                12,
                4,
                partial(
                    table,
                    "Collected Metrics (CPU: last 24h | idle DBs: last 7d)",
                    sql=_METRICS_SQL,
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
            ),
        ],
    )
    return finops_dashboard(
        "finops-server-inventory",
        "FinOps · Server Inventory",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
