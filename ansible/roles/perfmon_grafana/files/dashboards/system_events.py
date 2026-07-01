from ._shared import *


# Upstream tab: System Events (9 sub-tabs: Corruption Events, Contention Events, Severe Errors,
# I/O Issues, Scheduler Issues, Memory Conditions, CPU Tasks, Memory Broker, Memory Node OOM)
def system_events():
    reset_id()
    panels = []

    # Upstream sub-tab: Corruption Events
    # Shows bad pages, dump requests, access violations from HealthParser_SystemHealth
    panels.append(row("Corruption Events", 0))
    panels.append(
        timeseries(
            "Bad pages & dump requests",
            0,
            1,
            12,
            8,
            [
                target(
                    "SELECT time = sh.collection_time, bad_pages_detected = CONVERT(float, sh.BadPagesDetected), bad_pages_fixed = CONVERT(float, sh.BadPagesFixed), interval_dump_requests = CONVERT(float, sh.intervalDumpRequests) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Access violations",
            12,
            1,
            12,
            8,
            [
                target(
                    "SELECT time = sh.collection_time, access_violation_occurred = CONVERT(float, sh.isAccessViolationOccurred), write_access_violations = CONVERT(float, sh.writeAccessViolationCount) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "System health events",
            0,
            9,
            24,
            9,
            """
SELECT TOP (200)
    sh.id,
    sh.collection_time,
    sh.event_time,
    sh.state,
    sh.BadPagesDetected,
    sh.BadPagesFixed,
    sh.isAccessViolationOccurred,
    sh.writeAccessViolationCount,
    sh.totalDumpRequests,
    sh.intervalDumpRequests,
    sh.spinlockBackoffs,
    sh.sickSpinlockType,
    sh.sickSpinlockTypeAfterAv,
    sh.latchWarnings,
    sh.nonYieldingTasksReported,
    sh.pageFaults,
    sh.systemCpuUtilization,
    sh.sqlCpuUtilization
FROM collect.HealthParser_SystemHealth AS sh
WHERE $__timeFilter(sh.collection_time)
ORDER BY sh.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Contention Events
    # Shows non-yielding tasks, latch warnings, spinlock backoffs, SQL CPU vs system CPU
    panels.append(row("Contention Events", 18))
    panels.append(
        timeseries(
            "Non-yielding tasks & latch warnings",
            0,
            19,
            12,
            8,
            [
                target(
                    "SELECT time = sh.collection_time, non_yielding_tasks = CONVERT(float, sh.nonYieldingTasksReported), latch_warnings = CONVERT(float, sh.latchWarnings), spinlock_backoffs = CONVERT(float, sh.spinlockBackoffs) FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "SQL CPU vs system CPU",
            12,
            19,
            12,
            8,
            [
                target(
                    "SELECT time = sh.collection_time, sql_cpu = sh.sqlCpuUtilization, system_cpu = sh.systemCpuUtilization FROM collect.HealthParser_SystemHealth AS sh WHERE $__timeFilter(sh.collection_time) ORDER BY sh.collection_time;"
                )
            ],
            unit="percent",
            max_=100,
        )
    )

    # Upstream sub-tab: Severe Errors
    panels.append(row("Severe Errors", 27))
    panels.append(
        timeseries(
            "Severe errors per hour",
            0,
            28,
            12,
            8,
            [
                target(
                    "SELECT time = $__timeGroup(se.collection_time, '1h'), errors = COUNT_BIG(*) FROM collect.HealthParser_SevereErrors AS se WHERE $__timeFilter(se.collection_time) GROUP BY $__timeGroup(se.collection_time, '1h') ORDER BY 1;"
                )
            ],
            bars=True,
        )
    )
    panels.append(
        table(
            "Severe error log",
            12,
            28,
            12,
            8,
            """
SELECT TOP (200)
    se.collection_time,
    se.event_time,
    se.error_number,
    se.severity,
    se.state,
    se.database_name,
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
        )
    )

    # Upstream sub-tab: I/O Issues
    panels.append(row("I/O Issues", 36))
    panels.append(
        timeseries(
            "I/O latch timeouts & long I/Os",
            0,
            37,
            12,
            8,
            [
                target(
                    "SELECT time = io.collection_time, io_latch_timeouts = io.ioLatchTimeouts, interval_long_ios = io.intervalLongIos FROM collect.HealthParser_IOIssues AS io WHERE $__timeFilter(io.collection_time) ORDER BY io.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "I/O issue events",
            12,
            37,
            12,
            8,
            """
SELECT TOP (100)
    io.collection_time,
    io.event_time,
    io.state,
    io.ioLatchTimeouts,
    io.intervalLongIos,
    io.totalLongIos,
    io.longestPendingRequests_duration_ms,
    io.longestPendingRequests_filePath
FROM collect.HealthParser_IOIssues AS io
WHERE $__timeFilter(io.collection_time)
ORDER BY io.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Scheduler Issues
    panels.append(row("Scheduler Issues", 45))
    panels.append(
        timeseries(
            "Non-yielding scheduler time",
            0,
            46,
            12,
            8,
            [
                target(
                    "SELECT time = si.collection_time, scheduler_id = si.scheduler_id, non_yielding_ms = CONVERT(float, si.non_yielding_time_ms) FROM collect.HealthParser_SchedulerIssues AS si WHERE $__timeFilter(si.collection_time) ORDER BY si.collection_time;"
                )
            ],
            unit="ms",
        )
    )
    panels.append(
        table(
            "Scheduler issue events",
            12,
            46,
            12,
            8,
            """
SELECT TOP (100)
    si.collection_time,
    si.event_time,
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
        )
    )

    # Upstream sub-tab: Memory Conditions
    panels.append(row("Memory Conditions", 54))
    panels.append(
        table(
            "Memory condition snapshots",
            0,
            55,
            24,
            9,
            """
SELECT TOP (100)
    mc.id,
    mc.collection_time,
    mc.event_time,
    mc.lastNotification,
    mc.outOfMemoryExceptions,
    mc.isAnyPoolOutOfMemory,
    mc.processOutOfMemoryPeriod,
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
        )
    )

    # Upstream sub-tab: CPU Tasks
    panels.append(row("CPU Tasks", 64))
    panels.append(
        timeseries(
            "Worker threads",
            0,
            65,
            12,
            8,
            [
                target(
                    "SELECT time = ct.collection_time, workers_created = ct.workersCreated, workers_idle = ct.workersIdle, max_workers = ct.maxWorkers FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.collection_time) ORDER BY ct.collection_time;"
                )
            ],
        )
    )
    panels.append(
        timeseries(
            "Pending tasks & oldest pending wait",
            12,
            65,
            12,
            8,
            [
                target(
                    "SELECT time = ct.collection_time, pending_tasks = ct.pendingTasks, oldest_pending_wait_ms = ct.oldestPendingTaskWaitingTime FROM collect.HealthParser_CPUTasks AS ct WHERE $__timeFilter(ct.collection_time) ORDER BY ct.collection_time;"
                )
            ],
            unit="ms",
        )
    )
    panels.append(
        table(
            "CPU task events",
            0,
            73,
            24,
            8,
            """
SELECT TOP (200)
    ct.collection_time,
    ct.event_time,
    ct.state,
    ct.maxWorkers,
    ct.workersCreated,
    ct.workersIdle,
    ct.pendingTasks,
    ct.oldestPendingTaskWaitingTime,
    ct.hasUnresolvableDeadlockOccurred,
    ct.hasDeadlockedSchedulersOccurred,
    ct.didBlockingOccur
FROM collect.HealthParser_CPUTasks AS ct
WHERE $__timeFilter(ct.collection_time)
ORDER BY ct.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    # Upstream sub-tab: Memory Broker
    panels.append(row("Memory Broker", 81))
    panels.append(
        timeseries(
            "Memory broker - currently allocated",
            0,
            82,
            12,
            8,
            [
                target(
                    "SELECT time = mb.collection_time, metric = mb.broker, value = CONVERT(float, mb.currently_allocated) FROM collect.HealthParser_MemoryBroker AS mb WHERE $__timeFilter(mb.collection_time) ORDER BY mb.collection_time;"
                )
            ],
        )
    )
    panels.append(
        table(
            "Memory broker events",
            12,
            82,
            12,
            8,
            """
SELECT TOP (200)
    mb.id,
    mb.collection_time,
    mb.event_time,
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
        )
    )

    # Upstream sub-tab: Memory Node OOM
    panels.append(row("Memory Node OOM", 90))
    panels.append(
        table(
            "Memory node OOM events",
            0,
            91,
            24,
            9,
            """
SELECT TOP (100)
    hpmn.collection_time,
    hpmn.event_time,
    hpmn.node_id,
    hpmn.memory_node_id,
    hpmn.memory_utilization_pct,
    total_physical_memory_gb = CONVERT(decimal(10,2), hpmn.total_physical_memory_kb / 1048576.0),
    available_physical_memory_gb = CONVERT(decimal(10,2), hpmn.available_physical_memory_kb / 1048576.0),
    committed_gb = CONVERT(decimal(10,2), hpmn.committed_kb / 1048576.0),
    hpmn.failure_type,
    hpmn.failure_value,
    hpmn.resources,
    hpmn.last_error,
    hpmn.is_system_physical_memory_low,
    hpmn.is_process_physical_memory_low,
    hpmn.is_process_virtual_memory_low
FROM collect.HealthParser_MemoryNodeOOM AS hpmn
WHERE $__timeFilter(hpmn.collection_time)
ORDER BY hpmn.collection_time DESC;
""",
            sort_by=[{"displayName": "collection_time", "desc": True}],
        )
    )

    return dashboard(
        "perfmon-system-events", "PerfMon · System Events", panels, [instance_var()]
    )
