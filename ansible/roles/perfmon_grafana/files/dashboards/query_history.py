from ._shared import *


# Query Stats drill-down - per-collection-time history for a single query_hash.
# Navigated to from the Query Stats tables via data links on the query_hash column.
# Mirrors the upstream QueryStatsHistoryWindow (DatabaseService.QueryPerformance.History.cs
# GetQueryStatsHistoryAsync): shows how CPU, elapsed, I/O, and execution counts evolved
# across collection intervals for the selected query.
def query_history():
    reset_id()
    panels = []

    qh_filter = """\
$__timeFilter(qs.collection_time)
AND ('${database}' = '' OR qs.database_name = '${database}')
AND ('${query_hash}' = '' OR qs.query_hash = CONVERT(binary(8), '${query_hash}', 1))"""

    panels.append(row("CPU & Elapsed Time", 0))
    panels.append(
        timeseries(
            "CPU per collection interval",
            0,
            1,
            12,
            8,
            [target(f"""
SELECT time = qs.collection_time, cpu_ms = SUM(qs.total_worker_time_delta) / 1000.0
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Elapsed time per collection interval",
            12,
            1,
            12,
            8,
            [target(f"""
SELECT time = qs.collection_time, elapsed_ms = SUM(qs.total_elapsed_time_delta) / 1000.0
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
            unit="ms",
        )
    )

    panels.append(row("Average Per Execution", 9))
    panels.append(
        timeseries(
            "Avg CPU & elapsed time per execution",
            0,
            10,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    avg_cpu_ms = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_worker_time) / 1000.0 / SUM(qs.execution_count) END,
    avg_elapsed_ms = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_elapsed_time) / 1000.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Avg logical reads & writes per execution",
            8,
            10,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    avg_logical_reads = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_logical_reads) / 1.0 / SUM(qs.execution_count) END,
    avg_logical_writes = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_logical_writes) / 1.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Avg physical reads & rows per execution",
            16,
            10,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    avg_physical_reads = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_physical_reads) / 1.0 / SUM(qs.execution_count) END,
    avg_rows = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_rows) / 1.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )

    panels.append(row("I/O & Executions", 18))
    panels.append(
        timeseries(
            "Execution count per collection interval",
            0,
            19,
            8,
            8,
            [target(f"""
SELECT time = qs.collection_time, executions = SUM(qs.execution_count_delta)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Logical reads per collection interval",
            8,
            19,
            8,
            8,
            [target(f"""
SELECT time = qs.collection_time, logical_reads = SUM(qs.total_logical_reads_delta)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Physical reads & logical writes per interval",
            16,
            19,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    physical_reads = SUM(qs.total_physical_reads_delta),
    logical_writes = SUM(qs.total_logical_writes_delta)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )

    panels.append(row("Resource Usage", 27))
    panels.append(
        timeseries(
            "Rows returned (min/max per snapshot)",
            0,
            28,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    min_rows = MIN(qs.min_rows),
    max_rows = MAX(qs.max_rows)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Degree of parallelism (min/max per snapshot)",
            8,
            28,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    min_dop = MIN(qs.min_dop),
    max_dop = MAX(qs.max_dop)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Memory grant & spills (min/max per snapshot)",
            16,
            28,
            8,
            8,
            [target(f"""
SELECT
    time = qs.collection_time,
    min_grant_kb = MIN(qs.min_grant_kb),
    max_grant_kb = MAX(qs.max_grant_kb),
    min_spills = MIN(qs.min_spills),
    max_spills = MAX(qs.max_spills)
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
        )
    )

    panels.append(row("Collection History", 36))
    panels.append(
        table(
            "Raw collection snapshots",
            0,
            37,
            24,
            12,
            f"""
SELECT TOP (500)
    collection_id = qs.collection_id,
    qs.collection_time,
    qs.object_type,
    qs.creation_time,
    qs.last_execution_time,
    qs.server_start_time,
    execution_count_delta = qs.execution_count_delta,
    cpu_ms_delta = qs.total_worker_time_delta / 1000,
    elapsed_ms_delta = qs.total_elapsed_time_delta / 1000,
    logical_reads_delta = qs.total_logical_reads_delta,
    physical_reads_delta = qs.total_physical_reads_delta,
    logical_writes_delta = qs.total_logical_writes_delta,
    qs.sample_interval_seconds,
    execution_count = qs.execution_count,
    total_cpu_ms = qs.total_worker_time / 1000,
    min_cpu_ms = CONVERT(decimal(19,2), qs.min_worker_time / 1000.0),
    max_cpu_ms = CONVERT(decimal(19,2), qs.max_worker_time / 1000.0),
    avg_cpu_ms = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_worker_time / 1000.0 / qs.execution_count) END,
    total_elapsed_ms = qs.total_elapsed_time / 1000,
    min_elapsed_ms = CONVERT(decimal(19,2), qs.min_elapsed_time / 1000.0),
    max_elapsed_ms = CONVERT(decimal(19,2), qs.max_elapsed_time / 1000.0),
    avg_elapsed_ms = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_elapsed_time / 1000.0 / qs.execution_count) END,
    qs.total_logical_reads,
    avg_logical_reads = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_logical_reads / 1.0 / qs.execution_count) END,
    qs.total_physical_reads,
    qs.min_physical_reads,
    qs.max_physical_reads,
    avg_physical_reads = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_physical_reads / 1.0 / qs.execution_count) END,
    qs.total_logical_writes,
    avg_logical_writes = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_logical_writes / 1.0 / qs.execution_count) END,
    qs.total_clr_time,
    qs.total_rows,
    qs.min_rows,
    qs.max_rows,
    avg_rows = CASE WHEN qs.execution_count > 0 THEN CONVERT(decimal(19,4), qs.total_rows / 1.0 / qs.execution_count) END,
    qs.min_dop,
    qs.max_dop,
    qs.min_grant_kb,
    qs.max_grant_kb,
    qs.min_used_grant_kb,
    qs.max_used_grant_kb,
    qs.min_ideal_grant_kb,
    qs.max_ideal_grant_kb,
    qs.min_reserved_threads,
    qs.max_reserved_threads,
    qs.min_used_threads,
    qs.max_used_threads,
    qs.total_spills,
    qs.min_spills,
    qs.max_spills,
    sql_handle = CONVERT(varchar(130), qs.sql_handle, 1),
    plan_handle = CONVERT(varchar(130), qs.plan_handle, 1),
    query_hash = CONVERT(varchar(20), qs.query_hash, 1),
    query_plan_hash = CONVERT(varchar(20), qs.query_plan_hash, 1)
FROM collect.query_stats AS qs
WHERE {qh_filter}
ORDER BY qs.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    panels.append(row("Plan XML", 49))
    panels.append(
        table(
            "Execution plan XML (one row per distinct plan shape)",
            0,
            50,
            24,
            12,
            f"""
-- Phase 1: find distinct plan shapes and time ranges without reading the LOB.
-- IX_query_stats_hash_lookup(query_hash, database_name, collection_time) drives the seek;
-- query_plan_hash and collection_id are fetched via key lookup, query_plan_text is not.
WITH plan_ranges AS (
    SELECT
        qs.query_plan_hash,
        plan_first_seen      = MIN(qs.collection_time),
        plan_last_seen       = MAX(qs.collection_time),
        latest_time          = MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN qs.collection_time END),
        latest_collection_id = MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN qs.collection_id  END)
    FROM collect.query_stats AS qs
    WHERE {qh_filter}
    GROUP BY qs.query_plan_hash
    HAVING MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN 1 ELSE 0 END) = 1
)
-- Phase 2: one PK point lookup per distinct plan shape to read the LOB.
-- PK_query_stats is clustered on (collection_time, collection_id) so this is an exact seek.
SELECT
    query_plan_hash = CONVERT(varchar(20), pr.query_plan_hash, 1),
    pr.plan_first_seen,
    pr.plan_last_seen,
    plan_xml = CAST(DECOMPRESS(qs.query_plan_text) AS nvarchar(max))
FROM plan_ranges AS pr
JOIN collect.query_stats AS qs
    ON qs.collection_time = pr.latest_time
    AND qs.collection_id  = pr.latest_collection_id
ORDER BY pr.plan_last_seen DESC;
""",
            description=(
                "One row per distinct execution plan shape seen in the time range. "
                "Correlate query_plan_hash with the Raw collection snapshots table above to see which plan was active at a given collection. "
                "Multiple rows indicate plan instability or PSP. "
                "To export: Inspect -> Data -> Download CSV, save plan_xml as a .sqlplan file, open in SSMS."
            ),
        )
    )

    panels.append(
        table(
            "Query parameters (compiled values, one row per distinct plan shape)",
            0,
            62,
            24,
            8,
            f"""
SET QUOTED_IDENTIFIER ON;
WITH plan_ranges AS (
    SELECT
        qs.query_plan_hash,
        plan_last_seen       = MAX(qs.collection_time),
        latest_time          = MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN qs.collection_time END),
        latest_collection_id = MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN qs.collection_id  END)
    FROM collect.query_stats AS qs
    WHERE {qh_filter}
    GROUP BY qs.query_plan_hash
    HAVING MAX(CASE WHEN qs.query_plan_text IS NOT NULL THEN 1 ELSE 0 END) = 1
),
plan_xml AS (
    SELECT
        pr.query_plan_hash,
        pr.plan_last_seen,
        plan_xml = CONVERT(xml, CAST(DECOMPRESS(qs.query_plan_text) AS nvarchar(max)))
    FROM plan_ranges AS pr
    JOIN collect.query_stats AS qs
        ON qs.collection_time = pr.latest_time
        AND qs.collection_id  = pr.latest_collection_id
)
SELECT
    query_plan_hash = CONVERT(varchar(20), px.query_plan_hash, 1),
    px.plan_last_seen,
    param_name      = p.value('@Column', 'nvarchar(128)'),
    data_type       = p.value('@ParameterDataType', 'nvarchar(128)'),
    compiled_value  = p.value('@ParameterCompiledValue', 'nvarchar(max)')
FROM plan_xml AS px
CROSS APPLY px.plan_xml.nodes('declare namespace sp="http://schemas.microsoft.com/sqlserver/2004/07/showplan"; //sp:ParameterList/sp:ColumnReference') AS t(p)
ORDER BY px.plan_last_seen DESC;
""",
            description=(
                "One row per parameter per distinct plan shape. compiled_value is the value bound when the plan "
                "was compiled, not a per-execution history and only changes on recompile. "
                "Runtime parameter values are not captured historically anywhere upstream (only available live, "
                "for a currently-executing session, via sys.dm_exec_query_statistics_xml) so they do not appear here."
            ),
        )
    )

    return detail_dashboard(
        "perfmon-query-history",
        "PerfMon · Query History",
        panels,
        [
            instance_var(),
            text_var("database", "Database"),
            text_var("query_hash", "Query Hash"),
        ],
        time_from="now-24h",
    )
