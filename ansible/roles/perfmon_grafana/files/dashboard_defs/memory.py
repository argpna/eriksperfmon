from functools import partial

from ._shared import *


# Upstream tab: Memory
# 5 sub-tabs: Memory Overview, Memory Grants, Memory Clerks, Plan Cache, Memory Pressure Events
def memory():
    panels = []
    th_wait = thresholds(("green", None), ("yellow", 1), ("red", 5))
    th_pressure = thresholds(("green", None), ("red", 1))

    stats = [
        (
            "Buffer pool % of target (now)",
            "percent",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = buffer_pool_mb * 100.0 / NULLIF(committed_target_memory_mb, 0) FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "SQL Server total memory (now)",
            "decmbytes",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = total_memory_mb FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "Total physical memory (now)",
            "decmbytes",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = total_physical_memory_mb FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "OS available memory (now)",
            "decmbytes",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = available_physical_memory_mb FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "Committed target memory (now)",
            "decmbytes",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = committed_target_memory_mb FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "Memory grant waiters (now)",
            "short",
            th_wait,
            "SELECT v = ISNULL(SUM(mgs.waiter_count), 0) FROM collect.memory_grant_stats AS mgs WHERE mgs.collection_time = (SELECT MAX(collection_time) FROM collect.memory_grant_stats);",
        ),
        (
            "Buffer pool pressure (now)",
            "short",
            th_pressure,
            "SELECT TOP (1) v = CONVERT(int, buffer_pool_pressure_warning) FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
        (
            "Plan cache pressure (now)",
            "short",
            th_pressure,
            "SELECT TOP (1) v = CONVERT(int, plan_cache_pressure_warning) FROM collect.memory_stats ORDER BY collection_time DESC;",
        ),
    ]
    memory_breakdown_sql = "SELECT time = ms.collection_time, buffer_pool = ms.buffer_pool_mb, plan_cache = ms.plan_cache_mb, other = ms.other_memory_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
    os_memory_sql = "SELECT time = ms.collection_time, in_use = ms.physical_memory_in_use_mb, available = ms.available_physical_memory_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
    # Upstream ref: report.memory_usage_trends view (server-side, no separate C# fetch method)
    memory_usage_trends_sql = f"""
SELECT TOP (25)
    collection_time = {tz_col('mut.collection_time')},
    total_mb = mut.total_memory_mb,
    util_pct = mut.memory_utilization_percentage,
    util_change = mut.memory_utilization_change,
    mut.memory_state,
    mut.buffer_pool_state,
    mut.plan_cache_state,
    mut.recommendation
FROM report.memory_usage_trends AS mut
WHERE
    mut.memory_state <> N'STABLE'
    OR mut.buffer_pool_state NOT IN (N'STABLE', N'BASELINE')
    OR mut.plan_cache_state NOT IN (N'STABLE', N'BASELINE')
ORDER BY mut.collection_time DESC;
"""

    # Upstream ref: UpdateMemoryStatsSummaryPanel / LoadMemoryStatsOverviewChart (MemoryContent.MemoryStats.cs)
    y = subtab(
        panels,
        "Memory Overview",
        0,
        [
            (6, 4, partial(stat, title, sql=sql, unit=unit, th=th))
            for title, unit, th, sql in stats
        ]
        + [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Memory breakdown",
                    targets=[target(memory_breakdown_sql)],
                    unit="decmbytes",
                    stacked=True,
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "OS memory (in use vs available)",
                    targets=[target(os_memory_sql)],
                    unit="decmbytes",
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Memory usage trends",
                    sql=memory_usage_trends_sql,
                    overrides=[
                        status_colors(
                            "memory_state",
                            {
                                "SPIKE": "red",
                                "DROP": "orange",
                                "STABLE": "green",
                                "BASELINE": "blue",
                            },
                        ),
                        status_colors(
                            "buffer_pool_state",
                            {
                                "PRESSURE WARNING (new)": "red",
                                "GROWTH": "orange",
                                "SHRINK": "yellow",
                                "STABLE": "green",
                                "BASELINE": "blue",
                            },
                        ),
                        status_colors(
                            "plan_cache_state",
                            {
                                "PRESSURE WARNING (new)": "red",
                                "GROWTH": "orange",
                                "FLUSH": "yellow",
                                "STABLE": "green",
                                "BASELINE": "blue",
                            },
                        ),
                    ],
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    grants_by_pool_sql = """
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Granted MB', value = SUM(mgs.granted_memory_mb)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
UNION ALL
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Used MB', value = SUM(mgs.used_memory_mb)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
UNION ALL
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Available MB', value = SUM(mgs.available_memory_mb)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
ORDER BY time;
"""
    grant_queue_sql = """
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Grantees', value = SUM(mgs.grantee_count)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
UNION ALL
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Waiters', value = SUM(mgs.waiter_count)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
UNION ALL
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Timeouts', value = SUM(mgs.timeout_error_count_delta)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
UNION ALL
SELECT time = mgs.collection_time, metric = 'Pool ' + CONVERT(varchar(10), mgs.pool_id) + ': Forced Grants', value = SUM(mgs.forced_grant_count_delta)
FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time, mgs.pool_id
ORDER BY time;
"""
    # Upstream ref: report.memory_grant_pressure view. GetMemoryPressureAsync
    # (DatabaseService.ResourceMetrics.Memory.cs) reads the same source but isn't wired to
    # any WPF panel upstream, so there's no UI equivalent to cite for this data.
    memory_grant_pressure_sql = f"""
SELECT
    collection_time = {tz_col('mgp.collection_time')},
    active_grants = mgp.active_grants,
    queries_waiting = mgp.queries_waiting,
    available_mb = mgp.available_memory_mb,
    granted_mb = mgp.granted_memory_mb,
    used_mb = mgp.used_memory_mb,
    utilization_pct = mgp.memory_utilization_percent,
    timeout_errors = mgp.timeout_errors,
    forced_grants = mgp.forced_grants,
    mgp.pressure_level,
    mgp.recommendation
FROM report.memory_grant_pressure AS mgp;
"""
    memory_grant_stats_sql = f"""
SELECT TOP (200)
    mgs.collection_id,
    collection_time = {tz_col('mgs.collection_time')},
    server_start_time = {tz_col('mgs.server_start_time')},
    mgs.resource_semaphore_id,
    mgs.pool_id,
    mgs.target_memory_mb,
    mgs.max_target_memory_mb,
    mgs.total_memory_mb,
    mgs.available_memory_mb,
    mgs.granted_memory_mb,
    mgs.used_memory_mb,
    mgs.grantee_count,
    mgs.waiter_count,
    mgs.timeout_error_count,
    mgs.forced_grant_count,
    mgs.timeout_error_count_delta,
    mgs.forced_grant_count_delta,
    mgs.sample_interval_seconds
FROM collect.memory_grant_stats AS mgs
WHERE $__timeFilter(mgs.collection_time)
ORDER BY mgs.collection_time DESC;
"""

    # Upstream ref: LoadMemoryGrantSizingChart / LoadMemoryGrantActivityChart (MemoryContent.MemoryGrants.cs)
    y = subtab(
        panels,
        "Memory Grants",
        y,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Memory grants by resource pool",
                    targets=[target(grants_by_pool_sql)],
                    unit="decmbytes",
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Memory grant queue by resource pool",
                    targets=[target(grant_queue_sql)],
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Memory grant pressure",
                    sql=memory_grant_pressure_sql,
                    overrides=[
                        status_colors(
                            "pressure_level",
                            {
                                "CRITICAL - High wait queue": "red",
                                "HIGH - Moderate wait queue": "red",
                                "MEDIUM - Some grant waits": "orange",
                                "MEDIUM - Low available memory": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Memory grant stats (raw)",
                    sql=memory_grant_stats_sql,
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    top_clerks_sql = """
SELECT
    time = mcs.collection_time,
    metric = mcs.clerk_type,
    value = SUM(mcs.pages_kb) / 1024.0
FROM collect.memory_clerks_stats AS mcs
WHERE $__timeFilter(mcs.collection_time)
    AND mcs.clerk_type IN (
        SELECT TOP (8)
            m2.clerk_type
        FROM collect.memory_clerks_stats AS m2
        WHERE $__timeFilter(m2.collection_time)
        GROUP BY m2.clerk_type
        ORDER BY SUM(m2.pages_kb) DESC
    )
GROUP BY mcs.collection_time, mcs.clerk_type
ORDER BY mcs.collection_time;
"""
    # Upstream ref: RefreshMemoryClerksAsync / UpdateMemoryClerksSummaryPanel (MemoryContent.MemoryClerks.cs)
    y = subtab(
        panels,
        "Memory Clerks",
        y,
        [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Top memory clerks",
                    targets=[target(top_clerks_sql)],
                    unit="decmbytes",
                ),
            )
        ],
    )

    # Upstream ref: UpdatePlanCacheSummary (MemoryContent.PlanCache.cs), computed from the
    # latest collection instead of a chart series.
    panels.append(row("Plan Cache", y))
    # There's a 1-row gap between the stat pair and the charts below (hand-rolled in the
    # pre-flow() version too) - preserved here rather than tightened, since closing it is
    # an unrelated layout change outside this refactor's scope.
    y = flow(
        panels,
        y + 1,
        [
            (
                12,
                4,
                partial(
                    stat,
                    "Oldest plan age (now)",
                    sql="""
SELECT v = DATEDIFF(SECOND, MIN(pcs.oldest_plan_create_time), SYSDATETIME())
FROM collect.plan_cache_stats AS pcs
WHERE pcs.collection_time = (SELECT MAX(collection_time) FROM collect.plan_cache_stats);
""",
                    unit="s",
                    th=thresholds(("blue", None)),
                ),
            ),
            (
                12,
                4,
                partial(
                    stat,
                    "Total plans (now)",
                    sql="SELECT v = SUM(pcs.total_plans) FROM collect.plan_cache_stats AS pcs WHERE pcs.collection_time = (SELECT MAX(collection_time) FROM collect.plan_cache_stats);",
                    unit="short",
                    th=thresholds(("blue", None)),
                ),
            ),
        ],
    )
    # Upstream ref: report.plan_cache_bloat view
    plan_cache_bloat_sql = f"""
SELECT
    collection_time = {tz_col('pcb.collection_time')},
    total_plans = pcb.total_plans,
    single_use_plans = pcb.single_use_plans,
    single_use_pct = pcb.single_use_percent,
    total_cache_mb = pcb.total_cache_mb,
    single_use_mb = pcb.single_use_mb,
    single_use_cache_pct = pcb.single_use_cache_percent,
    pcb.bloat_level,
    pcb.recommendation
FROM report.plan_cache_bloat AS pcb;
"""
    y = flow(
        panels,
        y + 1,
        [
            (
                12,
                9,
                partial(
                    timeseries,
                    "Plan cache size by object type",
                    targets=[
                        target(
                            "SELECT time = pcs.collection_time, metric = pcs.objtype, value = SUM(CONVERT(float, pcs.total_size_mb)) FROM collect.plan_cache_stats AS pcs WHERE $__timeFilter(pcs.collection_time) GROUP BY pcs.collection_time, pcs.objtype ORDER BY pcs.collection_time;"
                        )
                    ],
                    unit="decmbytes",
                    stacked=True,
                ),
            ),
            (
                12,
                9,
                partial(
                    timeseries,
                    "Single-use vs multi-use plan cache",
                    targets=[
                        target(
                            "SELECT time = pcs.collection_time, single_use_mb = SUM(CONVERT(float, pcs.single_use_size_mb)), multi_use_mb = SUM(CONVERT(float, pcs.multi_use_size_mb)) FROM collect.plan_cache_stats AS pcs WHERE $__timeFilter(pcs.collection_time) GROUP BY pcs.collection_time ORDER BY pcs.collection_time;"
                        )
                    ],
                    unit="decmbytes",
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Plan cache bloat",
                    sql=plan_cache_bloat_sql,
                    overrides=[
                        status_colors(
                            "bloat_level",
                            {
                                "CRITICAL": "red",
                                "HIGH": "red",
                                "MEDIUM": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
        ],
    )

    # LoadMemoryPressureEventsChart (MemoryContent.MemoryPressure.cs) is this sub-tab's
    # headline visualization: hourly stacked bar counting sample rows
    # where process_indicator/system_indicator signal medium (==2) or severe (>=3)
    # pressure, matching sp_pressuredetector's thresholds.
    pressure_events_hourly_sql = """
SELECT
    time = $__timeGroup(mpe.event_time, '1h'),
    sql_server_medium = SUM(CASE WHEN mpe.process_indicator = 2 THEN 1 ELSE 0 END),
    sql_server_severe = SUM(CASE WHEN mpe.process_indicator >= 3 THEN 1 ELSE 0 END),
    operating_system_medium = SUM(CASE WHEN mpe.system_indicator = 2 THEN 1 ELSE 0 END),
    operating_system_severe = SUM(CASE WHEN mpe.system_indicator >= 3 THEN 1 ELSE 0 END)
FROM report.memory_pressure_events AS mpe
WHERE $__timeFilter(mpe.event_time)
    AND (mpe.process_indicator >= 2 OR mpe.system_indicator >= 2)
GROUP BY $__timeGroup(mpe.event_time, '1h')
ORDER BY 1;
"""
    pressure_events_sql = f"""
SELECT TOP (50)
    event_time = {tz_col('mpe.event_time')},
    mpe.notification,
    mpe.process_indicator,
    mpe.system_indicator,
    mpe.severity
FROM report.memory_pressure_events AS mpe
ORDER BY mpe.event_time DESC;
"""
    # Upstream ref: report.memory_pressure_indicators view
    pressure_composite_sql = f"""
SELECT
    collection_time = {tz_col('mpi.collection_time')},
    memory_utilization_pct = mpi.memory_utilization_percentage,
    buffer_pool_mb = mpi.buffer_pool_mb,
    plan_cache_mb = mpi.plan_cache_mb,
    available_os_mb = mpi.available_physical_memory_mb,
    grant_waiters = mpi.memory_grant_waiters,
    grant_timeouts = mpi.memory_grant_timeouts,
    grant_forced = mpi.memory_grant_forced,
    grant_available_mb = mpi.memory_grant_available_mb,
    resource_semaphore_wait_ms = mpi.resource_semaphore_wait_ms,
    pageiolatch_wait_ms = mpi.pageiolatch_wait_ms,
    mpi.pressure_level,
    mpi.recommendation
FROM report.memory_pressure_indicators AS mpi;
"""
    # Upstream ref: LoadMemoryPressureEventsChart (MemoryContent.MemoryPressure.cs)
    subtab(
        panels,
        "Memory Pressure Events",
        y,
        [
            (
                24,
                9,
                partial(
                    timeseries,
                    "Memory pressure events (hourly)",
                    targets=[target(pressure_events_hourly_sql)],
                    unit="short",
                    stacked=True,
                    bars=True,
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Memory pressure events",
                    sql=pressure_events_sql,
                    overrides=[
                        status_colors(
                            "severity",
                            {
                                "CRITICAL": "red",
                                "HIGH": "red",
                                "WARNING": "orange",
                                "MEDIUM": "orange",
                                "INFO": "blue",
                                "LOW": "green",
                            },
                        )
                    ],
                ),
            ),
            (
                12,
                9,
                partial(
                    table,
                    "Memory pressure composite",
                    sql=pressure_composite_sql,
                    overrides=[
                        status_colors(
                            "pressure_level",
                            {
                                "CRITICAL - Memory > 95%": "red",
                                "CRITICAL - High grant waiters": "red",
                                "HIGH - Memory > 90%": "red",
                                "HIGH - Grant waiters": "orange",
                                "MEDIUM - Buffer pool pressure": "orange",
                                "MEDIUM - Plan cache pressure": "orange",
                                "NORMAL": "green",
                            },
                        )
                    ],
                ),
            ),
        ],
    )

    return dashboard("perfmon-memory", "PerfMon · Memory", panels, [instance_var()])
