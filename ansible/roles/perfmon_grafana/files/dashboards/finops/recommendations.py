from .._shared import *

_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH
    server_props AS
    (
        SELECT
            edition   = CAST(SERVERPROPERTY(N'Edition') AS nvarchar(128)),
            major_ver = CAST(SERVERPROPERTY(N'ProductMajorVersion') AS int)
    ),
    utilization AS
    (
        SELECT TOP (1)
            avg_cpu_pct,
            max_cpu_pct,
            p95_cpu_pct,
            cpu_count
        FROM report.finops_utilization_efficiency
    ),
    cpu_7d AS
    (
        SELECT DISTINCT
            p95_cpu =
                CONVERT(decimal(5,2),
                    PERCENTILE_CONT(0.95)
                    WITHIN GROUP (ORDER BY sqlserver_cpu_utilization)
                    OVER ())
        FROM collect.cpu_utilization_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
    ),
    cpu_7d_row AS (SELECT TOP (1) p95_cpu FROM cpu_7d),
    mem_latest AS
    (
        SELECT TOP (1)
            total_memory_mb,
            committed_target_memory_mb
        FROM collect.memory_stats
        ORDER BY collection_time DESC
    ),
    db_sizes AS
    (
        SELECT
            database_name,
            total_size_mb = SUM(total_size_mb)
        FROM collect.database_size_stats
        WHERE collection_time =
        (
            SELECT MAX(collection_time) FROM collect.database_size_stats
        )
        GROUP BY database_name
    ),
    db_activity AS
    (
        SELECT
            database_name,
            total_executions = SUM(execution_count_delta)
        FROM collect.query_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        AND   execution_count_delta IS NOT NULL
        GROUP BY database_name
    ),
    idle_dbs AS
    (
        SELECT
            idle_count   = COUNT(*),
            idle_size_mb = SUM(ds.total_size_mb)
        FROM db_sizes AS ds
        LEFT JOIN db_activity AS a ON a.database_name = ds.database_name
        WHERE ISNULL(a.total_executions, 0) = 0
        AND   ds.database_name NOT IN
              (N'master', N'model', N'msdb', N'tempdb', N'PerformanceMonitor')
    ),
    io_by_db AS
    (
        SELECT
            database_name,
            avg_read_ms =
                CASE WHEN SUM(num_of_reads_delta) > 0
                THEN CONVERT(decimal(10,2), SUM(io_stall_read_ms_delta) * 1.0 / SUM(num_of_reads_delta))
                ELSE 0.0 END,
            avg_write_ms =
                CASE WHEN SUM(num_of_writes_delta) > 0
                THEN CONVERT(decimal(10,2), SUM(io_stall_write_ms_delta) * 1.0 / SUM(num_of_writes_delta))
                ELSE 0.0 END
        FROM collect.file_io_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        AND   num_of_reads_delta IS NOT NULL
        GROUP BY database_name
        HAVING SUM(num_of_reads_delta) > 1000
    ),
    low_latency AS
    (
        SELECT
            low_count = COUNT(*),
            db_list   = STRING_AGG(
                            database_name
                            + N' (read ' + CAST(avg_read_ms AS varchar(10))
                            + N'ms, write ' + CAST(avg_write_ms AS varchar(10)) + N'ms)',
                            N'; ')
        FROM io_by_db
        WHERE avg_read_ms < 5 AND avg_write_ms < 3
    ),
    long_jobs AS
    (
        SELECT
            job_name,
            avg_dur_s  = AVG(current_duration_seconds),
            max_dur_s  = MAX(current_duration_seconds),
            avg_hist_s = AVG(avg_duration_seconds),
            times_long = SUM(CAST(is_running_long AS int))
        FROM collect.running_jobs
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
        AND   avg_duration_seconds > 0
        GROUP BY job_name
        HAVING SUM(CAST(is_running_long AS int)) >= 3
    ),
    findings AS
    (
        SELECT
            category   = N'Licensing',
            severity   = N'High',
            confidence = N'Medium',
            finding    = N'Enterprise Edition may not be required',
            detail     = N'Starting with SQL Server 2019, most previously Enterprise-only features '
                       + N'(including TDE, compression, partitioning, and columnstore) are available '
                       + N'in Standard Edition. Review whether remaining Enterprise-only features '
                       + N'(such as Always On availability groups with multiple secondaries) are in use '
                       + N'before considering a downgrade to Standard Edition.'
        FROM server_props
        WHERE edition LIKE N'%Enterprise%'
        AND   major_ver >= 15

        UNION ALL

        SELECT
            N'Compute',
            CASE WHEN u.p95_cpu_pct < 15 THEN N'High' ELSE N'Medium' END,
            N'Medium',
            N'CPU over-provisioned (' + CAST(u.cpu_count AS varchar) + N' cores, P95 = '
                + CAST(u.p95_cpu_pct AS varchar(10)) + N'%)',
            N'P95 CPU utilization is ' + CAST(u.p95_cpu_pct AS varchar(10))
                + N'% (avg ' + CAST(u.avg_cpu_pct AS varchar(10))
                + N'%, max ' + CAST(u.max_cpu_pct AS varchar(10))
                + N'%) across ' + CAST(u.cpu_count AS varchar) + N' cores. Consider reducing to ~'
                + CAST(
                    CASE WHEN CAST(u.cpu_count * u.p95_cpu_pct / 70.0 AS int) > 4
                    THEN CAST(u.cpu_count * u.p95_cpu_pct / 70.0 AS int)
                    ELSE 4 END
                  AS varchar) + N' cores.'
        FROM utilization AS u
        WHERE u.p95_cpu_pct < 30
        AND   u.cpu_count > 4

        UNION ALL

        SELECT
            N'Hardware',
            N'Medium',
            N'Medium',
            N'CPU: reduce from ' + CAST(u.cpu_count AS varchar) + N' to '
                + CAST(
                    CASE WHEN c.p95_cpu < 15
                    THEN CASE WHEN u.cpu_count / 4 < 2 THEN 2 ELSE u.cpu_count / 4 END
                    ELSE CASE WHEN u.cpu_count / 2 < 2 THEN 2 ELSE u.cpu_count / 2 END
                    END AS varchar)
                + N' cores (P95 CPU ' + CAST(c.p95_cpu AS varchar(10)) + N'%)',
            N'Over the last 7 days, P95 CPU utilization was ' + CAST(c.p95_cpu AS varchar(10))
                + N'%. Current allocation of ' + CAST(u.cpu_count AS varchar)
                + N' cores can safely be reduced.'
        FROM cpu_7d_row AS c
        CROSS JOIN utilization AS u
        WHERE c.p95_cpu < 30
        AND   u.cpu_count >= 4
        AND
        (
            CASE WHEN c.p95_cpu < 15
            THEN CASE WHEN u.cpu_count / 4 < 2 THEN 2 ELSE u.cpu_count / 4 END
            ELSE CASE WHEN u.cpu_count / 2 < 2 THEN 2 ELSE u.cpu_count / 2 END
            END
        ) < u.cpu_count

        UNION ALL

        SELECT
            N'Databases',
            CASE WHEN d.idle_count >= 3 THEN N'High' ELSE N'Medium' END,
            N'High',
            CAST(d.idle_count AS varchar) + N' idle database(s) consuming '
                + CAST(CONVERT(decimal(10,1), d.idle_size_mb / 1024.0) AS varchar) + N'GB',
            N'No query activity in the last 7 days. Candidates for archival or removal.'
        FROM idle_dbs AS d
        WHERE d.idle_count > 0

        UNION ALL

        SELECT
            N'Memory',
            N'High',
            N'High',
            N'Memory pressure: buffer pool / target = '
                + CAST(CONVERT(decimal(5,1),
                    m.total_memory_mb * 100.0 /
                    NULLIF(m.committed_target_memory_mb, 0)) AS varchar) + N'%',
            N'SQL Server memory usage exceeds 95% of committed target. '
            + N'Check for memory-intensive queries or increase max server memory.'
        FROM mem_latest AS m
        WHERE m.total_memory_mb > 0
        AND   m.total_memory_mb * 1.0 / NULLIF(m.committed_target_memory_mb, 0) > 0.95

        UNION ALL

        SELECT
            N'Maintenance',
            CASE WHEN times_long >= 5 THEN N'Medium' ELSE N'Low' END,
            N'High',
            job_name + N' ran long ' + CAST(times_long AS varchar) + N' times in 7 days',
            N'Average duration: '
                + CASE WHEN avg_dur_s >= 3600
                       THEN CAST(avg_dur_s / 3600 AS varchar) + N'h '
                          + CAST((avg_dur_s % 3600) / 60 AS varchar) + N'm '
                          + CAST(avg_dur_s % 60 AS varchar) + N's'
                       WHEN avg_dur_s >= 60
                       THEN CAST(avg_dur_s / 60 AS varchar) + N'm '
                          + CAST(avg_dur_s % 60 AS varchar) + N's'
                       ELSE CAST(avg_dur_s AS varchar) + N's' END
                + N', max: '
                + CASE WHEN max_dur_s >= 3600
                       THEN CAST(max_dur_s / 3600 AS varchar) + N'h '
                          + CAST((max_dur_s % 3600) / 60 AS varchar) + N'm '
                          + CAST(max_dur_s % 60 AS varchar) + N's'
                       WHEN max_dur_s >= 60
                       THEN CAST(max_dur_s / 60 AS varchar) + N'm '
                          + CAST(max_dur_s % 60 AS varchar) + N's'
                       ELSE CAST(max_dur_s AS varchar) + N's' END
                + N', historical average: '
                + CASE WHEN avg_hist_s >= 3600
                       THEN CAST(avg_hist_s / 3600 AS varchar) + N'h '
                          + CAST((avg_hist_s % 3600) / 60 AS varchar) + N'm '
                          + CAST(avg_hist_s % 60 AS varchar) + N's'
                       WHEN avg_hist_s >= 60
                       THEN CAST(avg_hist_s / 60 AS varchar) + N'm '
                          + CAST(avg_hist_s % 60 AS varchar) + N's'
                       ELSE CAST(avg_hist_s AS varchar) + N's' END
                + N'. Review whether this job''s schedule or operations need tuning.'
        FROM long_jobs

        UNION ALL

        SELECT
            N'Storage',
            N'Low',
            N'Medium',
            CAST(low_count AS varchar) + N' database(s) with low IO latency - standard storage may suffice',
            N'These databases have avg read latency under 5ms and write under 3ms over 7 days: '
                + db_list + N'. Premium/high-performance storage may not be needed.'
        FROM low_latency
        WHERE low_count > 0
    )
SELECT category, severity, confidence, finding, detail
FROM findings
ORDER BY
    CASE severity WHEN N'High' THEN 1 WHEN N'Medium' THEN 2 ELSE 3 END,
    category
OPTION(MAXDOP 1, RECOMPILE);
"""


def recommendations():
    reset_id()
    panels = [
        table(
            "Cost-Saving Recommendations (last 7 days)",
            0,
            0,
            24,
            14,
            _SQL,
            overrides=[
                status_colors(
                    "severity",
                    {"High": "red", "Medium": "orange", "Low": "blue"},
                )
            ],
            sort_by=[{"displayName": "severity", "desc": False}],
        )
    ]
    return finops_dashboard(
        "finops-recommendations",
        "FinOps · Recommendations",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
