from functools import partial

from ._shared import *


# Upstream tab: System Events (9 sub-tabs: Corruption Events, Contention Events, Severe Errors,
# I/O Issues, Scheduler Issues, Memory Conditions, CPU Tasks, Memory Broker, Memory Node OOM)
def system_events():
    panels = []
    th_count_red = thresholds(("green", None), ("red", 1))
    th_count_warn = thresholds(("transparent", None), ("#EAB839", 1))
    th_neutral = thresholds(("blue", None))

    # Upstream sub-tab: Corruption Events
    # Upstream ref: LoadCorruptionEventsCharts (SystemEventsContent.SystemHealth.cs)
    y = subtab(
        panels,
        "Corruption Events",
        0,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "Bad pages & dump requests",
                    targets=[
                        target(
                            "SELECT time = sh.collection_time, bad_pages_detected = CONVERT(float, sh.BadPagesDetected), bad_pages_fixed = CONVERT(float, sh.BadPagesFixed), interval_dump_requests = CONVERT(float, sh.intervalDumpRequests) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Access violations",
                    targets=[
                        target(
                            "SELECT time = sh.collection_time, access_violation_occurred = CONVERT(float, sh.isAccessViolationOccurred), write_access_violations = CONVERT(float, sh.writeAccessViolationCount) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "System health events",
                    sql=f"""
SELECT TOP (200)
    sh.id,
    collection_time = {tz_col('sh.collection_time')},
    event_time = {tz_col('sh.event_time')},
    sh.state,
    bad_pages_detected = sh.BadPagesDetected,
    bad_pages_fixed = sh.BadPagesFixed,
    is_access_violation_occurred = sh.isAccessViolationOccurred,
    write_access_violation_count = sh.writeAccessViolationCount,
    total_dump_requests = sh.totalDumpRequests,
    interval_dump_requests = sh.intervalDumpRequests,
    spinlock_backoffs = sh.spinlockBackoffs,
    sick_spinlock_type = sh.sickSpinlockType,
    sick_spinlock_type_after_av = sh.sickSpinlockTypeAfterAv,
    latch_warnings = sh.latchWarnings,
    non_yielding_tasks_reported = sh.nonYieldingTasksReported,
    page_faults = sh.pageFaults,
    system_cpu_utilization = sh.systemCpuUtilization,
    sql_cpu_utilization = sh.sqlCpuUtilization
FROM collect.HealthParser_SystemHealth AS sh
WHERE $__timeFilter(sh.collection_time)
ORDER BY sh.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Contention Events
    # Upstream ref: LoadContentionEventsCharts (SystemEventsContent.SystemHealth.cs)
    y = subtab(
        panels,
        "Contention Events",
        y,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "Non-yielding tasks & latch warnings",
                    targets=[
                        target(
                            "SELECT time = sh.collection_time, non_yielding_tasks = CONVERT(float, sh.nonYieldingTasksReported), latch_warnings = CONVERT(float, sh.latchWarnings), spinlock_backoffs = CONVERT(float, sh.spinlockBackoffs) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "SQL CPU vs system CPU",
                    targets=[
                        target(
                            "SELECT time = sh.collection_time, sql_cpu = sh.sqlCpuUtilization, system_cpu = sh.systemCpuUtilization FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                        )
                    ],
                    unit="percent",
                    max_=100,
                    bars=True,
                ),
            ),
            (
                24,
                8,
                partial(
                    timeseries,
                    "Sick spinlocks by type (top 5)",
                    targets=[target("""
SELECT
    time = sh.collection_time,
    metric = sh.sickSpinlockType,
    value = CONVERT(float, ISNULL(sh.spinlockBackoffs, 1))
FROM collect.HealthParser_SystemHealth AS sh
WHERE $__timeFilter(sh.collection_time)
    AND sh.sickSpinlockType IS NOT NULL
    AND sh.sickSpinlockType IN (
        SELECT TOP (5)
            s2.sickSpinlockType
        FROM collect.HealthParser_SystemHealth AS s2
        WHERE $__timeFilter(s2.collection_time)
            AND s2.sickSpinlockType IS NOT NULL
        GROUP BY s2.sickSpinlockType
        ORDER BY COUNT(*) DESC
    )
ORDER BY sh.collection_time;
""")],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Severe Errors
    # Upstream ref: LoadSevereErrorsChart (SystemEventsContent.SevereErrors.cs)
    y = subtab(
        panels,
        "Severe Errors",
        y,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "Severe errors per hour",
                    targets=[
                        target(
                            "SELECT time = $__timeGroup(se.collection_time, '1h'), errors = COUNT_BIG(*) FROM collect.HealthParser_SevereErrors AS se WHERE $__timeFilter(se.collection_time) GROUP BY $__timeGroup(se.collection_time, '1h') ORDER BY 1;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    table,
                    "Severe error log",
                    sql=f"""
SELECT TOP (200)
    se.id,
    collection_time = {tz_col('se.collection_time')},
    event_time = {tz_col('se.event_time')},
    se.error_number,
    se.severity,
    se.state,
    se.database_name,
    se.database_id,
    se.message
FROM collect.HealthParser_SevereErrors AS se
WHERE $__timeFilter(se.collection_time)
ORDER BY se.collection_time DESC;
""",
                    overrides=[
                        status_colors(
                            "severity",
                            {
                                "25": "red",
                                "24": "red",
                                "23": "red",
                                "22": "orange",
                                "21": "orange",
                                "20": "yellow",
                            },
                        )
                    ],
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: I/O Issues
    # Upstream ref: LoadIOIssuesChart / LoadLongestPendingIOChart (SystemEventsContent.IOIssues.cs)
    y = subtab(
        panels,
        "I/O Issues",
        y,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "I/O latch timeouts & long I/Os",
                    targets=[
                        target(
                            "SELECT time = io.collection_time, io_latch_timeouts = io.ioLatchTimeouts, interval_long_ios = io.intervalLongIos FROM collect.HealthParser_IOIssues AS io WHERE $__timeFilter(io.collection_time) ORDER BY io.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    table,
                    "I/O issue events",
                    sql=f"""
SELECT TOP (100)
    collection_time = {tz_col('io.collection_time')},
    event_time = {tz_col('io.event_time')},
    io.state,
    io_latch_timeouts = io.ioLatchTimeouts,
    interval_long_ios = io.intervalLongIos,
    total_long_ios = io.totalLongIos,
    longest_pending_request_duration_ms = io.longestPendingRequests_duration_ms,
    longest_pending_request_file_path = io.longestPendingRequests_filePath
FROM collect.HealthParser_IOIssues AS io
WHERE $__timeFilter(io.collection_time)
ORDER BY io.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Scheduler Issues
    # Upstream ref: UpdateSchedulerIssuesSummaryPanel (SystemEventsContent.SchedulerIssues.cs)
    y = subtab(
        panels,
        "Scheduler Issues",
        y,
        [
            (
                5,
                4,
                partial(
                    stat,
                    "Total issues (range)",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.event_time);",
                    unit="short",
                    th=th_neutral,
                ),
            ),
            (
                5,
                4,
                partial(
                    stat,
                    "Total non-yield time (range)",
                    sql="SELECT v = ISNULL(SUM(TRY_CAST(si.non_yielding_time_ms AS bigint)), 0) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.event_time);",
                    unit="ms",
                    th=th_neutral,
                ),
            ),
            (
                5,
                4,
                partial(
                    stat,
                    "Max non-yield time (range)",
                    sql="SELECT v = ISNULL(MAX(TRY_CAST(si.non_yielding_time_ms AS bigint)), 0) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.event_time);",
                    unit="ms",
                    th=th_neutral,
                ),
            ),
            (
                4,
                4,
                partial(
                    stat,
                    "Distinct schedulers affected",
                    sql="SELECT v = COUNT_BIG(DISTINCT si.scheduler_id) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.event_time);",
                    unit="short",
                    th=th_neutral,
                ),
            ),
            (
                5,
                4,
                partial(
                    stat,
                    "Offline scheduler events",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.event_time) AND si.is_online = 0;",
                    unit="short",
                    th=th_count_red,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Non-yielding scheduler time",
                    targets=[
                        target(
                            "SELECT time = si.collection_time, scheduler_id = si.scheduler_id, non_yielding_ms = CONVERT(float, si.non_yielding_time_ms) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.collection_time) ORDER BY si.collection_time;"
                        )
                    ],
                    unit="ms",
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    table,
                    "Scheduler issue events",
                    sql=f"""
SELECT TOP (100)
    si.id,
    collection_time = {tz_col('si.collection_time')},
    event_time = {tz_col('si.event_time')},
    si.scheduler_id,
    si.cpu_id,
    si.status,
    si.is_online,
    si.is_runnable,
    si.is_running,
    si.non_yielding_time_ms,
    si.thread_quantum_ms
FROM collect.HealthParser_SchedulerIssues AS si
WHERE $__timeFilter(si.collection_time)
ORDER BY si.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Memory Conditions
    # Upstream ref: LoadMemoryConditionsChart (SystemEventsContent.MemoryConditions.cs)
    y = subtab(
        panels,
        "Memory Conditions",
        y,
        [
            (
                24,
                8,
                partial(
                    timeseries,
                    "OOM exceptions (hourly)",
                    targets=[
                        target(
                            "SELECT time = $__timeGroup(mc.event_time, '1h'), oom_exceptions = SUM(mc.outOfMemoryExceptions) FROM collect.HealthParser_MemoryConditions AS mc WHERE $__timeFilter(mc.event_time) GROUP BY $__timeGroup(mc.event_time, '1h') ORDER BY 1;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Memory condition snapshots",
                    sql=f"""
SELECT TOP (100)
    mc.id,
    collection_time = {tz_col('mc.collection_time')},
    event_time = {tz_col('mc.event_time')},
    last_notification = mc.lastNotification,
    out_of_memory_exceptions = mc.outOfMemoryExceptions,
    is_any_pool_out_of_memory = mc.isAnyPoolOutOfMemory,
    process_out_of_memory_period = mc.processOutOfMemoryPeriod,
    mc.name,
    mc.available_physical_memory_gb,
    mc.available_virtual_memory_gb,
    mc.available_paging_file_gb,
    mc.working_set_gb,
    mc.percent_of_committed_memory_in_ws,
    mc.page_faults,
    mc.system_physical_memory_high,
    mc.system_physical_memory_low,
    mc.process_physical_memory_low,
    mc.process_virtual_memory_low,
    mc.vm_reserved_gb,
    mc.vm_committed_gb,
    mc.target_committed_gb,
    mc.current_committed_gb
FROM collect.HealthParser_MemoryConditions AS mc
WHERE $__timeFilter(mc.collection_time)
ORDER BY mc.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: CPU Tasks
    # Upstream ref: UpdateCPUTasksSummaryPanel (SystemEventsContent.CPUTasks.cs)
    y = subtab(
        panels,
        "CPU Tasks",
        y,
        [
            (
                6,
                4,
                partial(
                    stat,
                    "Unresolvable deadlocks (range)",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.event_time) AND ct.hasUnresolvableDeadlockOccurred = 1;",
                    unit="short",
                    th=th_count_red,
                ),
            ),
            (
                6,
                4,
                partial(
                    stat,
                    "Scheduler deadlocks (range)",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.event_time) AND ct.hasDeadlockedSchedulersOccurred = 1;",
                    unit="short",
                    th=th_count_red,
                ),
            ),
            (
                6,
                4,
                partial(
                    stat,
                    "Blocking events (range)",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.event_time) AND ct.didBlockingOccur = 1;",
                    unit="short",
                    th=th_neutral,
                ),
            ),
            (
                6,
                4,
                partial(
                    stat,
                    "Pending w/o blocking (range)",
                    sql="SELECT v = COUNT_BIG(*) FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.event_time) AND ISNULL(ct.pendingTasks, 0) > 0 AND ISNULL(ct.didBlockingOccur, 0) = 0;",
                    unit="short",
                    th=th_neutral,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Worker threads",
                    targets=[
                        target(
                            "SELECT time = ct.collection_time, workers_created = ct.workersCreated, workers_idle = ct.workersIdle, max_workers = ct.maxWorkers FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.collection_time) ORDER BY ct.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Pending tasks & oldest pending wait",
                    targets=[
                        target(
                            "SELECT time = ct.collection_time, pending_tasks = ct.pendingTasks, oldest_pending_wait_ms = ct.oldestPendingTaskWaitingTime FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.collection_time) ORDER BY ct.collection_time;"
                        )
                    ],
                    unit="ms",
                    bars=True,
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "CPU task events",
                    sql=f"""
SELECT TOP (200)
    ct.id,
    collection_time = {tz_col('ct.collection_time')},
    event_time = {tz_col('ct.event_time')},
    ct.state,
    max_workers = ct.maxWorkers,
    workers_created = ct.workersCreated,
    workers_idle = ct.workersIdle,
    tasks_completed_within_interval = ct.tasksCompletedWithinInterval,
    pending_tasks = ct.pendingTasks,
    oldest_pending_task_waiting_time = ct.oldestPendingTaskWaitingTime,
    has_unresolvable_deadlock_occurred = ct.hasUnresolvableDeadlockOccurred,
    has_deadlocked_schedulers_occurred = ct.hasDeadlockedSchedulersOccurred,
    did_blocking_occur = ct.didBlockingOccur
FROM collect.HealthParser_CPUTasks AS ct
WHERE $__timeFilter(ct.collection_time)
ORDER BY ct.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Memory Broker
    # Upstream ref: LoadMemoryBrokerChart (SystemEventsContent.MemoryBroker.cs)
    y = subtab(
        panels,
        "Memory Broker",
        y,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "Memory broker - currently allocated",
                    targets=[
                        target(
                            "SELECT time = mb.collection_time, metric = mb.broker, value = CONVERT(float, mb.currently_allocated) FROM collect.HealthParser_MemoryBroker AS mb WHERE $__timeFilter(mb.collection_time) ORDER BY mb.collection_time;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Memory broker - ratio & overall",
                    targets=[
                        target(
                            "SELECT time = mb.collection_time, memory_ratio = CONVERT(float, mb.memory_ratio), overall = CONVERT(float, mb.overall) FROM collect.HealthParser_MemoryBroker AS mb WHERE $__timeFilter(mb.collection_time) ORDER BY mb.collection_time;"
                        )
                    ],
                ),
            ),
            (
                24,
                8,
                partial(
                    table,
                    "Memory broker events",
                    sql=f"""
SELECT TOP (200)
    mb.id,
    collection_time = {tz_col('mb.collection_time')},
    event_time = {tz_col('mb.event_time')},
    mb.broker_id,
    mb.pool_metadata_id,
    mb.delta_time,
    mb.broker,
    mb.notification,
    mb.memory_ratio,
    mb.new_target,
    mb.overall,
    mb.rate,
    mb.currently_predicated,
    mb.currently_allocated,
    mb.previously_allocated
FROM collect.HealthParser_MemoryBroker AS mb
WHERE $__timeFilter(mb.collection_time)
ORDER BY mb.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    # Upstream sub-tab: Memory Node OOM
    # Upstream ref: UpdateMemoryStateIndicators (SystemEventsContent.MemoryNodeOOM.cs)
    # stat_grid shares a flow() line with "OOM events (hourly)" to match upstream's 2x2 layout.
    y = subtab(
        panels,
        "Memory Node OOM",
        y,
        [
            (
                12,
                8,
                partial(
                    timeseries,
                    "Memory node utilization %",
                    targets=[
                        target(
                            "SELECT time = hpmn.event_time, value = CONVERT(float, hpmn.memory_utilization_pct) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) AND hpmn.memory_utilization_pct IS NOT NULL ORDER BY hpmn.event_time;"
                        )
                    ],
                    unit="percent",
                    max_=100,
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "Memory node breakdown",
                    targets=[target("""
SELECT
    time = hpmn.event_time,
    target = CONVERT(float, hpmn.target_kb) / 1048576.0,
    committed = CONVERT(float, hpmn.committed_kb) / 1048576.0,
    total_page_file = CONVERT(float, hpmn.total_page_file_kb) / 1048576.0,
    available_page_file = CONVERT(float, hpmn.available_page_file_kb) / 1048576.0
FROM collect.HealthParser_MemoryNodeOOM AS hpmn
WHERE $__timeFilter(hpmn.event_time)
ORDER BY hpmn.event_time;
""")],
                    unit="decgbytes",
                ),
            ),
            (
                12,
                8,
                partial(
                    timeseries,
                    "OOM events (hourly)",
                    targets=[
                        target(
                            "SELECT time = $__timeGroup(hpmn.event_time, '1h'), oom_events = COUNT_BIG(*) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) GROUP BY $__timeGroup(hpmn.event_time, '1h') ORDER BY 1;"
                        )
                    ],
                    bars=True,
                ),
            ),
            (
                12,
                8,
                stat_grid(
                    [
                        {
                            "title": "Sys memory high (range)",
                            "sql": "SELECT v = COUNT_BIG(*) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) AND LOWER(hpmn.is_system_physical_memory_high) = 'true';",
                            "th": thresholds(("transparent", None), ("green", 1)),
                        },
                        {
                            "title": "Sys memory low (range)",
                            "sql": "SELECT v = COUNT_BIG(*) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) AND LOWER(hpmn.is_system_physical_memory_low) = 'true';",
                            "th": thresholds(("transparent", None), ("red", 1)),
                        },
                        {
                            "title": "Process memory low (range)",
                            "sql": "SELECT v = COUNT_BIG(*) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) AND LOWER(hpmn.is_process_physical_memory_low) = 'true';",
                            "th": thresholds(("transparent", None), ("red", 1)),
                        },
                        {
                            "title": "Process virtual memory low (range)",
                            "sql": "SELECT v = COUNT_BIG(*) FROM collect.HealthParser_MemoryNodeOOM AS hpmn WHERE $__timeFilter(hpmn.event_time) AND LOWER(hpmn.is_process_virtual_memory_low) = 'true';",
                            "th": th_count_warn,
                        },
                    ]
                ),
            ),
            (
                24,
                9,
                partial(
                    table,
                    "Memory node OOM events",
                    sql=f"""
SELECT TOP (100)
    hpmn.id,
    collection_time = {tz_col('hpmn.collection_time')},
    event_time = {tz_col('hpmn.event_time')},
    hpmn.node_id,
    hpmn.memory_node_id,
    hpmn.memory_utilization_pct,
    total_physical_memory_gb = CONVERT(decimal(18,2), hpmn.total_physical_memory_kb / 1048576.0),
    available_physical_memory_gb = CONVERT(decimal(18,2), hpmn.available_physical_memory_kb / 1048576.0),
    total_page_file_gb = CONVERT(decimal(18,2), hpmn.total_page_file_kb / 1048576.0),
    available_page_file_gb = CONVERT(decimal(18,2), hpmn.available_page_file_kb / 1048576.0),
    total_virtual_address_space_gb = CONVERT(decimal(18,2), hpmn.total_virtual_address_space_kb / 1048576.0),
    available_virtual_address_space_gb = CONVERT(decimal(18,2), hpmn.available_virtual_address_space_kb / 1048576.0),
    target_gb = CONVERT(decimal(18,2), hpmn.target_kb / 1048576.0),
    reserved_gb = CONVERT(decimal(18,2), hpmn.reserved_kb / 1048576.0),
    committed_gb = CONVERT(decimal(18,2), hpmn.committed_kb / 1048576.0),
    shared_committed_gb = CONVERT(decimal(18,2), hpmn.shared_committed_kb / 1048576.0),
    awe_gb = CONVERT(decimal(18,2), hpmn.awe_kb / 1048576.0),
    pages_gb = CONVERT(decimal(18,2), hpmn.pages_kb / 1048576.0),
    hpmn.failure_type,
    hpmn.failure_value,
    hpmn.resources,
    hpmn.factor_text,
    hpmn.factor_value,
    hpmn.last_error,
    hpmn.pool_metadata_id,
    hpmn.is_process_in_job,
    hpmn.is_system_physical_memory_high,
    hpmn.is_system_physical_memory_low,
    hpmn.is_process_physical_memory_low,
    hpmn.is_process_virtual_memory_low
FROM collect.HealthParser_MemoryNodeOOM AS hpmn
WHERE $__timeFilter(hpmn.collection_time)
ORDER BY hpmn.collection_time DESC;
""",
                    sort_by=[{"displayName": "collection_time", "desc": True}],
                ),
            ),
        ],
    )

    return dashboard(
        "perfmon-system-events", "PerfMon · System Events", panels, [instance_var()]
    )
