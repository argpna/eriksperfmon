from .._shared import *

_SQL = """
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @cutoff datetime2(7) =
    DATEADD(MINUTE, DATEDIFF(MINUTE, GETUTCDATE(), GETDATE()),
            CONVERT(datetime2, $__timeFrom()));
DECLARE @cutoff_end datetime2(7) =
    DATEADD(MINUTE, DATEDIFF(MINUTE, GETUTCDATE(), GETDATE()),
            CONVERT(datetime2, $__timeTo()));

WITH
    agg AS
    (
        SELECT
            qs.query_hash,
            database_name = MIN(qs.database_name),
            total_executions = SUM(qs.execution_count_delta),
            total_cpu_ms = SUM(qs.total_worker_time_delta) / 1000.0,
            total_duration_ms = SUM(qs.total_elapsed_time_delta) / 1000.0,
            total_reads = SUM(qs.total_logical_reads_delta),
            total_writes = SUM(qs.total_logical_writes_delta),
            total_memory_mb = SUM(ISNULL(qs.max_grant_kb, 0)) / 1024.0
        FROM collect.query_stats AS qs
        WHERE qs.collection_time >= @cutoff
        AND   qs.collection_time <= @cutoff_end
        AND   qs.query_hash IS NOT NULL
        AND   qs.execution_count_delta > 0
        GROUP BY qs.query_hash
        HAVING SUM(qs.execution_count_delta) > 0
    ),
    interesting AS
    (
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_cpu_ms DESC) x
        UNION
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_duration_ms DESC) x
        UNION
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_reads DESC) x
        UNION
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_writes DESC) x
        UNION
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_memory_mb DESC) x
        UNION
        SELECT query_hash FROM (SELECT TOP (10) query_hash FROM agg ORDER BY total_executions DESC) x
    ),
    scored AS
    (
        SELECT
            a.*,
            cpu_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_cpu_ms),
            duration_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_duration_ms),
            reads_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_reads),
            writes_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_writes),
            memory_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_memory_mb),
            executions_pctl =
                PERCENT_RANK() OVER (ORDER BY a.total_executions),
            cpu_share =
                CONVERT(decimal(5,1),
                    100.0 * a.total_cpu_ms /
                    NULLIF(SUM(a.total_cpu_ms) OVER (), 0)),
            duration_share =
                CONVERT(decimal(5,1),
                    100.0 * a.total_duration_ms /
                    NULLIF(SUM(a.total_duration_ms) OVER (), 0)),
            reads_share =
                CONVERT(decimal(5,1),
                    100.0 * a.total_reads /
                    NULLIF(SUM(CONVERT(float, a.total_reads)) OVER (), 0)),
            writes_share =
                CONVERT(decimal(5,1),
                    100.0 * a.total_writes /
                    NULLIF(SUM(CONVERT(float, a.total_writes)) OVER (), 0)),
            memory_share =
                CONVERT(decimal(5,1),
                    100.0 * a.total_memory_mb /
                    NULLIF(SUM(a.total_memory_mb) OVER (), 0))
        FROM agg AS a
        JOIN interesting AS i ON a.query_hash = i.query_hash
    ),
    with_text AS
    (
        SELECT
            s.*,
            query_preview =
            (
                SELECT TOP (1)
                    LEFT(CASE
                        WHEN qs2.query_text IS NOT NULL
                        THEN CAST(DECOMPRESS(qs2.query_text) AS nvarchar(max))
                        ELSE N''
                    END, 200)
                FROM collect.query_stats AS qs2
                WHERE qs2.query_hash = s.query_hash
                AND   qs2.collection_time >= @cutoff
                AND   qs2.query_text IS NOT NULL
                ORDER BY qs2.execution_count_delta DESC
            )
        FROM scored AS s
    )
SELECT
    impact_score =
        CONVERT(int,
        (
            ISNULL(cpu_pctl, 0) + ISNULL(duration_pctl, 0) + ISNULL(reads_pctl, 0) +
            ISNULL(writes_pctl, 0) + ISNULL(memory_pctl, 0) + ISNULL(executions_pctl, 0)
        ) / (
            CASE WHEN cpu_pctl IS NOT NULL THEN 1.0 ELSE 0 END +
            CASE WHEN duration_pctl IS NOT NULL THEN 1.0 ELSE 0 END +
            CASE WHEN reads_pctl IS NOT NULL THEN 1.0 ELSE 0 END +
            CASE WHEN writes_pctl IS NOT NULL THEN 1.0 ELSE 0 END +
            CASE WHEN memory_pctl IS NOT NULL THEN 1.0 ELSE 0 END +
            CASE WHEN executions_pctl IS NOT NULL THEN 1.0 ELSE 0 END
        ) * 100),
    database_name,
    total_executions,
    total_cpu_ms,
    cpu_share,
    total_duration_ms,
    duration_share,
    total_reads,
    reads_share,
    total_writes,
    total_memory_mb,
    query_preview
FROM with_text
ORDER BY
    ISNULL(cpu_pctl, 0) + ISNULL(duration_pctl, 0) + ISNULL(reads_pctl, 0) +
    ISNULL(writes_pctl, 0) + ISNULL(memory_pctl, 0) + ISNULL(executions_pctl, 0) DESC
OPTION(MAXDOP 1, RECOMPILE);
"""


def high_impact():
    reset_id()
    panels = [
        table(
            "High Impact Queries",
            0,
            0,
            24,
            16,
            _SQL,
            sort_by=[{"displayName": "impact_score", "desc": True}],
            overrides=[
                col_thresholds(
                    "impact_score", ("green", None), ("orange", 60), ("red", 80)
                )
            ],
        )
    ]
    return finops_dashboard(
        "finops-high-impact",
        "FinOps · High Impact",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="5m",
    )
