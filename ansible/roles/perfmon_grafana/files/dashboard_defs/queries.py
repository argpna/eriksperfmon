from functools import partial

from ._shared import *


def _lifetime_cte(cte_name, src_table, alias, group_cols, time_filter, agg_cols):
    """Phase-1 per-lifetime dedup CTE shared by Query Stats and Procedure Stats: within each
    lifetime key (plan/cache creation time + identity columns), collapse the repeated
    per-collection snapshot rows down to one MAX/MIN per column (see the two panels'
    header comments for why - no _delta columns are involved).

    group_cols: raw qualified column refs forming the dedup key, e.g. ['qs.database_name',
    'qs.query_hash', 'qs.creation_time']. agg_cols: (output_name, agg_func, source_col)
    triples for the aggregated columns.
    """
    select_lines = list(group_cols) + [
        f"{name} = {agg}({src})" for name, agg, src in agg_cols
    ]
    select_clause = ",\n        ".join(select_lines)
    group_clause = ", ".join(group_cols)
    return f"""{cte_name} AS (
    SELECT
        {select_clause}
    FROM {src_table} AS {alias}
    WHERE {time_filter}
    GROUP BY {group_clause}
)"""


def _metric_group(
    pl, exec_col, src, total=None, avg=None, min_=None, max_=None, ms=False
):
    """Phase-2 total/avg/min/max column expressions, re-derived across lifetimes from a
    _lifetime_cte's total_<src>/min_<src>/max_<src> columns and weighted by exec_col.
    Pass None to omit a column (not every metric has all four - e.g. dop/grant_kb are
    min/max only, logical_reads has no min/max in Query Stats). ms=True applies the
    upstream's ms-to-ms conversion (/1000 raw total, /1000.0 with decimal(19,2) cast for
    avg/min/max).
    """
    lines = []
    tcol, mncol, mxcol = f"{pl}.total_{src}", f"{pl}.min_{src}", f"{pl}.max_{src}"
    if total:
        lines.append(f"{total} = SUM({tcol})" + (" / 1000" if ms else ""))
    if avg:
        if ms:
            lines.append(
                f"{avg} = CONVERT(decimal(19,2), SUM({tcol}) / 1000.0 / NULLIF(SUM({exec_col}), 0))"
            )
        else:
            lines.append(f"{avg} = SUM({tcol}) / NULLIF(SUM({exec_col}), 0)")
    if min_:
        lines.append(
            f"{min_} = CONVERT(decimal(19,2), MIN({mncol}) / 1000.0)"
            if ms
            else f"{min_} = MIN({mncol})"
        )
    if max_:
        lines.append(
            f"{max_} = CONVERT(decimal(19,2), MAX({mxcol}) / 1000.0)"
            if ms
            else f"{max_} = MAX({mxcol})"
        )
    return lines


