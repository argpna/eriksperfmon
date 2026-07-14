from .._shared import *
from ._shared import idle_database_ctes, monthly_cost_params_cte

# Upstream ref: GetFinOpsRecommendationsAsync (DatabaseService.FinOps.Recommendations.cs).
# Deviation: upstream runs each check as a sequential C# method against its own small
# query and appends a recommendation object; this collapses the same check set into one
# SQL query with a findings CTE (one UNION ALL branch per check), so Grafana can render
# it as a single table. Thresholds/formulas below set to match upstream's per-check
# values (CPU/memory rightsizing targets, savings %, idle/dev-test detection).
_SQL = (
    """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

CREATE TABLE #compression_candidates
(
    database_name   sysname,
    schema_name     sysname,
    table_name      sysname,
    index_type_desc nvarchar(60),
    size_mb         decimal(19,2)
);

BEGIN TRY
    DECLARE @compression_sql nvarchar(max) = N'';

    SELECT
        @compression_sql += N'
SELECT
    database_name   = N''' + REPLACE(d.name, N'''', N'''''') + N''',
    schema_name     = s.name,
    table_name      = t.name,
    index_type_desc = i.type_desc,
    size_mb         = SUM(a.total_pages) * 8 / 1024.0
FROM ' + QUOTENAME(d.name) + N'.sys.tables AS t
JOIN ' + QUOTENAME(d.name) + N'.sys.schemas AS s ON t.schema_id = s.schema_id
JOIN ' + QUOTENAME(d.name) + N'.sys.indexes AS i ON t.object_id = i.object_id
JOIN ' + QUOTENAME(d.name) + N'.sys.partitions AS p ON i.object_id = p.object_id AND i.index_id = p.index_id
JOIN ' + QUOTENAME(d.name) + N'.sys.allocation_units AS a ON p.partition_id = a.container_id
WHERE p.data_compression_desc = N''NONE''
AND   t.is_ms_shipped = 0
GROUP BY s.name, t.name, i.type_desc
HAVING SUM(a.total_pages) * 8 / 1024.0 >= 1024
UNION ALL '
    FROM sys.databases AS d
    WHERE d.database_id > 4
    AND   d.state_desc = N'ONLINE'
    AND   d.is_read_only = 0;

    IF @compression_sql <> N''
    BEGIN
        SET @compression_sql = LEFT(@compression_sql, LEN(@compression_sql) - LEN(N'UNION ALL '));
        INSERT INTO #compression_candidates
        EXEC sys.sp_executesql @compression_sql;
    END;
END TRY
BEGIN CATCH
    /* Cross-database metadata access unavailable - leave the candidate list empty rather
    than fail the whole panel. */
    TRUNCATE TABLE #compression_candidates;
END CATCH;

WITH
    """
    + monthly_cost_params_cte()
    + """,
    server_props AS
    (
        SELECT
            edition   = CAST(SERVERPROPERTY(N'Edition') AS nvarchar(128)),
            major_ver = CAST(SERVERPROPERTY(N'ProductMajorVersion') AS int)
    ),
    ag_role AS
    (
        SELECT
            role = CASE
                WHEN EXISTS (SELECT 1 FROM sys.dm_hadr_availability_replica_states WHERE role = 1 AND is_local = 1) THEN N'Primary'
                WHEN EXISTS (SELECT 1 FROM sys.dm_hadr_availability_replica_states WHERE role = 2 AND is_local = 1) THEN N'Secondary'
                ELSE N'Standalone'
            END
    ),
    advanced_ag AS
    (
        SELECT advanced_ag_count = (SELECT COUNT(*) FROM sys.availability_groups WHERE basic_features = 0)
    ),
    ag_caveat AS
    (
        SELECT
            advanced_ag_count = a.advanced_ag_count,
            caveat_text =
                CASE WHEN a.advanced_ag_count > 0
                THEN N' Note: this instance hosts ' + CAST(a.advanced_ag_count AS varchar)
                   + N' Always On Availability Group' + CASE WHEN a.advanced_ag_count = 1 THEN N'' ELSE N's' END
                   + N' using advanced features. Standard Edition supports only Basic Availability Groups, '
                   + N'which are limited to two replicas, a single database per group, and provide no readable '
                   + N'secondary or backups on the secondary. Factor this into any downgrade decision.'
                ELSE N'' END
        FROM advanced_ag AS a
    ),
    /* Deviation: upstream detects TDE pre-2019 via a per-database dynamic-SQL scan of
       sys.dm_db_persisted_sku_features (a persisted "feature was used" flag). We use
       sys.dm_database_encryption_keys.encryption_state = 3 instead (currently-encrypted
       databases) - avoids the cross-database dynamic SQL, same practical detection. */
    tde_dbs AS
    (
        SELECT database_name = db.name
        FROM sys.dm_database_encryption_keys AS dek
        JOIN sys.databases AS db ON db.database_id = dek.database_id
        WHERE dek.encryption_state = 3
    ),
    tde_summary AS
    (
        SELECT
            tde_count = COUNT(*),
            tde_list  = (SELECT STRING_AGG(database_name, N', ') WITHIN GROUP (ORDER BY database_name)
                         FROM (SELECT TOP (20) database_name FROM tde_dbs ORDER BY database_name) AS top20)
        FROM tde_dbs
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
    cpu_rightsizing AS
    (
        SELECT
            u.p95_cpu_pct, u.cpu_count, u.avg_cpu_pct, u.max_cpu_pct,
            target_cores = CASE WHEN CAST(u.cpu_count * u.p95_cpu_pct / 70.0 AS int) > 4
                            THEN CAST(u.cpu_count * u.p95_cpu_pct / 70.0 AS int) ELSE 4 END
        FROM utilization AS u
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
    hw_cpu AS
    (
        SELECT
            c.p95_cpu, u.cpu_count,
            target_cores = CASE WHEN c.p95_cpu < 15
                            THEN CASE WHEN u.cpu_count / 4 < 2 THEN 2 ELSE u.cpu_count / 4 END
                            ELSE CASE WHEN u.cpu_count / 2 < 2 THEN 2 ELSE u.cpu_count / 2 END END
        FROM cpu_7d_row AS c
        CROSS JOIN utilization AS u
        WHERE u.cpu_count >= 4
    ),
    cpu_stability AS
    (
        SELECT
            avg_cpu      = AVG(CAST(sqlserver_cpu_utilization AS decimal(5,2))),
            stddev_cpu   = STDEV(CAST(sqlserver_cpu_utilization AS decimal(5,2))),
            sample_count = COUNT(*)
        FROM collect.cpu_utilization_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
    ),
    mem_p95 AS
    (
        SELECT TOP (1)
            p95_total_memory_mb = PERCENTILE_CONT(0.95)
                WITHIN GROUP (ORDER BY total_memory_mb) OVER (),
            sample_count        = COUNT_BIG(*) OVER (),
            physical_memory_mb  = CAST(SERVERPROPERTY(N'PhysicalMemoryInMB') AS int)
        FROM collect.memory_stats
        WHERE collection_time >= DATEADD(DAY, -7, SYSDATETIME())
    ),
    mem_rightsizing AS
    (
        SELECT
            m.p95_total_memory_mb, m.physical_memory_mb, m.sample_count,
            target_mb = CASE WHEN m.p95_total_memory_mb * 2 > 8192 THEN m.p95_total_memory_mb * 2 ELSE 8192 END
        FROM mem_p95 AS m
    ),
    hw_mem AS
    (
        SELECT
            m.p95_total_memory_mb, m.physical_memory_mb, m.sample_count,
            mem_ratio = CAST(m.p95_total_memory_mb AS decimal(10,4)) / NULLIF(m.physical_memory_mb, 0),
            target_mb =
                CASE
                    WHEN CAST(m.p95_total_memory_mb AS decimal(10,4)) / NULLIF(m.physical_memory_mb, 0) < 0.25
                    THEN CASE WHEN m.physical_memory_mb / 4 < 4096 THEN 4096 ELSE m.physical_memory_mb / 4 END
                    WHEN CAST(m.p95_total_memory_mb AS decimal(10,4)) / NULLIF(m.physical_memory_mb, 0) < 0.40
                    THEN CASE WHEN m.physical_memory_mb / 2 < 4096 THEN 4096 ELSE m.physical_memory_mb / 2 END
                    ELSE 0
                END
        FROM mem_p95 AS m
    ),
    """
    + idle_database_ctes()
    + """,
    total_db_size AS
    (
        SELECT total_mb = SUM(total_size_mb) FROM db_sizes
    ),
    idle_dbs AS
    (
        SELECT
            idle_count   = COUNT(*),
            idle_size_mb = SUM(total_size_mb),
            db_list      =
            (
                SELECT STRING_AGG(database_name, N', ') WITHIN GROUP (ORDER BY total_size_mb DESC)
                FROM (SELECT TOP (5) database_name, total_size_mb FROM idle_dbs_all ORDER BY total_size_mb DESC) AS top5
            )
        FROM idle_dbs_all
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
    devtest_dbs AS
    (
        SELECT database_name = name
        FROM sys.databases
        WHERE (name LIKE N'%dev%' OR name LIKE N'%test%' OR name LIKE N'%staging%' OR name LIKE N'%qa%')
        AND   database_id > 4
    ),
    devtest_summary AS
    (
        SELECT
            dev_count = COUNT(*),
            dev_list  = (SELECT STRING_AGG(database_name, N', ') WITHIN GROUP (ORDER BY database_name)
                         FROM (SELECT TOP (10) database_name FROM devtest_dbs ORDER BY database_name) AS top10)
        FROM devtest_dbs
    ),
    index_cleanup_check AS
    (
        SELECT sp_exists = CASE WHEN OBJECT_ID(N'dbo.sp_IndexCleanup', N'P') IS NOT NULL THEN 1 ELSE 0 END
    ),
    compression_candidates AS
    (
        SELECT * FROM #compression_candidates
    ),
    compression_summary AS
    (
        SELECT
            candidate_count = COUNT(*),
            total_gb        = SUM(size_mb) / 1024.0,
            top_list        =
                (
                    SELECT STRING_AGG(
                        CAST(schema_name + N'.' + table_name + N' (' + CAST(CONVERT(decimal(10,1), size_mb / 1024.0) AS varchar) + N'GB)' AS nvarchar(max)),
                        N'; ') WITHIN GROUP (ORDER BY size_mb DESC)
                    FROM (SELECT TOP (5) schema_name, table_name, size_mb FROM compression_candidates ORDER BY size_mb DESC) AS top5
                )
        FROM compression_candidates
    ),
    findings AS
    (
        SELECT
            category            = N'Licensing',
            severity            = N'Low',
            confidence          = N'High',
            finding             = N'Enterprise Edition - Availability Group secondary replica',
            detail              = N'This instance is currently a secondary replica in an Availability Group. Every replica in an AG '
                                + N'must run the same SQL Server edition, so edition and licensing decisions apply to the whole group '
                                + N'and should be evaluated on the primary replica. A secondary used only for failover may also be '
                                + N'covered by Software Assurance rather than separately licensed.',
            est_monthly_savings = CAST(NULL AS decimal(12,2))
        FROM server_props AS sp
        CROSS JOIN ag_role AS r
        WHERE sp.edition LIKE N'%Enterprise%'
        AND   r.role = N'Secondary'

        UNION ALL

        SELECT
            N'Licensing',
            N'High',
            CASE WHEN c.advanced_ag_count > 0 THEN N'Low' ELSE N'Medium' END,
            N'Enterprise Edition may not be required',
            N'Starting with SQL Server 2019, most previously Enterprise-only features '
              + N'(including TDE, compression, partitioning, and columnstore) are available '
              + N'in Standard Edition. Review whether remaining Enterprise-only features '
              + N'(such as Always On availability groups with multiple secondaries) are in use '
              + N'before considering a downgrade to Standard Edition.' + c.caveat_text,
            CASE WHEN p.monthly_cost > 0 THEN p.monthly_cost * 0.40 ELSE NULL END
        FROM server_props AS sp
        CROSS JOIN ag_role AS r
        CROSS JOIN ag_caveat AS c
        CROSS JOIN params AS p
        WHERE sp.edition LIKE N'%Enterprise%'
        AND   r.role <> N'Secondary'
        AND   sp.major_ver >= 15

        UNION ALL

        SELECT
            N'Licensing',
            N'High',
            CASE WHEN c.advanced_ag_count > 0 THEN N'Medium' ELSE N'High' END,
            CASE WHEN c.advanced_ag_count > 0
                 THEN N'Enterprise Edition - review Availability Group requirements before downgrading'
                 ELSE N'Enterprise Edition with no Enterprise-only features detected' END,
            N'No databases use Transparent Data Encryption (TDE), the only feature '
              + N'still restricted to Enterprise Edition since SQL Server 2016 SP1. '
              + N'Review whether Standard Edition would meet workload requirements for potential license savings.'
              + c.caveat_text,
            CASE WHEN p.monthly_cost > 0 THEN p.monthly_cost * 0.40 ELSE NULL END
        FROM server_props AS sp
        CROSS JOIN ag_role AS r
        CROSS JOIN ag_caveat AS c
        CROSS JOIN tde_summary AS t
        CROSS JOIN params AS p
        WHERE sp.edition LIKE N'%Enterprise%'
        AND   r.role <> N'Secondary'
        AND   sp.major_ver < 15
        AND   ISNULL(t.tde_count, 0) = 0

        UNION ALL

        SELECT
            N'Licensing',
            N'Low',
            N'High',
            N'TDE in use - Enterprise Edition downgrade blocker',
            N'The following databases use Transparent Data Encryption: ' + t.tde_list
              + CASE WHEN t.tde_count > 20 THEN N' and ' + CAST(t.tde_count - 20 AS varchar) + N' more' ELSE N'' END
              + N'. TDE must be removed before downgrading to Standard Edition.',
            CAST(NULL AS decimal(12,2))
        FROM server_props AS sp
        CROSS JOIN ag_role AS r
        CROSS JOIN tde_summary AS t
        WHERE sp.edition LIKE N'%Enterprise%'
        AND   r.role <> N'Secondary'
        AND   sp.major_ver < 15
        AND   ISNULL(t.tde_count, 0) > 0

        UNION ALL

        SELECT
            N'Licensing',
            N'Low',
            N'Low',
            N'Enterprise to Standard would save ~$' + FORMAT(u.cpu_count * 5000.0 / 12, N'N0') + N'/mo at list pricing (' + CAST(u.cpu_count AS varchar) + N' cores)',
            N'Based on list pricing differential of ~$5,000/core/year between Enterprise and Standard. '
              + N'Actual savings depend on your licensing agreement. See Enterprise feature audit for downgrade blockers.',
            CAST(u.cpu_count * 5000.0 / 12 AS decimal(12,2))
        FROM server_props AS sp
        CROSS JOIN ag_role AS r
        CROSS JOIN tde_summary AS t
        CROSS JOIN utilization AS u
        WHERE sp.edition LIKE N'%Enterprise%'
        AND   r.role <> N'Secondary'
        AND   sp.major_ver < 15
        AND   ISNULL(t.tde_count, 0) > 0
        AND   u.cpu_count > 0

        UNION ALL

        SELECT
            N'Compute',
            CASE WHEN c.p95_cpu_pct < 15 THEN N'High' ELSE N'Medium' END,
            N'Medium',
            N'CPU over-provisioned (' + CAST(c.cpu_count AS varchar) + N' cores, P95 = '
                + CAST(c.p95_cpu_pct AS varchar(10)) + N'%)',
            N'P95 CPU utilization is ' + CAST(c.p95_cpu_pct AS varchar(10))
                + N'% (avg ' + CAST(c.avg_cpu_pct AS varchar(10))
                + N'%, max ' + CAST(c.max_cpu_pct AS varchar(10))
                + N'%) across ' + CAST(c.cpu_count AS varchar) + N' cores. Consider reducing to ~'
                + CAST(c.target_cores AS varchar) + N' cores.',
            CASE WHEN p.monthly_cost > 0
                 THEN p.monthly_cost * (1 - CAST(c.target_cores AS decimal(10,4)) / c.cpu_count) * 0.60
                 ELSE NULL END
        FROM cpu_rightsizing AS c
        CROSS JOIN params AS p
        WHERE c.p95_cpu_pct < 30
        AND   c.cpu_count > 4

        UNION ALL

        SELECT
            N'Memory',
            CASE WHEN m.p95_total_memory_mb * 1.0 / NULLIF(m.physical_memory_mb, 0) < 0.30
                 THEN N'High' ELSE N'Medium' END,
            N'Medium',
            N'Memory over-provisioned (P95 SQL memory uses '
                + CAST(CONVERT(decimal(5,1),
                    m.p95_total_memory_mb * 100.0 / NULLIF(m.physical_memory_mb, 0)) AS varchar)
                + N'% of ' + CAST(m.physical_memory_mb / 1024 AS varchar) + N'GB RAM)',
            N'P95 SQL Server memory over 7 days is ' + CAST(m.p95_total_memory_mb AS varchar)
                + N' MB out of ' + CAST(m.physical_memory_mb AS varchar) + N' MB physical RAM ('
                + CAST(CONVERT(decimal(5,1),
                    m.p95_total_memory_mb * 100.0 / NULLIF(m.physical_memory_mb, 0)) AS varchar)
                + N'% utilization). Consider reducing to ~'
                + CAST(m.target_mb / 1024 AS varchar) + N'GB.',
            CASE WHEN p.monthly_cost > 0
                 THEN p.monthly_cost * (1 - CAST(m.target_mb AS decimal(10,4)) / m.physical_memory_mb) * 0.30
                 ELSE NULL END
        FROM mem_rightsizing AS m
        CROSS JOIN params AS p
        WHERE m.physical_memory_mb > 0
        AND   m.sample_count >= 16
        AND   m.p95_total_memory_mb * 1.0 / NULLIF(m.physical_memory_mb, 0) < 0.50
        AND   m.physical_memory_mb > 8192

        UNION ALL

        SELECT
            N'Hardware',
            N'Medium',
            N'Medium',
            N'CPU: reduce from ' + CAST(h.cpu_count AS varchar) + N' to '
                + CAST(h.target_cores AS varchar)
                + N' cores (P95 CPU ' + CAST(h.p95_cpu AS varchar(10)) + N'%)',
            N'Over the last 7 days, P95 CPU utilization was ' + CAST(h.p95_cpu AS varchar(10))
                + N'%. Current allocation of ' + CAST(h.cpu_count AS varchar)
                + N' cores can safely be reduced to ' + CAST(h.target_cores AS varchar) + N' cores.',
            CASE WHEN p.monthly_cost > 0
                 THEN p.monthly_cost * (1 - CAST(h.target_cores AS decimal(10,4)) / h.cpu_count) * 0.50
                 ELSE NULL END
        FROM hw_cpu AS h
        CROSS JOIN params AS p
        WHERE h.p95_cpu < 30
        AND   h.target_cores < h.cpu_count

        UNION ALL

        SELECT
            N'Hardware',
            N'Medium',
            N'Medium',
            N'Memory: reduce from ' + CAST(h.physical_memory_mb / 1024 AS varchar) + N'GB to '
                + CAST(h.target_mb / 1024 AS varchar) + N'GB (P95 SQL memory uses '
                + CAST(CONVERT(decimal(5,1), h.mem_ratio * 100) AS varchar) + N'%)',
            N'P95 SQL Server memory over 7 days is ' + CAST(h.p95_total_memory_mb AS varchar)
                + N' MB of ' + CAST(h.physical_memory_mb AS varchar) + N' MB physical RAM ('
                + CAST(CONVERT(decimal(5,1), h.mem_ratio * 100) AS varchar)
                + N'%). Reducing to ' + CAST(h.target_mb / 1024 AS varchar) + N'GB would still leave headroom.',
            CASE WHEN p.monthly_cost > 0
                 THEN p.monthly_cost * (1 - CAST(h.target_mb AS decimal(10,4)) / h.physical_memory_mb) * 0.30
                 ELSE NULL END
        FROM hw_mem AS h
        CROSS JOIN params AS p
        WHERE h.physical_memory_mb >= 4096
        AND   h.sample_count >= 16
        AND   h.target_mb > 0
        AND   h.target_mb < h.physical_memory_mb

        UNION ALL

        SELECT
            N'Databases',
            CASE WHEN d.idle_count >= 3 THEN N'High' ELSE N'Medium' END,
            N'High',
            CAST(d.idle_count AS varchar) + N' idle database(s) consuming '
                + CAST(CONVERT(decimal(10,1), d.idle_size_mb / 1024.0) AS varchar) + N'GB',
            N'No query activity in 7 days: ' + d.db_list
                + CASE WHEN d.idle_count > 5
                       THEN N' and ' + CAST(d.idle_count - 5 AS varchar) + N' more'
                       ELSE N'' END
                + N'. Consider archiving or removing these databases.',
            CASE WHEN p.monthly_cost > 0 AND t.total_mb > 0
                 THEN p.monthly_cost * (CAST(d.idle_size_mb AS decimal(18,4)) / t.total_mb)
                 ELSE NULL END
        FROM idle_dbs AS d
        CROSS JOIN total_db_size AS t
        CROSS JOIN params AS p
        WHERE d.idle_count > 0

        UNION ALL

        SELECT
            N'Environment',
            N'Medium',
            N'Low',
            CAST(d.dev_count AS varchar) + N' possible dev/test database(s) on production server',
            N'Databases matching dev/test patterns: ' + d.dev_list
                + CASE WHEN d.dev_count > 10 THEN N' and ' + CAST(d.dev_count - 10 AS varchar) + N' more' ELSE N'' END
                + N'. If these are non-production workloads, consider moving to a lower-cost tier or separate server.',
            CAST(NULL AS decimal(12,2))
        FROM devtest_summary AS d
        WHERE d.dev_count > 0

        UNION ALL

        SELECT
            N'Indexes',
            N'Low',
            N'Low',
            N'Index analysis unavailable (sp_IndexCleanup not installed)',
            N'Install sp_IndexCleanup from https://github.com/erikdarlingdata/DarlingData '
                + N'to identify unused and duplicate indexes that waste storage and add write overhead.',
            CAST(NULL AS decimal(12,2))
        FROM index_cleanup_check
        WHERE sp_exists = 0

        UNION ALL

        SELECT
            N'Storage',
            CASE WHEN s.total_gb > 50 THEN N'High' WHEN s.total_gb > 10 THEN N'Medium' ELSE N'Low' END,
            N'High',
            CAST(s.candidate_count AS varchar) + N' uncompressed object(s) >= 1GB ('
                + CAST(CONVERT(decimal(10,1), s.total_gb) AS varchar) + N'GB total)',
            N'Large uncompressed tables/indexes: ' + s.top_list
                + CASE WHEN s.candidate_count > 5 THEN N' and ' + CAST(s.candidate_count - 5 AS varchar) + N' more' ELSE N'' END
                + N'. Consider PAGE or ROW compression to reduce storage and improve I/O.',
            CAST(NULL AS decimal(12,2))
        FROM compression_summary AS s
        WHERE s.candidate_count > 0

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
                + N'. Review whether this job''s schedule or operations need tuning.',
            CAST(NULL AS decimal(12,2))
        FROM long_jobs

        UNION ALL

        SELECT
            N'Storage',
            N'Low',
            N'Medium',
            CAST(low_count AS varchar) + N' database(s) with low IO latency - standard storage may suffice',
            N'These databases have avg read latency under 5ms and write under 3ms over 7 days: '
                + db_list + N'. Premium/high-performance storage may not be needed.',
            CAST(NULL AS decimal(12,2))
        FROM low_latency
        WHERE low_count > 0

        UNION ALL

        SELECT
            N'Cloud',
            N'Low',
            CASE WHEN c.stddev_cpu / NULLIF(c.avg_cpu, 0) < 0.15 THEN N'High' ELSE N'Medium' END,
            N'Stable CPU utilization (avg ' + CAST(CONVERT(decimal(5,1), c.avg_cpu) AS varchar)
                + N'%, CV ' + CAST(CONVERT(decimal(5,2), c.stddev_cpu / NULLIF(c.avg_cpu, 0)) AS varchar)
                + N') - reserved capacity candidate',
            N'CPU utilization is consistently ' + CAST(CONVERT(decimal(5,1), c.avg_cpu) AS varchar)
                + N'% with low variance (+/-' + CAST(CONVERT(decimal(5,1), c.stddev_cpu) AS varchar) + N'%). '
                + N'Reserved pricing typically saves 30-40% over pay-as-you-go for predictable workloads.',
            CAST(NULL AS decimal(12,2))
        FROM cpu_stability AS c
        WHERE c.sample_count >= 24
        AND   c.avg_cpu > 20
        AND   c.stddev_cpu > 0
        AND   c.stddev_cpu / NULLIF(c.avg_cpu, 0) < 0.3
    )
SELECT category, severity, confidence, finding, detail, est_monthly_savings
FROM findings
ORDER BY
    CASE severity WHEN N'High' THEN 1 WHEN N'Medium' THEN 2 ELSE 3 END,
    category
OPTION(MAXDOP 1, RECOMPILE);
"""
)


def recommendations():
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
                ),
                col_unit("est_monthly_savings", "currencyUSD", "Est. Monthly Savings"),
            ],
            sort_by=[{"displayName": "severity", "desc": False}],
        )
    ]
    return finops_dashboard(
        "finops-recommendations",
        "FinOps · Recommendations",
        panels,
        [instance_var(), text_var("monthly_cost", "Monthly Cost (USD)", "0")],
        time_from="now-24h",
        refresh="5m",
    )
