from ._shared import *


# Procedure Stats drill-down - per-collection-time history for a single procedure/trigger/function.
# Navigated to from the Procedure Stats table via data link on the object_name column.
# Mirrors the upstream ProcedureHistoryWindow (DatabaseService.QueryPerformance.History.cs
# GetProcedureStatsHistoryAsync): shows CPU, elapsed, I/O, and execution counts over time
# for the selected object.
def proc_history():
    reset_id()
    panels = []

    ps_filter = """\
$__timeFilter(ps.collection_time)
AND (N'${database}' = N'' OR ps.database_name = N'${database}')
AND (N'${schema_name}' = N'' OR ps.schema_name = N'${schema_name}')
AND (N'${procedure_name}' = N'' OR ps.object_name = N'${procedure_name}')"""

    panels.append(row("CPU & Elapsed Time", 0))
    panels.append(
        timeseries(
            "CPU per collection interval",
            0,
            1,
            12,
            8,
            [target(f"""
SELECT time = ps.collection_time, cpu_ms = ps.total_worker_time_delta / 1000.0
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
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
SELECT time = ps.collection_time, elapsed_ms = ps.total_elapsed_time_delta / 1000.0
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
            unit="ms",
        )
    )

    panels.append(row("I/O & Executions", 9))
    panels.append(
        timeseries(
            "Execution count per collection interval",
            0,
            10,
            8,
            8,
            [target(f"""
SELECT time = ps.collection_time, executions = ps.execution_count_delta
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Logical reads per collection interval",
            8,
            10,
            8,
            8,
            [target(f"""
SELECT time = ps.collection_time, logical_reads = ps.total_logical_reads_delta
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Physical reads & logical writes per interval",
            16,
            10,
            8,
            8,
            [target(f"""
SELECT
    time = ps.collection_time,
    physical_reads = ps.total_physical_reads_delta,
    logical_writes = ps.total_logical_writes_delta
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )

    panels.append(row("Resource Usage", 18))
    panels.append(
        timeseries(
            "Logical reads min/max per snapshot",
            0,
            19,
            8,
            8,
            [target(f"""
SELECT
    time = ps.collection_time,
    min_logical_reads = ps.min_logical_reads,
    max_logical_reads = ps.max_logical_reads
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Physical reads & logical writes min/max",
            8,
            19,
            8,
            8,
            [target(f"""
SELECT
    time = ps.collection_time,
    min_physical_reads = ps.min_physical_reads,
    max_physical_reads = ps.max_physical_reads,
    min_logical_writes = ps.min_logical_writes,
    max_logical_writes = ps.max_logical_writes
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )
    panels.append(
        timeseries(
            "Spills (min/max per snapshot)",
            16,
            19,
            8,
            8,
            [target(f"""
SELECT
    time = ps.collection_time,
    min_spills = ps.min_spills,
    max_spills = ps.max_spills
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
""")],
        )
    )

    panels.append(row("Collection History", 27))
    panels.append(
        table(
            "Raw collection snapshots",
            0,
            28,
            24,
            12,
            f"""
SELECT TOP (500)
    collection_id = ps.collection_id,
    ps.collection_time,
    ps.object_type,
    ps.type_desc,
    ps.cached_time,
    ps.last_execution_time,
    ps.server_start_time,
    execution_count_delta = ps.execution_count_delta,
    cpu_ms_delta = ps.total_worker_time_delta / 1000,
    elapsed_ms_delta = ps.total_elapsed_time_delta / 1000,
    logical_reads_delta = ps.total_logical_reads_delta,
    physical_reads_delta = ps.total_physical_reads_delta,
    logical_writes_delta = ps.total_logical_writes_delta,
    ps.sample_interval_seconds,
    execution_count = ps.execution_count,
    total_cpu_ms = ps.total_worker_time / 1000,
    min_cpu_ms = CONVERT(decimal(19,2), ps.min_worker_time / 1000.0),
    max_cpu_ms = CONVERT(decimal(19,2), ps.max_worker_time / 1000.0),
    total_elapsed_ms = ps.total_elapsed_time / 1000,
    min_elapsed_ms = CONVERT(decimal(19,2), ps.min_elapsed_time / 1000.0),
    max_elapsed_ms = CONVERT(decimal(19,2), ps.max_elapsed_time / 1000.0),
    ps.total_logical_reads,
    ps.min_logical_reads,
    ps.max_logical_reads,
    ps.total_physical_reads,
    ps.min_physical_reads,
    ps.max_physical_reads,
    ps.total_logical_writes,
    ps.min_logical_writes,
    ps.max_logical_writes,
    ps.total_spills,
    ps.min_spills,
    ps.max_spills,
    sql_handle = CONVERT(varchar(130), ps.sql_handle, 1),
    plan_handle = CONVERT(varchar(130), ps.plan_handle, 1)
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    panels.append(row("Plan XML", 40))
    panels.append(
        table(
            "Execution plan XML",
            0,
            41,
            24,
            12,
            f"""
SELECT TOP (1)
    ps.collection_time,
    plan_xml = CAST(DECOMPRESS(ps.query_plan_text) AS nvarchar(max))
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
    AND ps.query_plan_text IS NOT NULL
ORDER BY ps.collection_time DESC;
""",
            description=(
                "Most recent execution plan for the selected procedure within the time range. "
                "To export: click the panel menu (three dots, top-right) -> Inspect -> Data -> Download CSV. "
                "The plan_xml column contains the complete XML showplan. "
                "Save the cell content with a .sqlplan extension and open in SSMS."
            ),
        )
    )

    return detail_dashboard(
        "perfmon-proc-history",
        "PerfMon · Procedure History",
        panels,
        [
            instance_var(),
            text_var("database", "Database"),
            text_var("schema_name", "Schema"),
            text_var("procedure_name", "Procedure"),
        ],
        time_from="now-24h",
    )