# Upstream tab: Queries
# 9 sub-tabs: Performance Trends, Active Queries, Current Active Queries, Query Stats,
# Procedure Stats, Query Store, Query Store Regressions, Query Trace Patterns, Query Heatmap
def queries():
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
    active_queries_mode_var = {
        "name": "active_queries_mode",
        "label": "Active Queries Mode",
        "type": "custom",
        "query": "Historical,Real-Time",
        "current": {"text": "Historical", "value": "Historical"},
        "options": [
            {"text": v, "value": v, "selected": v == "Historical"}
            for v in ["Historical", "Real-Time"]
        ],
        "multi": False,
        "includeAll": False,
        "hide": 0,
    }
    panels = []

    # Upstream ref: GetQueryDurationTrendsAsync / GetProcedureDurationTrendsAsync /
    # GetQueryStoreDurationTrendsAsync / GetExecutionTrendsAsync (DatabaseService.QueryPerformance.Trends.cs)
    y = subtab(
        panels,
        "Performance Trends",
        0,
        [
            # Upstream ref: GetQueryDurationTrendsAsync (DatabaseService.QueryPerformance.Trends.cs).
            # Rate-normalised total elapsed ms across all queries per collection interval.
            (
                6,
                8,
                partial(
                    timeseries,
                    "Query durations (elapsed ms/s, all queries)",
                    targets=[target("""
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
                ),
            ),
            # Upstream ref: GetProcedureDurationTrendsAsync (DatabaseService.QueryPerformance.Trends.cs).
            # Same metric but from collect.procedure_stats.
            (
                6,
                8,
                partial(
                    timeseries,
                    "Procedure durations (elapsed ms/s, all procedures)",
                    targets=[target("""
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
                ),
            ),
            # Upstream ref: GetQueryStoreDurationTrendsAsync (DatabaseService.QueryPerformance.Trends.cs).
            # Rate-normalised from collect.query_store_data. QS has no delta columns; uses
            # avg_duration * count_executions as total work per interval.
            (
                6,
                8,
                partial(
                    timeseries,
                    "Query Store durations (elapsed ms/s)",
                    targets=[target("""
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
                ),
            ),
            # Upstream ref: GetExecutionTrendsAsync (DatabaseService.QueryPerformance.Trends.cs).
            # Executions per second across all queries.
            (
                6,
                8,
                partial(
                    timeseries,
                    "Execution counts (executions/s, all queries)",
                    targets=[target("""
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
                ),
            ),
        ],
    )

    # Upstream ref: GetQuerySnapshotsAsync / GetCurrentActiveQueriesAsync (DatabaseService.QueryPerformance.Snapshots.cs).
    # Merges upstream's Active Queries (historical sp_WhoIsActive snapshots) and Current
    # Active Queries sub-tabs into one panel toggled by $active_queries_mode.
    active_queries_sql = f"""
IF N${{active_queries_mode:sqlstring}} = N'Real-Time'
BEGIN
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
    /* SET LOCK_TIMEOUT 1000; */

    SELECT
        der.session_id,
        database_name = DB_NAME(der.database_id),
        duration =
            CASE
                WHEN der.total_elapsed_time < 0
                THEN '00 00:00:00.000'
                ELSE RIGHT(REPLICATE('0', 2) + CONVERT(varchar(10), der.total_elapsed_time / 86400000), 2) +
                     ' ' + RIGHT(CONVERT(varchar(30), DATEADD(SECOND, der.total_elapsed_time / 1000, 0), 120), 9) +
                     '.' + RIGHT('000' + CONVERT(varchar(3), der.total_elapsed_time % 1000), 3)
            END,
        query_text = SUBSTRING(dest.text, (der.statement_start_offset / 2) + 1,
            ((CASE der.statement_end_offset WHEN -1 THEN DATALENGTH(dest.text)
              ELSE der.statement_end_offset END - der.statement_start_offset) / 2) + 1),
        query_plan = TRY_CAST(deqp.query_plan AS nvarchar(max)),
        live_query_plan = CONVERT(nvarchar(max), deqs.query_plan),
        der.status,
        der.blocking_session_id,
        der.wait_type,
        wait_time_ms = CONVERT(bigint, der.wait_time),
        der.wait_resource,
        cpu_time_ms = CONVERT(bigint, der.cpu_time),
        total_elapsed_time_ms = CONVERT(bigint, der.total_elapsed_time),
        der.reads,
        der.writes,
        der.logical_reads,
        granted_query_memory_gb = CONVERT(decimal(38, 2), (der.granted_query_memory / 128. / 1024.)),
        transaction_isolation_level =
            CASE der.transaction_isolation_level
                WHEN 0 THEN 'Unspecified'
                WHEN 1 THEN 'Read Uncommitted'
                WHEN 2 THEN 'Read Committed'
                WHEN 3 THEN 'Repeatable Read'
                WHEN 4 THEN 'Serializable'
                WHEN 5 THEN 'Snapshot'
                ELSE '???'
            END,
        der.dop,
        der.parallel_worker_count,
        des.login_name,
        des.host_name,
        des.program_name,
        des.open_transaction_count,
        der.percent_complete
    FROM sys.dm_exec_requests AS der
    JOIN sys.dm_exec_sessions AS des
        ON des.session_id = der.session_id
    OUTER APPLY sys.dm_exec_sql_text(COALESCE(der.sql_handle, der.plan_handle)) AS dest
    OUTER APPLY sys.dm_exec_text_query_plan(der.plan_handle, der.statement_start_offset, der.statement_end_offset) AS deqp
    OUTER APPLY sys.dm_exec_query_statistics_xml(der.session_id) AS deqs
    WHERE der.session_id <> @@SPID
        AND der.session_id >= 50
        AND dest.text IS NOT NULL
        AND der.database_id <> ISNULL(DB_ID(N'PerformanceMonitor'), 0)
    ORDER BY der.cpu_time DESC, der.parallel_worker_count DESC
    OPTION (MAXDOP 1, RECOMPILE);
END
ELSE
BEGIN
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    SELECT TOP (ISNULL(TRY_CAST(${{topn:sqlstring}} AS int), 25))
        collection_time = {tz_col('qs.collection_time')},
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
        start_time = {tz_col('qs.start_time')},
        tran_start_time = {tz_col('qs.tran_start_time')},
        qs.request_id,
        qs.login_name,
        qs.host_name,
        qs.database_name,
        qs.program_name,
        additional_info = CONVERT(nvarchar(max), qs.additional_info),
        sql_text = {strip_whoisactive_wrapper('CONVERT(nvarchar(max), qs.sql_text)')},
        sql_command = {strip_whoisactive_wrapper('CONVERT(nvarchar(max), qs.sql_command)')}
    FROM report.query_snapshots AS qs
    WHERE $__timeFilter(qs.collection_time)
        AND CONVERT(nvarchar(max), qs.sql_text) NOT LIKE N'WAITFOR%'
    ORDER BY qs.collection_time DESC;
END
"""
    # Upstream ref: GetQuerySnapshotsAsync / GetCurrentActiveQueriesAsync (DatabaseService.QueryPerformance.Snapshots.cs)
    y = subtab(
        panels,
        "Active Queries",
        y,
        [
            (
                24,
                12,
                partial(
                    table,
                    "Active queries (${active_queries_mode})",
                    sql=active_queries_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                    description="Historical mode (default) reads sp_WhoIsActive snapshots collected on a "
                    "schedule from report.query_snapshots, bound by the dashboard time range. Real-Time mode "
                    "queries sys.dm_exec_requests/sessions live against the current instance, independent of "
                    "the time range, for monitoring queries executing right now.",
                ),
            ),
        ],
    )

    # Upstream ref: GetQueryStatsAsync (DatabaseService.QueryPerformance.Stats.cs).
    # Phase 1 aggregates cumulative MAX(execution_count) per plan lifetime
    # (database_name, query_hash, creation_time). Phase 2 sums across lifetimes grouped
    # by (database_name, query_hash). Time filter uses creation_time/last_execution_time
    # so plans that were active during the selected window are included regardless of
    # whether they survived to the next collection interval. No _delta columns used.
    def _qs_sql(order):
        per_lifetime = _lifetime_cte(
            "per_lifetime",
            "collect.query_stats",
            "qs",
            ["qs.database_name", "qs.query_hash", "qs.creation_time"],
            f"qs.creation_time <= {tz_to()} AND qs.last_execution_time >= {tz_from()}",
            [
                ("object_type", "MAX", "qs.object_type"),
                ("schema_name", "MAX", "qs.schema_name"),
                ("object_name", "MAX", "qs.object_name"),
                ("last_execution_time", "MAX", "qs.last_execution_time"),
                ("execution_count", "MAX", "qs.execution_count"),
                ("total_worker_time", "MAX", "qs.total_worker_time"),
                ("min_worker_time", "MIN", "qs.min_worker_time"),
                ("max_worker_time", "MAX", "qs.max_worker_time"),
                ("total_elapsed_time", "MAX", "qs.total_elapsed_time"),
                ("min_elapsed_time", "MIN", "qs.min_elapsed_time"),
                ("max_elapsed_time", "MAX", "qs.max_elapsed_time"),
                ("total_logical_reads", "MAX", "qs.total_logical_reads"),
                ("total_logical_writes", "MAX", "qs.total_logical_writes"),
                ("total_physical_reads", "MAX", "qs.total_physical_reads"),
                ("min_physical_reads", "MIN", "qs.min_physical_reads"),
                ("max_physical_reads", "MAX", "qs.max_physical_reads"),
                ("total_rows", "MAX", "qs.total_rows"),
                ("min_rows", "MIN", "qs.min_rows"),
                ("max_rows", "MAX", "qs.max_rows"),
                ("min_dop", "MIN", "qs.min_dop"),
                ("max_dop", "MAX", "qs.max_dop"),
                ("min_grant_kb", "MIN", "qs.min_grant_kb"),
                ("max_grant_kb", "MAX", "qs.max_grant_kb"),
                ("total_spills", "MAX", "qs.total_spills"),
                ("min_spills", "MIN", "qs.min_spills"),
                ("max_spills", "MAX", "qs.max_spills"),
                ("query_plan_hash", "MAX", "qs.query_plan_hash"),
                ("sql_handle", "MAX", "qs.sql_handle"),
                ("plan_handle", "MAX", "qs.plan_handle"),
            ],
        )
        metric_cols = (
            _metric_group(
                "pl",
                "pl.execution_count",
                "worker_time",
                total="total_cpu_ms",
                avg="avg_cpu_ms",
                min_="min_cpu_ms",
                max_="max_cpu_ms",
                ms=True,
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "elapsed_time",
                total="total_elapsed_ms",
                avg="avg_elapsed_ms",
                min_="min_elapsed_ms",
                max_="max_elapsed_ms",
                ms=True,
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "logical_reads",
                total="logical_reads",
                avg="avg_logical_reads",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "logical_writes",
                total="logical_writes",
                avg="avg_logical_writes",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "physical_reads",
                total="physical_reads",
                avg="avg_physical_reads",
                min_="min_physical_reads",
                max_="max_physical_reads",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "rows",
                total="total_rows",
                avg="avg_rows",
                min_="min_rows",
                max_="max_rows",
            )
            + _metric_group(
                "pl", "pl.execution_count", "dop", min_="min_dop", max_="max_dop"
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "grant_kb",
                min_="min_grant_kb",
                max_="max_grant_kb",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "spills",
                total="total_spills",
                min_="min_spills",
                max_="max_spills",
            )
        )
        metric_clause = ",\n    ".join(metric_cols)
        return f"""
WITH {per_lifetime}
SELECT TOP (ISNULL(TRY_CAST(${{topn:sqlstring}} AS int), 25))
    database_name        = pl.database_name,
    query_hash           = CONVERT(nvarchar(20), pl.query_hash, 1),
    object_type          = MAX(pl.object_type),
    object_name          = CASE MAX(pl.object_type)
        WHEN N'STATEMENT' THEN N'Adhoc'
        ELSE QUOTENAME(MAX(pl.schema_name)) + N'.' + QUOTENAME(MAX(pl.object_name))
    END,
    first_execution_time = {tz_col('MIN(pl.creation_time)')},
    last_execution_time  = {tz_col('MAX(pl.last_execution_time)')},
    executions           = SUM(pl.execution_count),
    {metric_clause},
    query_plan_hash      = CONVERT(nvarchar(20), MAX(pl.query_plan_hash), 1),
    sql_handle           = CONVERT(nvarchar(130), MAX(pl.sql_handle), 1),
    plan_handle          = CONVERT(nvarchar(130), MAX(pl.plan_handle), 1),
    query_text           = (
        SELECT TOP (1)
            CAST(DECOMPRESS(qs2.query_text) AS nvarchar(max))
        FROM collect.query_stats AS qs2
        WHERE qs2.query_hash = pl.query_hash
            AND qs2.database_name = pl.database_name
        ORDER BY qs2.collection_time DESC
    )
FROM per_lifetime AS pl
GROUP BY pl.database_name, pl.query_hash
ORDER BY {order} DESC
/* Upstream applies OPTION(RECOMPILE, HASH GROUP, HASH JOIN, USE HINT('ENABLE_PARALLEL_PLAN_PREFERENCE'))
   tuned for its two-phase #top_ranked temp-table script; this is a single CTE, a different plan
   shape. */
OPTION(RECOMPILE);
"""

    qs_link = col_datalink(
        "query_hash",
        "View query history",
        "/d/perfmon-query-history?${__url_time_range}&var-instance=${instance}"
        "&var-database=${__data.fields.database_name}&var-query_hash=${__data.fields.query_hash}",
    )
    parameter_sensitivity_sql = f"""
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
        last_execution_time     = {tz_col('MAX(qs.last_execution_time)')}
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
"""

    # Upstream ref: GetQueryStatsAsync (DatabaseService.QueryPerformance.Stats.cs)
    y = subtab(
        panels,
        "Query Stats",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Top queries by CPU",
                    sql=_qs_sql(
                        "SUM(pl.total_worker_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0)"
                    ),
                    overrides=[qs_link],
                ),
            ),
            (
                24,
                10,
                partial(
                    table,
                    "Top queries by logical reads",
                    sql=_qs_sql("SUM(pl.total_logical_reads)"),
                    overrides=[qs_link],
                ),
            ),
            (
                24,
                10,
                partial(
                    table,
                    "Parameter sensitivity / plan instability",
                    sql=parameter_sensitivity_sql,
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
                ),
            ),
        ],
    )

    # Upstream ref: GetProcedureStatsAsync (DatabaseService.QueryPerformance.Stats.cs).
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

    def _ps_sql():
        per_lifetime = _lifetime_cte(
            "per_lifetime",
            "collect.procedure_stats",
            "ps",
            ["ps.database_name", "ps.schema_name", "ps.object_name", "ps.cached_time"],
            f"ps.cached_time <= {tz_to()} AND ps.last_execution_time >= {tz_from()}",
            [
                ("object_type", "MAX", "ps.object_type"),
                ("type_desc", "MAX", "ps.type_desc"),
                ("object_id", "MAX", "ps.object_id"),
                ("last_execution_time", "MAX", "ps.last_execution_time"),
                ("execution_count", "MAX", "ps.execution_count"),
                ("total_worker_time", "MAX", "ps.total_worker_time"),
                ("min_worker_time", "MIN", "ps.min_worker_time"),
                ("max_worker_time", "MAX", "ps.max_worker_time"),
                ("total_elapsed_time", "MAX", "ps.total_elapsed_time"),
                ("min_elapsed_time", "MIN", "ps.min_elapsed_time"),
                ("max_elapsed_time", "MAX", "ps.max_elapsed_time"),
                ("total_logical_reads", "MAX", "ps.total_logical_reads"),
                ("min_logical_reads", "MIN", "ps.min_logical_reads"),
                ("max_logical_reads", "MAX", "ps.max_logical_reads"),
                ("total_logical_writes", "MAX", "ps.total_logical_writes"),
                ("min_logical_writes", "MIN", "ps.min_logical_writes"),
                ("max_logical_writes", "MAX", "ps.max_logical_writes"),
                ("total_physical_reads", "MAX", "ps.total_physical_reads"),
                ("min_physical_reads", "MIN", "ps.min_physical_reads"),
                ("max_physical_reads", "MAX", "ps.max_physical_reads"),
                ("total_spills", "MAX", "ps.total_spills"),
                ("min_spills", "MIN", "ps.min_spills"),
                ("max_spills", "MAX", "ps.max_spills"),
                ("sql_handle", "MAX", "ps.sql_handle"),
                ("plan_handle", "MAX", "ps.plan_handle"),
            ],
        )
        metric_cols = (
            _metric_group(
                "pl",
                "pl.execution_count",
                "worker_time",
                total="total_cpu_ms",
                avg="avg_cpu_ms",
                min_="min_cpu_ms",
                max_="max_cpu_ms",
                ms=True,
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "elapsed_time",
                total="total_elapsed_ms",
                avg="avg_elapsed_ms",
                min_="min_elapsed_ms",
                max_="max_elapsed_ms",
                ms=True,
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "logical_reads",
                total="logical_reads",
                avg="avg_logical_reads",
                min_="min_logical_reads",
                max_="max_logical_reads",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "logical_writes",
                total="logical_writes",
                avg="avg_logical_writes",
                min_="min_logical_writes",
                max_="max_logical_writes",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "physical_reads",
                total="physical_reads",
                avg="avg_physical_reads",
                min_="min_physical_reads",
                max_="max_physical_reads",
            )
            + _metric_group(
                "pl",
                "pl.execution_count",
                "spills",
                total="total_spills",
                avg="avg_spills",
                min_="min_spills",
                max_="max_spills",
            )
        )
        metric_clause = ",\n    ".join(metric_cols)
        return f"""
WITH {per_lifetime}
SELECT TOP (ISNULL(TRY_CAST(${{topn:sqlstring}} AS int), 25))
    database_name    = pl.database_name,
    object_name      = QUOTENAME(pl.schema_name) + N'.' + QUOTENAME(pl.object_name),
    schema_name      = pl.schema_name,
    procedure_name   = pl.object_name,
    object_type      = MAX(pl.object_type),
    type_desc        = MAX(pl.type_desc),
    first_cached_time = {tz_col('MIN(pl.cached_time)')},
    last_execution_time = {tz_col('MAX(pl.last_execution_time)')},
    executions       = SUM(pl.execution_count),
    {metric_clause},
    object_id        = MAX(pl.object_id),
    sql_handle       = CONVERT(varchar(130), MAX(pl.sql_handle), 1),
    plan_handle      = CONVERT(varchar(130), MAX(pl.plan_handle), 1)
FROM per_lifetime AS pl
GROUP BY pl.database_name, pl.schema_name, pl.object_name
ORDER BY SUM(pl.total_worker_time) / 1000.0 / NULLIF(SUM(pl.execution_count), 0) DESC
/* Upstream applies OPTION(RECOMPILE, HASH GROUP, HASH JOIN, USE HINT('ENABLE_PARALLEL_PLAN_PREFERENCE'))
   tuned for its two-phase #top_ranked temp-table script; this is a single CTE, a different plan
   shape. */
OPTION(RECOMPILE);
"""

    # Upstream ref: GetProcedureStatsAsync (DatabaseService.QueryPerformance.Stats.cs)
    y = subtab(
        panels,
        "Procedure Stats",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Top procedures / triggers / functions by CPU",
                    sql=_ps_sql(),
                    overrides=[proc_link],
                ),
            ),
        ],
    )

    # Upstream ref: GetQueryStoreDataAsync (DatabaseService.QueryPerformance.Stats.cs)
    query_store_sql = f"""
WITH ranked AS (
    SELECT TOP (ISNULL(TRY_CAST(${{topn:sqlstring}} AS int), 25))
        qsd.database_name,
        qsd.query_id,
        execution_type_desc   = MAX(qsd.execution_type_desc),
        module_name           = MAX(qsd.module_name),
        first_execution_time  = {tz_col("MIN(qsd.server_first_execution_time)")},
        last_execution_time   = {tz_col("MAX(qsd.server_last_execution_time)")},
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
        avg_clr_time_ms       = CONVERT(decimal(19,2), SUM(CONVERT(float, qsd.avg_clr_time) * qsd.count_executions) / 1000.0 / NULLIF(SUM(qsd.count_executions), 0)),
        min_clr_time_ms       = CONVERT(decimal(19,2), MIN(qsd.min_clr_time) / 1000.0),
        max_clr_time_ms       = CONVERT(decimal(19,2), MAX(qsd.max_clr_time) / 1000.0),
        avg_num_physical_io_reads = SUM(CONVERT(float, qsd.avg_num_physical_io_reads) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_num_physical_io_reads = MIN(qsd.min_num_physical_io_reads),
        max_num_physical_io_reads = MAX(qsd.max_num_physical_io_reads),
        avg_log_bytes_used    = SUM(CONVERT(float, qsd.avg_log_bytes_used) * qsd.count_executions) / NULLIF(SUM(qsd.count_executions), 0),
        min_log_bytes_used    = MIN(qsd.min_log_bytes_used),
        max_log_bytes_used    = MAX(qsd.max_log_bytes_used)
    FROM collect.query_store_data AS qsd
    WHERE qsd.server_first_execution_time <= {tz_to()}
        AND qsd.server_last_execution_time >= {tz_from()}
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
ORDER BY r.avg_cpu_time_ms DESC
/* Upstream applies OPTION(RECOMPILE, HASH GROUP, HASH JOIN, USE HINT('ENABLE_PARALLEL_PLAN_PREFERENCE'))
   tuned for its two-phase #top_ranked temp-table script; this is a single CTE, a different plan
   shape. */
OPTION(RECOMPILE);
"""

    # Upstream ref: GetQueryStoreDataAsync (DatabaseService.QueryPerformance.Stats.cs)
    y = subtab(
        panels,
        "Query Store",
        y,
        [
            (
                24,
                10,
                partial(
                    table, "Top queries by avg CPU (Query Store)", sql=query_store_sql
                ),
            ),
        ],
    )

    # Upstream ref: GetQueryStoreRegressionsAsync (DatabaseService.QueryPerformance.Trends.cs)
    query_store_regressions_sql = f"""
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
    qsr.query_text_sample,
    last_execution_time  = {tz_col("qsr.last_execution_time")}
FROM report.query_store_regressions({tz_from()}, {tz_to()}) AS qsr
ORDER BY qsr.additional_duration_ms DESC;
"""

    # Upstream ref: GetQueryStoreRegressionsAsync (DatabaseService.QueryPerformance.Trends.cs)
    y = subtab(
        panels,
        "Query Store Regressions",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Query Store regressions (recent vs baseline window)",
                    sql=query_store_regressions_sql,
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
                ),
            ),
        ],
    )

    # Upstream ref: GetLongRunningQueryPatternsAsync (DatabaseService.QueryPerformance.Trends.cs)
    query_trace_patterns_sql = f"""
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
        sample_query_text = MAX(ta.sql_text),
        last_execution = {tz_col('MAX(ta.end_time)')}
    FROM collect.trace_analysis AS ta
    WHERE ta.collection_time >= DATEADD(MINUTE, -10, {tz_from()})
        AND ta.collection_time <= DATEADD(MINUTE, 10, {tz_to()})
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
    qp.query_pattern,
    qp.sample_query_text
FROM query_patterns AS qp
WHERE qp.executions > 1
ORDER BY qp.avg_duration_ms DESC;
"""

    # Upstream ref: GetLongRunningQueryPatternsAsync (DatabaseService.QueryPerformance.Trends.cs)
    y = subtab(
        panels,
        "Query Trace Patterns",
        y,
        [
            (
                24,
                10,
                partial(
                    table,
                    "Long-running query patterns (SQL Trace)",
                    sql=query_trace_patterns_sql,
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
                ),
            ),
        ],
    )

    # Upstream ref: GetQueryHeatmapAsync (DatabaseService.QueryPerformance.Heatmap.cs).
    # Bins queries from collect.query_stats into 7 magnitude buckets (powers of 10: 0-1, 1-10,
    # 10-100, 100-1K, 1K-10K, 10K-100K, >100K) per 5-minute time window. The $heatmap_metric
    # variable selects the per-execution metric: Duration/CPU in ms, Reads/Writes in pages,
    # Execution Count as raw count. Bucket labels reflect the selected metric unit.
    # Returns time-series rows (time, metric=bucket_label, value=query_count) so Grafana's
    # native Heatmap panel renders one Y-row per bucket, coloured by query concentration.
    # Grafana limitation: upstream tooltip shows the top query text for each cell (from
    # DECOMPRESS(query_text) with per-bucket MAX(execution_count_delta)), Grafana's heatmap
    # panel cannot embed extra data fields in hover tooltips.
    heatmap_sql = f"""
WITH metric_vals AS (
    SELECT
        time_bin     = DATEADD(MINUTE, DATEDIFF(MINUTE, 0, {tz_col('qs.collection_time')}) / 5 * 5, 0),
        metric_value = CASE N${{heatmap_metric:sqlstring}}
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
        WHEN 0 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'0: 0-1ms'     ELSE N'0: 0-1'       END
        WHEN 1 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'1: 1-10ms'    ELSE N'1: 1-10'     END
        WHEN 2 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'2: 10-100ms'  ELSE N'2: 10-100'   END
        WHEN 3 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'3: 100ms-1s'  ELSE N'3: 100-1K'   END
        WHEN 4 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'4: 1-10s'     ELSE N'4: 1K-10K'   END
        WHEN 5 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'5: 10-100s'   ELSE N'5: 10K-100K' END
        ELSE        CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'6: >100s'     ELSE N'6: >100K'    END
    END,
    value  = CAST(b.query_count AS float)
FROM bucketed AS b
UNION ALL
SELECT
    time   = DATEADD(MINUTE, DATEDIFF(MINUTE, 0, CONVERT(datetime2, $__timeFrom(), 127)) / 5 * 5, 0), /* time-macro-allow: heatmap synthetic zero-row anchor, UTC output time-axis value Grafana buckets against, not a filter on a server-local column */
    metric = CASE ab.n
        WHEN 0 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'0: 0-1ms'     ELSE N'0: 0-1'       END
        WHEN 1 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'1: 1-10ms'    ELSE N'1: 1-10'     END
        WHEN 2 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'2: 10-100ms'  ELSE N'2: 10-100'   END
        WHEN 3 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'3: 100ms-1s'  ELSE N'3: 100-1K'   END
        WHEN 4 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'4: 1-10s'     ELSE N'4: 1K-10K'   END
        WHEN 5 THEN CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'5: 10-100s'   ELSE N'5: 10K-100K' END
        ELSE        CASE WHEN N${{heatmap_metric:sqlstring}} IN (N'Duration', N'CPU') THEN N'6: >100s'     ELSE N'6: >100K'    END
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
        metric_value = CASE N${heatmap_metric:sqlstring}
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
        WHEN 0 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'0-1ms'    ELSE N'0-1'       END
        WHEN 1 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'1-10ms'   ELSE N'1-10'      END
        WHEN 2 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'10-100ms' ELSE N'10-100'    END
        WHEN 3 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'100ms-1s' ELSE N'100-1K'    END
        WHEN 4 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'1-10s'    ELSE N'1K-10K'   END
        WHEN 5 THEN CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'10-100s'  ELSE N'10K-100K' END
        ELSE        CASE WHEN N${heatmap_metric:sqlstring} IN (N'Duration', N'CPU') THEN N'>100s'    ELSE N'>100K'    END
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

    def _query_heatmap(x, y, w, h):
        return {
            "id": nid(),
            "type": "heatmap",
            "title": "Query heatmap (${heatmap_metric} distribution over time)",
            "datasource": DS,
            "description": "Each cell = number of query executions in that metric bucket for the "
            "5-minute window. Duration and CPU buckets are in ms; Reads and Writes in pages. "
            "Brighter/yellower = more queries. Y-axis is ordered 0 (fastest/smallest) to 6 (slowest/largest). "
            "The companion table to the right shows the top query per bucket across the full time range.",
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
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

    # Upstream ref: GetQueryHeatmapAsync (DatabaseService.QueryPerformance.Heatmap.cs)
    subtab(
        panels,
        "Query Heatmap",
        y,
        [
            (16, 12, _query_heatmap),
            (
                8,
                12,
                partial(
                    table,
                    "Top query per bucket (${heatmap_metric}, by impact)",
                    sql=heatmap_companion_sql,
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
                    description="Top query (by total_impact = metric_value * exec_count) in each metric "
                    "bucket across the selected time range. Ordered highest bucket first to align with "
                    "the heatmap Y-axis. Workaround for Grafana heatmap panels not supporting per-cell "
                    "tooltip metadata.",
                ),
            ),
        ],
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
        [instance_var(), topn_var, active_queries_mode_var, heatmap_metric_var],
    )
