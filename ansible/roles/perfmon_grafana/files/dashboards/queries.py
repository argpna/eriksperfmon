from ._shared import *


# Upstream tab: Queries
# 9 sub-tabs: Performance Trends, Active Queries, Current Active Queries, Query Stats,
# Procedure Stats, Query Store, Query Store Regressions, Query Trace Patterns, Query Heatmap
def queries():
    reset_id()
    topn_var = {
        "name": "topn",
        "label": "Top N",
        "type": "custom",
        "query": "10,25,50,100",
        "current": {"text": "25", "value": "25"},
        "options": [
            {"text": v, "value": v, "selected": v == "25"}
            for v in ["10", "25", "50", "100"]
        ],
        "multi": False,
        "includeAll": False,
        "hide": 0,
    }
    panels = []

    # Upstream sub-tab: Performance Trends
    # Charts: Query CPU, Batch requests/compilations, Query Durations (elapsed rate),
    # Procedure Durations (elapsed rate), Query Store Durations (elapsed rate from QS),
    # Execution Counts (exec/s).
    panels.append(row("Performance Trends", 0))
    panels.append(
        timeseries(
            "Query CPU consumed (all queries)",
            0,
            1,
            12,
            8,
            [target("""
SELECT
    time = qs.collection_time,
    query_cpu_ms = SUM(qs.total_worker_time_delta) / 1000.0
FROM collect.query_stats AS qs
WHERE $__timeFilter(qs.collection_time)
GROUP BY qs.collection_time
ORDER BY qs.collection_time;
""")],
            unit="ms",
        )
    )
    panels.append(
        timeseries(
            "Batch requests & compilations per second",
            12,
            1,
            12,
            8,
            [target("""
SELECT
    time = ps.collection_time,
    metric = RTRIM(ps.counter_name),
    value = ps.cntr_value_delta / 60.0
FROM collect.perfmon_stats AS ps
WHERE $__timeFilter(ps.collection_time)
    AND ps.counter_name IN (N'Batch Requests/sec', N'SQL Compilations/sec', N'SQL Re-Compilations/sec')
ORDER BY ps.collection_time;
""")],
            unit="ops",
        )
    )
    # Query Durations: rate-normalised total elapsed ms across all queries per collection interval.
    # Matches upstream GetQueryDurationTrendsAsync (elapsed_ms_per_second).
    panels.append(
        timeseries(
            "Query durations (elapsed ms/s, all queries)",
            0,
            9,
            6,
            8,
            [target("""
WITH agg AS (
    SELECT
        qs.collection_time,
        total_elapsed_ms = SUM(qs.total_elapsed_time_delta) / 1000.0
    FROM collect.query_stats AS qs
    WHERE $__timeFilter(qs.collection_time)
    GROUP BY qs.collection_time
),
raw AS (
    SELECT
        collection_time,
        total_elapsed_ms,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    elapsed_ms_per_second = total_elapsed_ms / interval_seconds
FROM raw
WHERE interval_seconds > 0
ORDER BY collection_time;
""")],
            unit="ms",
        )
    )
    # Procedure Durations: same metric but from collect.procedure_stats.
    # Matches upstream GetProcedureDurationTrendsAsync.
    panels.append(
        timeseries(
            "Procedure durations (elapsed ms/s, all procedures)",
            6,
            9,
            6,
            8,
            [target("""
WITH agg AS (
    SELECT
        ps.collection_time,
        total_elapsed_ms = SUM(ps.total_elapsed_time_delta) / 1000.0
    FROM collect.procedure_stats AS ps
    WHERE $__timeFilter(ps.collection_time)
    GROUP BY ps.collection_time
),
raw AS (
    SELECT
        collection_time,
        total_elapsed_ms,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    elapsed_ms_per_second = total_elapsed_ms / interval_seconds
FROM raw
WHERE interval_seconds > 0
ORDER BY collection_time;
""")],
            unit="ms",
        )
    )
    # Query Store Durations: rate-normalised from collect.query_store_data.
    # QS has no delta columns; uses avg_duration * count_executions as total work per interval.
    # Matches upstream GetQueryStoreDurationTrendsAsync.
    panels.append(
        timeseries(
            "Query Store durations (elapsed ms/s)",
            12,
            9,
            6,
            8,
            [target("""
WITH agg AS (
    SELECT
        qsd.collection_time,
        total_elapsed_ms = SUM(qsd.avg_duration * qsd.count_executions) / 1000.0
    FROM collect.query_store_data AS qsd
    WHERE $__timeFilter(qsd.collection_time)
    GROUP BY qsd.collection_time
),
raw AS (
    SELECT
        collection_time,
        total_elapsed_ms,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    elapsed_ms_per_second = total_elapsed_ms / interval_seconds
FROM raw
WHERE interval_seconds > 0
ORDER BY collection_time;
""")],
            unit="ms",
        )
    )
    # Execution Counts: executions per second across all queries.
    # Matches upstream GetExecutionTrendsAsync.
    panels.append(
        timeseries(
            "Execution counts (executions/s, all queries)",
            18,
            9,
            6,
            8,
            [target("""
WITH agg AS (
    SELECT
        qs.collection_time,
        total_executions = SUM(qs.execution_count_delta)
    FROM collect.query_stats AS qs
    WHERE $__timeFilter(qs.collection_time)
    GROUP BY qs.collection_time
),
raw AS (
    SELECT
        collection_time,
        total_executions,
        interval_seconds = DATEDIFF(SECOND,
            LAG(collection_time) OVER (ORDER BY collection_time),
            collection_time)
    FROM agg
)
SELECT
    time = collection_time,
    executions_per_second = CAST(CAST(total_executions AS decimal(19,4)) / interval_seconds AS decimal(18,4))
FROM raw
WHERE interval_seconds > 0
ORDER BY collection_time;
""")],
            unit="ops",
        )
    )

    # Upstream sub-tab: Active Queries (historical sp_WhoIsActive snapshots)
    # Note: Current Active Queries (live DMV) is not feasible in Grafana - queries are
    # time-range-bound and cannot show a true live snapshot independent of the time picker.
    panels.append(row("Active Queries", 17))
    panels.append(
        table(
            "Active query snapshots (sp_WhoIsActive)",
            0,
            18,
            24,
            12,
            """
SELECT TOP ($topn)
    qs.collection_time,
    qs.session_id,
    duration = qs.[dd hh:mm:ss.mss],
    qs.status,
    qs.wait_info,
    qs.blocking_session_id,
    qs.blocked_session_count,
    qs.cpu,
    qs.reads,
    qs.writes,
    qs.physical_reads,
    qs.context_switches,
    qs.used_memory,
    qs.tempdb_current,
    qs.tempdb_allocations,
    qs.tran_log_writes,
    qs.open_tran_count,
    qs.percent_complete,
    qs.start_time,
    qs.tran_start_time,
    qs.request_id,
    qs.login_name,
    qs.host_name,
    qs.database_name,
    qs.program_name,
    additional_info = CONVERT(nvarchar(max), qs.additional_info),
    sql_text = CONVERT(nvarchar(500), LEFT(CONVERT(nvarchar(max), qs.sql_text), 500)),
    sql_command = CONVERT(nvarchar(200), LEFT(CONVERT(nvarchar(max), qs.sql_command), 200))
FROM report.query_snapshots AS qs
WHERE $__timeFilter(qs.collection_time)
    AND CONVERT(nvarchar(max), qs.sql_text) NOT LIKE N'WAITFOR%'
ORDER BY qs.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Query Stats
    # Matches upstream GetQueryStatsAsync (DatabaseService.QueryPerformance.Stats.cs):
    # Phase 1 aggregates cumulative MAX(execution_count) per plan lifetime
    # (database_name, query_hash, creation_time). Phase 2 sums across lifetimes grouped
    # by (database_name, query_hash). Time filter uses creation_time/last_execution_time
    # so plans that were active during the selected window are included regardless of
    # whether they survived to the next collection interval. No _delta columns used.
    def _qs_sql(order):
        return f"""
WITH per_lifetime AS (
    SELECT
        qs.database_name,
        qs.query_hash,
        qs.creation_time,
        object_type          = MAX(qs.object_type),
        schema_name          = MAX(qs.schema_name),
        object_name          = MAX(qs.object_name),
        last_execution_time  = MAX(qs.last_execution_time),
        execution_count      = MAX(qs.execution_count),
        total_worker_time    = MAX(qs.total_worker_time),
        min_worker_time      = MIN(qs.min_worker_time),
        max_worker_time      = MAX(qs.max_worker_time),
        total_elapsed_time   = MAX(qs.total_elapsed_time),
        min_elapsed_time     = MIN(qs.min_elapsed_time),
        max_elapsed_time     = MAX(qs.max_elapsed_time),
        total_logical_reads  = MAX(qs.total_logical_reads),
        total_logical_writes = MAX(qs.total_logical_writes),
        total_physical_reads = MAX(qs.total_physical_reads),
        min_physical_reads   = MIN(qs.min_physical_reads),
        max_physical_reads   = MAX(qs.max_physical_reads),
        total_rows           = MAX(qs.total_rows),
        min_rows             = MIN(qs.min_rows),
        max_rows             = MAX(qs.max_rows),
        min_dop              = MIN(qs.min_dop),
        max_dop              = MAX(qs.max_dop),
        min_grant_kb         = MIN(qs.min_grant_kb),
        max_grant_kb         = MAX(qs.max_grant_kb),
        total_spills         = MAX(qs.total_spills),
        min_spills           = MIN(qs.min_spills),
        max_spills           = MAX(qs.max_spills),
        query_plan_hash      = MAX(qs.query_plan_hash),
        sql_handle           = MAX(qs.sql_handle),
        plan_handle          = MAX(qs.plan_handle)
    FROM collect.query_stats AS qs
    WHERE qs.creation_time <= $__timeTo() AND qs.last_execution_time >= $__timeFrom()
    GROUP BY qs.database_name, qs.query_hash, qs.creation_time
)
SELECT TOP ($topn)
    database_name        = pl.database_name,
    query_hash           = CONVERT(nvarchar(20), pl.query_hash, 1),
    object_type          = MAX(pl.object_type),
    object_name          = CASE MAX(pl.object_type)
        WHEN N'STATEMENT' THEN N'Adhoc'
        ELSE QUOTENAME(MAX(pl.schema_name)) + N'.' + QUOTENAME(MAX(pl.object_name))
    END,
    first_execution_time = MIN(pl.creation_time),
    last_execution_time  = MAX(pl.last_execution_time),
    executions           = SUM(pl.execution_count),
    total_cpu_ms         = SUM(pl.total_worker_time) / 1000,
    avg_cpu_ms           = CONVERT(decimal(19,2), SUM(pl.total_worker_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0)),
    min_cpu_ms           = CONVERT(decimal(19,2), MIN(pl.min_worker_time) / 1000.0),
    max_cpu_ms           = CONVERT(decimal(19,2), MAX(pl.max_worker_time) / 1000.0),
    total_elapsed_ms     = SUM(pl.total_elapsed_time) / 1000,
    avg_elapsed_ms       = CONVERT(decimal(19,2), SUM(pl.total_elapsed_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0)),
    min_elapsed_ms       = CONVERT(decimal(19,2), MIN(pl.min_elapsed_time) / 1000.0),
    max_elapsed_ms       = CONVERT(decimal(19,2), MAX(pl.max_elapsed_time) / 1000.0),
    logical_reads        = SUM(pl.total_logical_reads),
    avg_logical_reads    = SUM(pl.total_logical_reads) / NULLIF(SUM(pl.execution_count), 0),
    logical_writes       = SUM(pl.total_logical_writes),
    avg_logical_writes   = SUM(pl.total_logical_writes) / NULLIF(SUM(pl.execution_count), 0),
    physical_reads       = SUM(pl.total_physical_reads),
    avg_physical_reads   = SUM(pl.total_physical_reads) / NULLIF(SUM(pl.execution_count), 0),
    min_physical_reads   = MIN(pl.min_physical_reads),
    max_physical_reads   = MAX(pl.max_physical_reads),
    total_rows           = SUM(pl.total_rows),
    avg_rows             = SUM(pl.total_rows) / NULLIF(SUM(pl.execution_count), 0),
    min_rows             = MIN(pl.min_rows),
    max_rows             = MAX(pl.max_rows),
    min_dop              = MIN(pl.min_dop),
    max_dop              = MAX(pl.max_dop),
    min_grant_kb         = MIN(pl.min_grant_kb),
    max_grant_kb         = MAX(pl.max_grant_kb),
    total_spills         = SUM(pl.total_spills),
    min_spills           = MIN(pl.min_spills),
    max_spills           = MAX(pl.max_spills),
    query_plan_hash      = CONVERT(nvarchar(20), MAX(pl.query_plan_hash), 1),
    sql_handle           = CONVERT(nvarchar(130), MAX(pl.sql_handle), 1),
    plan_handle          = CONVERT(nvarchar(130), MAX(pl.plan_handle), 1),
    query_text           = (
        SELECT TOP (1)
            LEFT(CAST(DECOMPRESS(qs2.query_text) AS nvarchar(max)), 400)
        FROM collect.query_stats AS qs2
        WHERE qs2.query_hash = pl.query_hash
            AND qs2.database_name = pl.database_name
        ORDER BY qs2.collection_time DESC
    )
FROM per_lifetime AS pl
GROUP BY pl.database_name, pl.query_hash
ORDER BY {order} DESC;
"""

    qs_link = col_datalink(
        "query_hash",
        "View query history",
        "/d/perfmon-query-history?${__url_time_range}&var-instance=${instance}"
        "&var-database=${__data.fields.database_name}&var-query_hash=${__data.fields.query_hash}",
    )
    panels.append(row("Query Stats", 30))
    panels.append(
        table(
            "Top queries by CPU",
            0,
            31,
            24,
            10,
            _qs_sql("SUM(pl.total_worker_time)"),
            overrides=[qs_link],
        )
    )
    panels.append(
        table(
            "Top queries by logical reads",
            0,
            41,
            24,
            10,
            _qs_sql("SUM(pl.total_logical_reads)"),
            overrides=[qs_link],
        )
    )
    panels.append(
        table(
            "Parameter sensitivity / plan instability",
            0,
            51,
            24,
            10,
            """
WITH query_plan_variations AS (
    SELECT
        qs.database_name,
        qs.query_hash,
        plan_count              = COUNT_BIG(DISTINCT qs.query_plan_hash),
        execution_count         = SUM(qs.execution_count_delta),
        total_worker_time_ms    = SUM(qs.total_worker_time_delta) / 1000.0,
        min_worker_time_ms      = MIN(qs.min_worker_time) / 1000.0,
        max_worker_time_ms      = MAX(qs.max_worker_time) / 1000.0,
        min_elapsed_time_ms     = MIN(qs.min_elapsed_time) / 1000.0,
        max_elapsed_time_ms     = MAX(qs.max_elapsed_time) / 1000.0,
        sample_query_text       = CAST(DECOMPRESS(MAX(qs.query_text)) AS nvarchar(max)),
        last_execution_time     = MAX(qs.last_execution_time)
    FROM collect.query_stats AS qs
    WHERE $__timeFilter(qs.collection_time)
        AND qs.query_hash IS NOT NULL
        AND qs.execution_count_delta > 0
    GROUP BY qs.database_name, qs.query_hash
    HAVING COUNT_BIG(DISTINCT qs.query_plan_hash) > 1
)
SELECT TOP (50)
    qpv.database_name,
    qpv.plan_count,
    qpv.execution_count,
    total_cpu_ms = CONVERT(decimal(19,2), qpv.total_worker_time_ms),
    worker_time_variance_ratio =
        CASE WHEN qpv.min_worker_time_ms > 0 THEN qpv.max_worker_time_ms / qpv.min_worker_time_ms ELSE 0 END,
    elapsed_time_variance_ratio =
        CASE WHEN qpv.min_elapsed_time_ms > 0 THEN qpv.max_elapsed_time_ms / qpv.min_elapsed_time_ms ELSE 0 END,
    sensitivity_level =
        CASE
            WHEN qpv.plan_count > 5 THEN N'CRITICAL - > 5 plans'
            WHEN qpv.plan_count > 3 AND qpv.max_elapsed_time_ms / NULLIF(qpv.min_elapsed_time_ms, 0) > 10
            THEN N'HIGH - Multiple plans with high variance'
            WHEN qpv.plan_count > 2 THEN N'MEDIUM - Multiple plans'
            ELSE N'LOW'
        END,
    recommendation =
        CASE
            WHEN qpv.plan_count > 3
            THEN N'Consider OPTIMIZE FOR UNKNOWN, plan guides, or Query Store plan forcing'
            WHEN qpv.max_elapsed_time_ms / NULLIF(qpv.min_elapsed_time_ms, 0) > 10
            THEN N'High variance suggests parameter sniffing - review execution plans'
            ELSE N'Monitor for plan stability'
        END,
    sample_query_text       = LEFT(CONVERT(nvarchar(max), qpv.sample_query_text), 300),
    qpv.last_execution_time
FROM query_plan_variations AS qpv
ORDER BY qpv.plan_count DESC, qpv.total_worker_time_ms DESC;
""",
            overrides=[
                status_colors(
                    "sensitivity_level",
                    {
                        "CRITICAL - > 5 plans": "red",
                        "HIGH - Multiple plans with high variance": "red",
                        "MEDIUM - Multiple plans": "orange",
                        "LOW": "green",
                    },
                )
            ],
            sort_by=[{"displayName": "plan_count", "desc": True}],
        )
    )
    panels.append(
        table(
            "Long-running query patterns",
            0,
            61,
            24,
            10,
            """
-- collection_time is the clustered PK leading column of collect.trace_analysis
WITH query_patterns AS (
    SELECT
        ta.database_name,
        query_pattern     = LEFT(ta.sql_text, 200),
        executions        = COUNT_BIG(*),
        avg_duration_ms   = AVG(ta.duration_ms),
        max_duration_ms   = MAX(ta.duration_ms),
        avg_cpu_ms        = AVG(ta.cpu_ms),
        avg_reads         = AVG(ta.reads),
        avg_writes        = AVG(ta.writes),
        sample_query_text = MAX(ta.sql_text),
        last_execution    = MAX(ta.end_time)
    FROM collect.trace_analysis AS ta
    WHERE $__timeFilter(ta.collection_time)
    GROUP BY ta.database_name, LEFT(ta.sql_text, 200)
    HAVING COUNT_BIG(*) > 1
)
SELECT TOP (50)
    qp.database_name,
    qp.query_pattern,
    qp.executions,
    avg_duration_sec  = CONVERT(decimal(19,2), qp.avg_duration_ms / 1000.0),
    max_duration_sec  = CONVERT(decimal(19,2), qp.max_duration_ms / 1000.0),
    avg_cpu_sec       = CONVERT(decimal(19,2), qp.avg_cpu_ms / 1000.0),
    avg_reads         = qp.avg_reads,
    avg_writes        = qp.avg_writes,
    concern_level =
        CASE
            WHEN qp.avg_duration_ms > 60000 THEN N'CRITICAL - Avg > 1 minute'
            WHEN qp.avg_duration_ms > 30000 THEN N'HIGH - Avg > 30 seconds'
            WHEN qp.avg_duration_ms > 10000 THEN N'MEDIUM - Avg > 10 seconds'
            ELSE N'INFO'
        END,
    recommendation =
        CASE
            WHEN qp.avg_reads > 1000000 THEN N'High read count - check for missing indexes, table scans'
            WHEN qp.avg_cpu_ms > qp.avg_duration_ms * 0.8 THEN N'CPU-bound query - check for complex calculations, functions'
            WHEN qp.avg_writes > 100000 THEN N'High write volume - review update/delete patterns'
            ELSE N'Review execution plan for optimization opportunities'
        END,
    sample_query      = LEFT(CONVERT(nvarchar(500), qp.sample_query_text), 300),
    last_execution    = qp.last_execution
FROM query_patterns AS qp
ORDER BY qp.avg_duration_ms DESC;
""",
            overrides=[
                status_colors(
                    "concern_level",
                    {
                        "CRITICAL - Avg > 1 minute": "red",
                        "HIGH - Avg > 30 seconds": "red",
                        "MEDIUM - Avg > 10 seconds": "orange",
                        "INFO": "blue",
                    },
                )
            ],
        )
    )

    # Upstream sub-tab: Procedure Stats
    # Matches upstream GetProcedureStatsAsync (DatabaseService.QueryPerformance.Stats.cs):
    # Phase 1 aggregates per cache lifetime (database_name, schema_name, object_name,
    # cached_time). Phase 2 sums across lifetimes grouped by (database_name, schema_name,
    # object_name). Time filter uses cached_time/last_execution_time. No _delta columns.
    proc_link = col_datalink(
        "object_name",
        "View procedure history",
        "/d/perfmon-proc-history?${__url_time_range}&var-instance=${instance}"
        "&var-database=${__data.fields.database_name}"
        "&var-schema_name=${__data.fields.schema_name}"
        "&var-procedure_name=${__data.fields.procedure_name}",
    )
    panels.append(row("Procedure Stats", 71))
    panels.append(
        table(
            "Top procedures / triggers / functions by CPU",
            0,
            72,
            24,
            10,
            """
WITH per_lifetime AS (
    SELECT
        ps.database_name,
        ps.schema_name,
        ps.object_name,
        ps.cached_time,
        object_type          = MAX(ps.object_type),
        type_desc            = MAX(ps.type_desc),
        object_id            = MAX(ps.object_id),
        last_execution_time  = MAX(ps.last_execution_time),
        execution_count      = MAX(ps.execution_count),
        total_worker_time    = MAX(ps.total_worker_time),
        min_worker_time      = MIN(ps.min_worker_time),
        max_worker_time      = MAX(ps.max_worker_time),
        total_elapsed_time   = MAX(ps.total_elapsed_time),
        min_elapsed_time     = MIN(ps.min_elapsed_time),
        max_elapsed_time     = MAX(ps.max_elapsed_time),
        total_logical_reads  = MAX(ps.total_logical_reads),
        min_logical_reads    = MIN(ps.min_logical_reads),
        max_logical_reads    = MAX(ps.max_logical_reads),
        total_logical_writes = MAX(ps.total_logical_writes),
        min_logical_writes   = MIN(ps.min_logical_writes),
        max_logical_writes   = MAX(ps.max_logical_writes),
        total_physical_reads = MAX(ps.total_physical_reads),
        min_physical_reads   = MIN(ps.min_physical_reads),
        max_physical_reads   = MAX(ps.max_physical_reads),
        total_spills         = MAX(ps.total_spills),
        min_spills           = MIN(ps.min_spills),
        max_spills           = MAX(ps.max_spills),
        sql_handle           = MAX(ps.sql_handle),
        plan_handle          = MAX(ps.plan_handle)
    FROM collect.procedure_stats AS ps
    WHERE ps.cached_time <= $__timeTo() AND ps.last_execution_time >= $__timeFrom()
    GROUP BY ps.database_name, ps.schema_name, ps.object_name, ps.cached_time
)
SELECT TOP ($topn)
    database_name    = pl.database_name,
    object_name      = QUOTENAME(pl.schema_name) + N'.' + QUOTENAME(pl.object_name),
    schema_name      = pl.schema_name,
    procedure_name   = pl.object_name,
    object_type      = MAX(pl.object_type),
    type_desc        = MAX(pl.type_desc),
    first_cached_time = MIN(pl.cached_time),
    last_execution_time = MAX(pl.last_execution_time),
    executions       = SUM(pl.execution_count),
    total_cpu_ms     = SUM(pl.total_worker_time) / 1000,
    avg_cpu_ms       = CONVERT(decimal(19,2), SUM(pl.total_worker_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0)),
    min_cpu_ms       = CONVERT(decimal(19,2), MIN(pl.min_worker_time) / 1000.0),
    max_cpu_ms       = CONVERT(decimal(19,2), MAX(pl.max_worker_time) / 1000.0),
    total_elapsed_ms = SUM(pl.total_elapsed_time) / 1000,
    avg_elapsed_ms   = CONVERT(decimal(19,2), SUM(pl.total_elapsed_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0)),
    min_elapsed_ms   = CONVERT(decimal(19,2), MIN(pl.min_elapsed_time) / 1000.0),
    max_elapsed_ms   = CONVERT(decimal(19,2), MAX(pl.max_elapsed_time) / 1000.0),
    logical_reads    = SUM(pl.total_logical_reads),
    avg_logical_reads = SUM(pl.total_logical_reads) / NULLIF(SUM(pl.execution_count), 0),
    min_logical_reads = MIN(pl.min_logical_reads),
    max_logical_reads = MAX(pl.max_logical_reads),
    logical_writes   = SUM(pl.total_logical_writes),
    avg_logical_writes = SUM(pl.total_logical_writes) / NULLIF(SUM(pl.execution_count), 0),
    min_logical_writes = MIN(pl.min_logical_writes),
    max_logical_writes = MAX(pl.max_logical_writes),
    physical_reads   = SUM(pl.total_physical_reads),
    avg_physical_reads = SUM(pl.total_physical_reads) / NULLIF(SUM(pl.execution_count), 0),
    min_physical_reads = MIN(pl.min_physical_reads),
    max_physical_reads = MAX(pl.max_physical_reads),
    total_spills     = SUM(pl.total_spills),
    avg_spills       = SUM(pl.total_spills) / NULLIF(SUM(pl.execution_count), 0),
    min_spills       = MIN(pl.min_spills),
    max_spills       = MAX(pl.max_spills),
    object_id        = MAX(pl.object_id),
    sql_handle       = CONVERT(varchar(130), MAX(pl.sql_handle), 1),
    plan_handle      = CONVERT(varchar(130), MAX(pl.plan_handle), 1)
FROM per_lifetime AS pl
GROUP BY pl.database_name, pl.schema_name, pl.object_name
ORDER BY SUM(pl.total_worker_time) DESC;
""",
            overrides=[proc_link],
        )
    )

    # Upstream sub-tab: Query Store
    # Matches upstream GetQueryStoreDataAsync (DatabaseService.QueryPerformance.Stats.cs):
    # Groups by (database_name, query_id) - not per plan_id. Plan count exposed as plan_count.
    # Two-phase: CTE selects TOP ($topn) by avg_cpu_time_ms, then OUTER APPLY hydrates
    # query_sql_text via DECOMPRESS for winners only (avoids decompressing every row).
    # query_plan_xml is fetched on-demand in the upstream; omitted from the grid here.
    panels.append(row("Query Store", 82))
    panels.append(
        table(
            "Top queries by avg CPU (Query Store)",
            0,
            83,
            24,
            10,
            """
WITH ranked AS (
    SELECT TOP ($topn)
        qsd.database_name,
        qsd.query_id,
        execution_type_desc   = MAX(qsd.execution_type_desc),
        module_name           = MAX(qsd.module_name),
        first_execution_time  = MIN(qsd.server_first_execution_time),
        last_execution_time   = MAX(qsd.server_last_execution_time),
        execution_count       = SUM(qsd.count_executions),
        plan_count            = COUNT_BIG(DISTINCT qsd.plan_id),
        avg_duration_ms       = CONVERT(decimal(19,2), SUM(CONVERT(float, qsd.avg_duration) * qsd.count_executions) / 1000.0 / NULLIF(SUM(qsd.count_executions), 0)),
        min_duration_ms       = CONVERT(decimal(19,2), MIN(qsd.min_duration) / 1000.0),
        max_duration_ms       = CONVERT(decimal(19,2), MAX(qsd.max_duration) / 1000.0),
        avg_cpu_time_ms       = CONVERT(decimal(19,2), SUM(CONVERT(float, qsd.avg_cpu_time) * qsd.count_executions) / 1000.0 / NULLIF(SUM(qsd.count_executions), 0)),
        min_cpu_time_ms       = CONVERT(decimal(19,2), MIN(qsd.min_cpu_time) / 1000.0),
        max_cpu_time_ms       = CONVERT(decimal(19,2), MAX(qsd.max_cpu_time) / 1000.0),
        avg_logical_reads     = SUM(CONVERT(float, qsd.avg_logical_io_reads) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_logical_reads     = MIN(qsd.min_logical_io_reads),
        max_logical_reads     = MAX(qsd.max_logical_io_reads),
        avg_logical_writes    = SUM(CONVERT(float, qsd.avg_logical_io_writes) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_logical_writes    = MIN(qsd.min_logical_io_writes),
        max_logical_writes    = MAX(qsd.max_logical_io_writes),
        avg_physical_reads    = SUM(CONVERT(float, qsd.avg_physical_io_reads) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_physical_reads    = MIN(qsd.min_physical_io_reads),
        max_physical_reads    = MAX(qsd.max_physical_io_reads),
        min_dop               = MIN(qsd.min_dop),
        max_dop               = MAX(qsd.max_dop),
        avg_memory_pages      = SUM(CONVERT(float, qsd.avg_query_max_used_memory) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_memory_pages      = MIN(qsd.min_query_max_used_memory),
        max_memory_pages      = MAX(qsd.max_query_max_used_memory),
        avg_rowcount          = SUM(CONVERT(float, qsd.avg_rowcount) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_rowcount          = MIN(qsd.min_rowcount),
        max_rowcount          = MAX(qsd.max_rowcount),
        avg_tempdb_pages      = SUM(CONVERT(float, ISNULL(qsd.avg_tempdb_space_used, 0)) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_tempdb_pages      = MIN(qsd.min_tempdb_space_used),
        max_tempdb_pages      = MAX(qsd.max_tempdb_space_used),
        plan_type             = MAX(qsd.plan_type),
        is_forced_plan        = MAX(CONVERT(tinyint, qsd.is_forced_plan)),
        compatibility_level   = MAX(qsd.compatibility_level),
        query_plan_hash       = CONVERT(nvarchar(20), MAX(qsd.query_plan_hash), 1),
        force_failure_count   = SUM(qsd.force_failure_count),
        last_force_failure_reason_desc = MAX(qsd.last_force_failure_reason_desc),
        plan_forcing_type     = MAX(qsd.plan_forcing_type),
        min_clr_time_ms       = CONVERT(decimal(19,2), MIN(qsd.min_clr_time) / 1000.0),
        max_clr_time_ms       = CONVERT(decimal(19,2), MAX(qsd.max_clr_time) / 1000.0),
        min_num_physical_io_reads = MIN(qsd.min_num_physical_io_reads),
        max_num_physical_io_reads = MAX(qsd.max_num_physical_io_reads),
        min_log_bytes_used    = MIN(qsd.min_log_bytes_used),
        max_log_bytes_used    = MAX(qsd.max_log_bytes_used)
    FROM collect.query_store_data AS qsd
    WHERE qsd.server_first_execution_time <= $__timeTo()
        AND qsd.server_last_execution_time >= $__timeFrom()
    GROUP BY qsd.database_name, qsd.query_id
    ORDER BY SUM(CONVERT(float, qsd.avg_cpu_time) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0) DESC
)
SELECT r.*, qt.query_sql_text
FROM ranked AS r
OUTER APPLY (
    SELECT TOP (1)
        query_sql_text = CAST(DECOMPRESS(qsd2.query_sql_text) AS nvarchar(max))
    FROM collect.query_store_data AS qsd2
    WHERE qsd2.database_name = r.database_name
        AND qsd2.query_id = r.query_id
    ORDER BY qsd2.collection_time DESC
) AS qt
ORDER BY r.avg_cpu_time_ms DESC;
""",
        )
    )

    # Upstream sub-tab: Query Store Regressions
    # Matches upstream GetQueryStoreRegressionsAsync (DatabaseService.QueryPerformance.Trends.cs).
    # Uses report.query_store_regressions(@start_date, @end_date) inline TVF which compares
    # a "recent" window ($__timeFrom to $__timeTo) against a baseline window of equal duration
    # immediately before $__timeFrom. Query Store times are UTC, so no tz correction needed.
    panels.append(row("Query Store Regressions", 93))
    panels.append(
        table(
            "Query Store regressions (recent vs baseline window)",
            0,
            94,
            24,
            10,
            """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
SELECT
    qsr.database_name,
    qsr.query_id,
    baseline_duration_ms = CONVERT(decimal(19,2), qsr.baseline_duration_ms),
    recent_duration_ms   = CONVERT(decimal(19,2), qsr.recent_duration_ms),
    qsr.duration_regression_percent,
    baseline_cpu_ms      = CONVERT(decimal(19,2), qsr.baseline_cpu_ms),
    recent_cpu_ms        = CONVERT(decimal(19,2), qsr.recent_cpu_ms),
    qsr.cpu_regression_percent,
    baseline_reads       = CONVERT(decimal(19,2), qsr.baseline_reads),
    recent_reads         = CONVERT(decimal(19,2), qsr.recent_reads),
    qsr.io_regression_percent,
    additional_duration_ms = CONVERT(decimal(19,2), qsr.additional_duration_ms),
    qsr.baseline_exec_count,
    qsr.recent_exec_count,
    qsr.baseline_plan_count,
    qsr.recent_plan_count,
    qsr.severity,
    query_text_sample    = LEFT(qsr.query_text_sample, 300),
    qsr.last_execution_time
FROM report.query_store_regressions($__timeFrom(), $__timeTo()) AS qsr
ORDER BY qsr.additional_duration_ms DESC;
""",
            overrides=[
                status_colors(
                    "severity",
                    {
                        "CRITICAL": "red",
                        "HIGH": "orange",
                        "MEDIUM": "yellow",
                        "LOW": "green",
                    },
                )
            ],
            description="Compares the selected time range (recent window) against an equal-length "
            "baseline window immediately before it. Ranks by additional_duration_ms (regression "
            "magnitude * execution count). Only queries with >25% CPU regression are included.",
        )
    )

    # Upstream sub-tab: Query Trace Patterns
    # Matches upstream GetLongRunningQueryPatternsAsync (DatabaseService.QueryPerformance.Trends.cs).
    # Queries collect.trace_analysis directly with time filter on end_time, not the
    # report.long_running_query_patterns view, which has no time filter and aggregates all history.
    panels.append(row("Query Trace Patterns", 104))
    panels.append(
        table(
            "Long-running query patterns (SQL Trace)",
            0,
            105,
            24,
            10,
            """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
WITH query_patterns AS (
    SELECT
        ta.database_name,
        query_pattern  = LEFT(ta.sql_text, 200),
        executions     = COUNT_BIG(*),
        avg_duration_ms = AVG(ta.duration_ms),
        max_duration_ms = MAX(ta.duration_ms),
        avg_cpu_ms     = AVG(ta.cpu_ms),
        avg_reads      = AVG(ta.reads),
        avg_writes     = AVG(ta.writes),
        last_execution = MAX(ta.end_time)
    FROM collect.trace_analysis AS ta
    WHERE ta.collection_time >= DATEADD(MINUTE, -10, CONVERT(datetime2, $__timeFrom()))
        AND ta.collection_time <= DATEADD(MINUTE, 10, CONVERT(datetime2, $__timeTo()))
        AND $__timeFilter(ta.end_time)
    GROUP BY ta.database_name, LEFT(ta.sql_text, 200)
)
SELECT TOP (50)
    qp.database_name,
    qp.executions,
    avg_duration_sec = CONVERT(decimal(19,2), qp.avg_duration_ms / 1000.0),
    max_duration_sec = CONVERT(decimal(19,2), qp.max_duration_ms / 1000.0),
    avg_cpu_sec      = CONVERT(decimal(19,2), qp.avg_cpu_ms / 1000.0),
    qp.avg_reads,
    qp.avg_writes,
    concern_level = CASE
        WHEN qp.avg_duration_ms > 60000 THEN N'CRITICAL - Avg > 1 minute'
        WHEN qp.avg_duration_ms > 30000 THEN N'HIGH - Avg > 30 seconds'
        WHEN qp.avg_duration_ms > 10000 THEN N'MEDIUM - Avg > 10 seconds'
        ELSE N'INFO'
    END,
    recommendation = CASE
        WHEN qp.avg_reads > 1000000 THEN N'High read count - check for missing indexes, table scans'
        WHEN qp.avg_cpu_ms > qp.avg_duration_ms * 0.8 THEN N'CPU-bound query - check for complex calculations, functions'
        WHEN qp.avg_writes > 100000 THEN N'High write volume - review update/delete patterns'
        ELSE N'Review execution plan for optimization opportunities'
    END,
    qp.last_execution,
    qp.query_pattern
FROM query_patterns AS qp
WHERE qp.executions > 1
ORDER BY qp.avg_duration_ms DESC;
""",
            overrides=[
                status_colors(
                    "concern_level",
                    {
                        "CRITICAL - Avg > 1 minute": "red",
                        "HIGH - Avg > 30 seconds": "orange",
                        "MEDIUM - Avg > 10 seconds": "yellow",
                        "INFO": "blue",
                    },
                )
            ],
            description="Queries from SQL Trace (collect.trace_analysis) grouped by the first 200 "
            "characters of sql_text. Requires the trace_analysis_collector to be enabled and "
            "collecting trace files. Zero rows means no trace data exists in the selected time range.",
        )
    )

    # Upstream sub-tab: Query Heatmap
    # Matches upstream GetQueryHeatmapAsync (DatabaseService.QueryPerformance.cs).
    # Bins queries from collect.query_stats into 7 magnitude buckets (powers of 10: 0-1, 1-10,
    # 10-100, 100-1K, 1K-10K, 10K-100K, >100K) per 5-minute time window. The $heatmap_metric
    # variable selects the per-execution metric: Duration/CPU in ms, Reads/Writes in pages,
    # Execution Count as raw count. Bucket labels reflect the selected metric unit.
    # Returns time-series rows (time, metric=bucket_label, value=query_count) so Grafana's
    # native Heatmap panel renders one Y-row per bucket, coloured by query concentration.
    # Grafana limitation: upstream tooltip shows the top query text for each cell (from
    # DECOMPRESS(query_text) with per-bucket MAX(execution_count_delta)), Grafana's heatmap
    # panel cannot embed extra data fields in hover tooltips.
    heatmap_sql = """
WITH metric_vals AS (
    SELECT
        time_bin     = DATEADD(MINUTE, DATEDIFF(MINUTE, 0, qs.collection_time) / 5 * 5, 0),
        metric_value = CASE N'${heatmap_metric}'
            WHEN N'Duration'       THEN (qs.total_elapsed_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'CPU'            THEN (qs.total_worker_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Logical Reads'  THEN CAST(qs.total_logical_reads_delta AS float) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Logical Writes' THEN CAST(qs.total_logical_writes_delta AS float) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Execution Count' THEN CAST(qs.execution_count_delta AS float)
            ELSE (qs.total_elapsed_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
        END
    FROM collect.query_stats AS qs WITH (NOLOCK)
    WHERE $__timeFilter(qs.collection_time) AND qs.execution_count_delta > 0
),
bucketed AS (
    SELECT
        time_bin,
        bucket_index = CASE
            WHEN metric_value < 1      THEN 0
            WHEN metric_value < 10     THEN 1
            WHEN metric_value < 100    THEN 2
            WHEN metric_value < 1000   THEN 3
            WHEN metric_value < 10000  THEN 4
            WHEN metric_value < 100000 THEN 5
            ELSE 6
        END,
        query_count = COUNT(*)
    FROM metric_vals
    WHERE metric_value IS NOT NULL
    GROUP BY
        time_bin,
        CASE
            WHEN metric_value < 1      THEN 0
            WHEN metric_value < 10     THEN 1
            WHEN metric_value < 100    THEN 2
            WHEN metric_value < 1000   THEN 3
            WHEN metric_value < 10000  THEN 4
            WHEN metric_value < 100000 THEN 5
            ELSE 6
        END
)
SELECT
    time   = b.time_bin,
    metric = CASE b.bucket_index
        WHEN 0 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'0: 0-1ms'     ELSE N'0: 0-1'       END
        WHEN 1 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'1: 1-10ms'    ELSE N'1: 1-10'     END
        WHEN 2 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'2: 10-100ms'  ELSE N'2: 10-100'   END
        WHEN 3 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'3: 100ms-1s'  ELSE N'3: 100-1K'   END
        WHEN 4 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'4: 1-10s'     ELSE N'4: 1K-10K'   END
        WHEN 5 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'5: 10-100s'   ELSE N'5: 10K-100K' END
        ELSE        CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'6: >100s'     ELSE N'6: >100K'    END
    END,
    value  = CAST(b.query_count AS float)
FROM bucketed AS b
UNION ALL
SELECT
    time   = DATEADD(MINUTE, DATEDIFF(MINUTE, 0, CONVERT(datetime2, $__timeFrom(), 127)) / 5 * 5, 0),
    metric = CASE ab.n
        WHEN 0 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'0: 0-1ms'     ELSE N'0: 0-1'       END
        WHEN 1 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'1: 1-10ms'    ELSE N'1: 1-10'     END
        WHEN 2 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'2: 10-100ms'  ELSE N'2: 10-100'   END
        WHEN 3 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'3: 100ms-1s'  ELSE N'3: 100-1K'   END
        WHEN 4 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'4: 1-10s'     ELSE N'4: 1K-10K'   END
        WHEN 5 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'5: 10-100s'   ELSE N'5: 10K-100K' END
        ELSE        CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'6: >100s'     ELSE N'6: >100K'    END
    END,
    value  = 0.0
FROM (VALUES (0),(1),(2),(3),(4),(5),(6)) AS ab(n)
ORDER BY 1, 2;
"""
    # Companion table for the heatmap: shows per bucket the total query count and the highest
    # total-impact query (SUM(metric_value * exec_count)) across the selected time range.
    # Performance: the main scan groups by query_hash (8-byte binary) to avoid DECOMPRESS on
    # every row, OUTER APPLY decompresses only the 1-7 winning rows at the end.
    # Ordered highest bucket first to align with the heatmap Y-axis.
    heatmap_companion_sql = """
WITH metric_vals AS (
    SELECT
        metric_value = CASE N'${heatmap_metric}'
            WHEN N'Duration'       THEN (qs.total_elapsed_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'CPU'            THEN (qs.total_worker_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Logical Reads'  THEN CAST(qs.total_logical_reads_delta AS float) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Logical Writes' THEN CAST(qs.total_logical_writes_delta AS float) / NULLIF(qs.execution_count_delta, 0)
            WHEN N'Execution Count' THEN CAST(qs.execution_count_delta AS float)
            ELSE (qs.total_elapsed_time_delta / 1000.0) / NULLIF(qs.execution_count_delta, 0)
        END,
        qs.query_hash,
        exec_count = qs.execution_count_delta
    FROM collect.query_stats AS qs WITH (NOLOCK)
    WHERE $__timeFilter(qs.collection_time) AND qs.execution_count_delta > 0
),
bucketed AS (
    SELECT
        bucket_index = CASE
            WHEN metric_value < 1      THEN 0
            WHEN metric_value < 10     THEN 1
            WHEN metric_value < 100    THEN 2
            WHEN metric_value < 1000   THEN 3
            WHEN metric_value < 10000  THEN 4
            WHEN metric_value < 100000 THEN 5
            ELSE 6
        END,
        query_hash,
        metric_value,
        exec_count
    FROM metric_vals
    WHERE metric_value IS NOT NULL
),
per_query AS (
    SELECT
        bucket_index,
        query_hash,
        total_impact = SUM(metric_value * exec_count),
        query_count  = COUNT(*)
    FROM bucketed
    GROUP BY bucket_index, query_hash
),
ranked AS (
    SELECT
        bucket_index,
        query_hash,
        rn = ROW_NUMBER() OVER (
            PARTITION BY bucket_index
            ORDER BY total_impact DESC, CONVERT(varchar(20), query_hash, 1)
        )
    FROM per_query
),
bucket_total AS (
    SELECT bucket_index, total_queries = SUM(query_count)
    FROM per_query
    GROUP BY bucket_index
)
SELECT
    bucket = CASE bt.bucket_index
        WHEN 0 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'0-1ms'    ELSE N'0-1'       END
        WHEN 1 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'1-10ms'   ELSE N'1-10'      END
        WHEN 2 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'10-100ms' ELSE N'10-100'    END
        WHEN 3 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'100ms-1s' ELSE N'100-1K'    END
        WHEN 4 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'1-10s'    ELSE N'1K-10K'   END
        WHEN 5 THEN CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'10-100s'  ELSE N'10K-100K' END
        ELSE        CASE WHEN N'${heatmap_metric}' IN (N'Duration', N'CPU') THEN N'>100s'    ELSE N'>100K'    END
    END,
    total_queries = bt.total_queries,
    top_query     = qt.query_preview
FROM bucket_total AS bt
JOIN ranked AS r ON r.bucket_index = bt.bucket_index AND r.rn = 1
OUTER APPLY (
    SELECT TOP (1)
        query_preview = LEFT(CAST(DECOMPRESS(qs2.query_text) AS nvarchar(max)), 300)
    FROM collect.query_stats AS qs2 WITH (NOLOCK)
    WHERE qs2.query_hash = r.query_hash
        AND $__timeFilter(qs2.collection_time)
        AND qs2.execution_count_delta > 0
) AS qt
ORDER BY bt.bucket_index DESC;
"""

    panels.append(row("Query Heatmap", 115))
    panels.append(
        {
            "id": nid(),
            "type": "heatmap",
            "title": "Query heatmap (${heatmap_metric} distribution over time)",
            "datasource": DS,
            "description": "Each cell = number of query executions in that metric bucket for the "
            "5-minute window. Duration and CPU buckets are in ms; Reads and Writes in pages. "
            "Brighter/yellower = more queries. Y-axis is ordered 0 (fastest/smallest) to 6 (slowest/largest). "
            "The companion table to the right shows the top query per bucket across the full time range.",
            "gridPos": {"h": 12, "w": 16, "x": 0, "y": 116},
            "options": {
                "calculate": False,
                "calculation": {},
                "cellGap": 2,
                "cellRadius": 0,
                "color": {
                    "mode": "scheme",
                    "scheme": "Viridis",
                    "steps": 64,
                    "reverse": False,
                    "exponent": 0.5,
                    "scale": "exponential",
                },
                "exemplars": {"color": "rgba(255,0,255,0.7)"},
                "filterValues": {"le": 1e-9},
                "legend": {"show": True},
                "rowsFrame": {"layout": "auto", "value": ""},
                "tooltip": {
                    "maxHeight": 600,
                    "mode": "single",
                    "yHistogram": False,
                    "showColorScale": True,
                },
                "yAxis": {
                    "axisPlacement": "left",
                    "decimals": 0,
                    "labelRotation": -90,
                    "reverse": False,
                    "unit": "short",
                },
            },
            "fieldConfig": {
                "defaults": {
                    "custom": {
                        "scaleDistribution": {"type": "linear"},
                        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    }
                },
                "overrides": [],
            },
            "targets": [target(heatmap_sql, "time_series")],
        }
    )
    panels.append(
        table(
            "Top query per bucket (${heatmap_metric}, by executions)",
            16,
            116,
            8,
            12,
            heatmap_companion_sql,
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "bucket"},
                    "properties": [{"id": "custom.width", "value": 80}],
                },
                {
                    "matcher": {"id": "byName", "options": "total_queries"},
                    "properties": [
                        {"id": "displayName", "value": "Count"},
                        {"id": "custom.width", "value": 55},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "top_query"},
                    "properties": [
                        {"id": "displayName", "value": "Top Query"},
                        {
                            "id": "custom.cellOptions",
                            "value": {"type": "auto", "wrapText": True},
                        },
                    ],
                },
            ],
            description="Top query (by total execution count) in each metric bucket across the "
            "selected time range. Ordered highest bucket first to align with the heatmap Y-axis. "
            "Workaround for Grafana heatmap panels not supporting per-cell tooltip metadata.",
        )
    )

    heatmap_metric_var = {
        "name": "heatmap_metric",
        "label": "Heatmap Metric",
        "type": "custom",
        "query": "Duration,CPU,Logical Reads,Logical Writes,Execution Count",
        "current": {"text": "Duration", "value": "Duration"},
        "options": [
            {"text": v, "value": v, "selected": v == "Duration"}
            for v in [
                "Duration",
                "CPU",
                "Logical Reads",
                "Logical Writes",
                "Execution Count",
            ]
        ],
        "multi": False,
        "includeAll": False,
        "hide": 0,
    }
    return dashboard(
        "perfmon-queries",
        "PerfMon · Queries",
        panels,
        [instance_var(), topn_var, heatmap_metric_var],
    )
