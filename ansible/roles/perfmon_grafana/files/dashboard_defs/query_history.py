from functools import partial

from ._shared import *


# Upstream ref: QueryStatsHistoryWindow (QueryStatsHistoryWindow.xaml.cs), backed by
# GetQueryStatsHistoryAsync (DatabaseService.QueryPerformance.History.cs). Per-collection-time
# history for a single query_hash, navigated to from the Query Stats tables via data links
# on the query_hash column.
# Deviation: upstream charts only per-execution averages (its Metric dropdown), so we
# do the same here rather than adding raw per-interval sums it doesn't plot.
def query_history():
    panels = []

    qh_filter = """\
$__timeFilter(qs.collection_time)
AND (${database:sqlstring} = '*' OR ${database:sqlstring} = '' OR qs.database_name = ${database:sqlstring})
AND (${query_hash:sqlstring} = '*' OR ${query_hash:sqlstring} = '' OR qs.query_hash = CONVERT(binary(8), ${query_hash:sqlstring}, 1))"""

    avg_cpu_elapsed_sql = f"""
SELECT
    time = qs.collection_time,
    avg_cpu_ms = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_worker_time) / 1000.0 / SUM(qs.execution_count) END,
    avg_elapsed_ms = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_elapsed_time) / 1000.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
"""
    avg_logical_sql = f"""
SELECT
    time = qs.collection_time,
    avg_logical_reads = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_logical_reads) / 1.0 / SUM(qs.execution_count) END,
    avg_logical_writes = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_logical_writes) / 1.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
"""
    avg_physical_rows_sql = f"""
SELECT
    time = qs.collection_time,
    avg_physical_reads = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_physical_reads) / 1.0 / SUM(qs.execution_count) END,
    avg_rows = CASE WHEN SUM(qs.execution_count) > 0 THEN SUM(qs.total_rows) / 1.0 / SUM(qs.execution_count) END
FROM collect.query_stats AS qs
WHERE {qh_filter}
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
"""

    # Upstream ref: UpdateChart (QueryStatsHistoryWindow.xaml.cs)
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
                    "Avg physical reads & rows per execution",
                    targets=[target(avg_physical_rows_sql)],
                ),
            ),
        ],
    )

    # Upstream ref: GetQueryStatsHistoryAsync (DatabaseService.QueryPerformance.History.cs)
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
                    # Deviation: upstream GROUPs BY collection_time with MAX/MIN/SUM to collapse
                    # multiple collect.query_stats rows (same query_hash, different
                    # sql_handle/statement offset) into one row per collection; this
                    # is a plain SELECT of the raw rows, so a single collection_time
                    # can appear more than once here where upstream shows one row.
                    "Raw collection snapshots",
                    sql=f"""
SELECT TOP (500)
    collection_id = qs.collection_id,
    collection_time = {tz_col('qs.collection_time')},
    qs.object_type,
    creation_time = {tz_col('qs.creation_time')},
    last_execution_time = {tz_col('qs.last_execution_time')},
    server_start_time = {tz_col('qs.server_start_time')},
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
                ),
            )
        ],
    )

    # Upstream ref: GetQueryStatsPlanXmlByCollectionIdAsync (DatabaseService.QueryPerformance.PlanXml.cs),
    # invoked per-row from ViewPlan_Click (QueryStatsHistoryWindow.xaml.cs) - no upstream tab
    # equivalent, since this drill-down replaces the per-row plan popup with a listing of every
    # distinct plan shape seen in the time range.
    # Phase 1 finds distinct plan shapes/time ranges without reading the LOB -
    # IX_query_stats_hash_lookup(query_hash, database_name, collection_time) drives
    # the seek; query_plan_hash/collection_id are fetched via key lookup, not
    # query_plan_text. Phase 2 is one PK point lookup per plan shape to read the
    # LOB - PK_query_stats is clustered on (collection_time, collection_id), an
    # exact seek. See plan_xml_sql() in _shared.py.
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
                    "Execution plan XML (one row per distinct plan shape)",
                    sql=plan_xml_sql(
                        "collect.query_stats", "qs", "query_plan_hash", 20, qh_filter
                    ),
                    description=(
                        "One row per distinct execution plan shape seen in the time range. "
                        "Correlate query_plan_hash with the Raw collection snapshots table above to see which plan was active at a given collection. "
                        "Multiple rows indicate plan instability or PSP. "
                        "To export: Inspect -> Data -> Download CSV, save plan_xml as a .sqlplan file, open in SSMS."
                    ),
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Query parameters (compiled values, one row per distinct plan shape)",
                    sql=plan_params_sql(
                        "collect.query_stats", "qs", "query_plan_hash", 20, qh_filter
                    ),
                    description=(
                        "One row per parameter per distinct plan shape. compiled_value is the value bound when the plan "
                        "was compiled, not a per-execution history and only changes on recompile. "
                        "Runtime parameter values are not captured historically anywhere upstream (only available live, "
                        "for a currently-executing session, via sys.dm_exec_query_statistics_xml) so they do not appear here."
                    ),
                ),
            ),
        ],
    )

    return detail_dashboard(
        "perfmon-query-history",
        "PerfMon · Query History",
        panels,
        [
            instance_var(),
            text_var("database", "Database", "*"),
            text_var("query_hash", "Query Hash", "*"),
        ],
        time_from="now-24h",
    )
