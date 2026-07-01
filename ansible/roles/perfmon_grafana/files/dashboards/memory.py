from ._shared import *


# Upstream tab: Memory
# 5 sub-tabs: Memory Overview, Memory Grants, Memory Clerks, Plan Cache, Memory Pressure Events
def memory():
    reset_id()
    panels = []
    th_wait = thresholds(("green", None), ("yellow", 1), ("red", 5))
    th_pressure = thresholds(("green", None), ("red", 1))

    # Upstream sub-tab: Memory Overview
    panels.append(row("Memory Overview", 0))
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
            "OS available memory (now)",
            "decmbytes",
            thresholds(("blue", None)),
            "SELECT TOP (1) v = available_physical_memory_mb FROM collect.memory_stats ORDER BY collection_time DESC;",
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
    for i, (title, unit, th, sql) in enumerate(stats):
        panels.append(stat(title, i * 4, 1, 4, 4, sql, unit, th))
    panels.append(
        timeseries(
            "Memory breakdown",
            0,
            5,
            12,
            9,
            [
                target(
                    "SELECT time = ms.collection_time, buffer_pool = ms.buffer_pool_mb, plan_cache = ms.plan_cache_mb, other = ms.other_memory_mb, committed_target = ms.committed_target_memory_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
                )
            ],
            unit="decmbytes",
            stacked=True,
        )
    )
    panels.append(
        timeseries(
            "OS memory (in use vs available)",
            12,
            5,
            12,
            9,
            [
                target(
                    "SELECT time = ms.collection_time, in_use = ms.physical_memory_in_use_mb, available = ms.available_physical_memory_mb FROM collect.memory_stats AS ms WHERE $__timeFilter(ms.collection_time) ORDER BY ms.collection_time;"
                )
            ],
            unit="decmbytes",
        )
    )
    panels.append(
        table(
            "Memory usage trends",
            0,
            14,
            24,
            9,
            """
SELECT TOP (25)
    mut.collection_time,
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
""",
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
        )
    )

    # Upstream sub-tab: Memory Grants
    panels.append(row("Memory Grants", 23))
    panels.append(
        timeseries(
            "Memory grants (all resource pools)",
            0,
            24,
            12,
            9,
            [
                target(
                    "SELECT time = mgs.collection_time, granted = SUM(mgs.granted_memory_mb), used = SUM(mgs.used_memory_mb), available = SUM(mgs.available_memory_mb) FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time ORDER BY mgs.collection_time;"
                )
            ],
            unit="decmbytes",
        )
    )
    panels.append(
        timeseries(
            "Memory grant queue",
            12,
            24,
            12,
            9,
            [
                target(
                    "SELECT time = mgs.collection_time, grantees = SUM(mgs.grantee_count), waiters = SUM(mgs.waiter_count), timeout_errors = SUM(mgs.timeout_error_count), forced_grants = SUM(mgs.forced_grant_count) FROM collect.memory_grant_stats AS mgs WHERE $__timeFilter(mgs.collection_time) GROUP BY mgs.collection_time ORDER BY mgs.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "Memory grant pressure",
            0,
            33,
            24,
            8,
            """
SELECT
    mgp.collection_time,
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
""",
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
        )
    )
    panels.append(
        table(
            "Memory grant stats (raw)",
            0,
            41,
            24,
            9,
            """
SELECT TOP (200)
    mgs.collection_id,
    mgs.collection_time,
    mgs.server_start_time,
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
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Memory Clerks
    panels.append(row("Memory Clerks", 50))
    panels.append(
        timeseries(
            "Top memory clerks",
            0,
            51,
            24,
            9,
            [
                target(
                    """
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
                )
            ],
            unit="decmbytes",
        )
    )
    # Upstream sub-tab: Plan Cache
    panels.append(row("Plan Cache", 60))
    panels.append(
        timeseries(
            "Plan cache size by object type",
            0,
            61,
            12,
            9,
            [
                target(
                    "SELECT time = pcs.collection_time, metric = pcs.objtype, value = SUM(CONVERT(float, pcs.total_size_mb)) FROM collect.plan_cache_stats AS pcs WHERE $__timeFilter(pcs.collection_time) GROUP BY pcs.collection_time, pcs.objtype ORDER BY pcs.collection_time;"
                )
            ],
            unit="decmbytes",
            stacked=True,
        )
    )
    panels.append(
        timeseries(
            "Single-use vs multi-use plan cache",
            12,
            61,
            12,
            9,
            [
                target(
                    "SELECT time = pcs.collection_time, single_use_mb = SUM(CONVERT(float, pcs.single_use_size_mb)), multi_use_mb = SUM(CONVERT(float, pcs.multi_use_size_mb)) FROM collect.plan_cache_stats AS pcs WHERE $__timeFilter(pcs.collection_time) GROUP BY pcs.collection_time ORDER BY pcs.collection_time;"
                )
            ],
            unit="decmbytes",
        )
    )
    panels.append(
        table(
            "Plan cache bloat",
            0,
            70,
            24,
            9,
            """
SELECT
    pcb.collection_time,
    total_plans = pcb.total_plans,
    single_use_plans = pcb.single_use_plans,
    single_use_pct = pcb.single_use_percent,
    total_cache_mb = pcb.total_cache_mb,
    single_use_mb = pcb.single_use_mb,
    single_use_cache_pct = pcb.single_use_cache_percent,
    pcb.bloat_level,
    pcb.recommendation
FROM report.plan_cache_bloat AS pcb;
""",
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
        )
    )
    # Upstream sub-tab: Memory Pressure Events
    panels.append(row("Memory Pressure Events", 79))
    panels.append(
        table(
            "Memory pressure events",
            0,
            80,
            12,
            9,
            """
SELECT TOP (50)
    mpe.event_time,
    mpe.notification,
    mpe.process_indicator,
    mpe.system_indicator,
    mpe.severity
FROM report.memory_pressure_events AS mpe
ORDER BY mpe.event_time DESC;
""",
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
        )
    )
    panels.append(
        table(
            "Memory pressure composite",
            12,
            80,
            12,
            9,
            """
SELECT
    mpi.collection_time,
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
""",
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
        )
    )

    return dashboard("perfmon-memory", "PerfMon · Memory", panels, [instance_var()])
