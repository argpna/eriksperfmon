from functools import partial

from ._shared import *


# Upstream ref: ProcedureHistoryWindow (ProcedureHistoryWindow.xaml.cs), backed by
# GetProcedureStatsHistoryAsync (DatabaseService.QueryPerformance.History.cs).
# Per-collection-time history for a single procedure/trigger/function, navigated to from
# the Procedure Stats table via data link on the object_name column. Deviation: upstream
# charts only per-execution averages (its Metric dropdown), so we do the same here
# rather than adding raw per-interval sums it doesn't plot.
def proc_history():
    panels = []

    ps_filter = """\
$__timeFilter(ps.collection_time)
AND (N${database:sqlstring} = N'*' OR N${database:sqlstring} = N'' OR ps.database_name = N${database:sqlstring})
AND (N${schema_name:sqlstring} = N'*' OR N${schema_name:sqlstring} = N'' OR ps.schema_name = N${schema_name:sqlstring})
AND (N${procedure_name:sqlstring} = N'*' OR N${procedure_name:sqlstring} = N'' OR ps.object_name = N${procedure_name:sqlstring})"""

    avg_cpu_elapsed_sql = f"""
SELECT
    time = ps.collection_time,
    avg_cpu_ms = CASE WHEN ps.execution_count > 0 THEN ps.total_worker_time / 1000.0 / ps.execution_count END,
    avg_elapsed_ms = CASE WHEN ps.execution_count > 0 THEN ps.total_elapsed_time / 1000.0 / ps.execution_count END
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
"""
    avg_logical_sql = f"""
SELECT
    time = ps.collection_time,
    avg_logical_reads = CASE WHEN ps.execution_count > 0 THEN ps.total_logical_reads / 1.0 / ps.execution_count END,
    avg_logical_writes = CASE WHEN ps.execution_count > 0 THEN ps.total_logical_writes / 1.0 / ps.execution_count END
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
"""
    avg_physical_spills_sql = f"""
SELECT
    time = ps.collection_time,
    avg_physical_reads = CASE WHEN ps.execution_count > 0 THEN ps.total_physical_reads / 1.0 / ps.execution_count END,
    avg_spills = CASE WHEN ps.execution_count > 0 AND ps.total_spills IS NOT NULL THEN ps.total_spills / 1.0 / ps.execution_count END
FROM collect.procedure_stats AS ps
WHERE {ps_filter}
ORDER BY ps.collection_time;
"""

    # Upstream ref: UpdateChart (ProcedureHistoryWindow.xaml.cs)
    y = subtab(
        panels,
        "Average Per Execution",
        0,
        [
            (
                8,
                8,
                partial(
                    timeseries,
                    "Avg CPU & elapsed time per execution",
                    targets=[target(avg_cpu_elapsed_sql)],
                    unit="ms",
                ),
            ),
            (
                8,
                8,
                partial(
                    timeseries,
                    "Avg logical reads & writes per execution",
                    targets=[target(avg_logical_sql)],
                ),
            ),
            (
                8,
                8,
                partial(
                    timeseries,
                    "Avg physical reads & spills per execution",
                    targets=[target(avg_physical_spills_sql)],
                ),
            ),
        ],
    )

    # Upstream ref: GetProcedureStatsHistoryAsync (DatabaseService.QueryPerformance.History.cs)
    y = subtab(
        panels,
        "Collection History",
        y,
        [
            (
                24,
                12,
                partial(
                    table,
                    "Raw collection snapshots",
                    sql=f"""
SELECT TOP (500)
    collection_id = ps.collection_id,
    collection_time = {tz_col('ps.collection_time')},
    ps.object_type,
    ps.type_desc,
    cached_time = {tz_col('ps.cached_time')},
    last_execution_time = {tz_col('ps.last_execution_time')},
    server_start_time = {tz_col('ps.server_start_time')},
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
                ),
            )
        ],
    )

    # Upstream ref: GetProcedureStatsPlanXmlByCollectionIdAsync (DatabaseService.QueryPerformance.PlanXml.cs),
    # invoked per-row from ViewPlan_Click (ProcedureHistoryWindow.xaml.cs) - no upstream tab
    # equivalent, since this drill-down replaces the per-row plan popup with a listing of
    # every distinct plan shape seen in the time range.
    # Mirrors query_history.py's plan_ranges pattern (see plan_xml_sql() in
    # _shared.py). collect.procedure_stats has no query_plan_hash
    # (sys.dm_exec_procedure_stats doesn't expose one, unlike
    # sys.dm_exec_query_stats) so plan_handle is the distinct-plan-shape key
    # here instead: each recompile gets a new plan_handle.
    subtab(
        panels,
        "Plan XML",
        y,
        [
            (
                24,
                12,
                partial(
                    table,
                    "Execution plan XML (one row per distinct plan_handle)",
                    sql=plan_xml_sql(
                        "collect.procedure_stats", "ps", "plan_handle", 130, ps_filter
                    ),
                    description=(
                        "One row per distinct plan_handle seen in the time range. "
                        "Correlate plan_handle with the Raw collection snapshots table above to see which plan was active at a given collection. "
                        "Multiple rows indicate recompiles - check cached_time in the raw snapshots to see when. "
                        "To export: click the panel menu (three dots, top-right) -> Inspect -> Data -> Download CSV. "
                        "The plan_xml column contains the complete XML showplan. "
                        "Save the cell content with a .sqlplan extension and open in SSMS."
                    ),
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Query parameters (compiled values, one row per distinct plan_handle)",
                    sql=plan_params_sql(
                        "collect.procedure_stats", "ps", "plan_handle", 130, ps_filter
                    ),
                    description=(
                        "One row per parameter per distinct plan_handle seen in the time range. compiled_value is the value "
                        "bound when that plan was compiled, not a per-execution history - it only changes on recompile. "
                        "Runtime parameter values are not captured historically anywhere upstream (only available live, "
                        "for a currently-executing session, via sys.dm_exec_query_statistics_xml) so they cannot appear here."
                    ),
                ),
            ),
        ],
    )

    return detail_dashboard(
        "perfmon-proc-history",
        "PerfMon · Procedure History",
        panels,
        [
            instance_var(),
            text_var("database", "Database", "*"),
            text_var("schema_name", "Schema", "*"),
            text_var("procedure_name", "Procedure", "*"),
        ],
        time_from="now-24h",
    )
