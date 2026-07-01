from ._shared import *


# Deadlock detail - navigated to from the Deadlock events table on the Locking dashboard.
# Refresh is off via detail_dashboard - XDL is fetched once on arrival, not on every tick.
def deadlock_detail():
    reset_id()
    panels = []

    id_filter = """
'${deadlock_id}' = ''
OR d.deadlock_group = (
    SELECT TOP (1) dl.deadlock_group
    FROM collect.deadlocks AS dl
    WHERE dl.deadlock_id = TRY_CAST('${deadlock_id}' AS bigint)
        AND $__timeFilter(dl.collection_time)
)"""

    panels.append(row("Participants", 0))
    panels.append(
        table(
            "Deadlock participants",
            0,
            1,
            24,
            11,
            f"""
SELECT
    d.event_date,
    d.database_name,
    d.ServerName,
    d.deadlock_type,
    d.spid,
    d.deadlock_group,
    d.status,
    d.isolation_level,
    d.owner_mode,
    d.waiter_mode,
    d.lock_mode,
    d.transaction_count,
    wait_ms = d.wait_time,
    d.wait_resource,
    d.priority,
    d.log_used,
    d.login_name,
    d.host_name,
    d.client_app,
    d.last_tran_started,
    d.last_batch_started,
    d.last_batch_completed,
    d.transaction_name,
    d.owner_waiter_type,
    d.owner_activity,
    d.owner_waiter_activity,
    d.owner_merging,
    d.owner_spilling,
    d.owner_waiting_to_close,
    d.waiter_waiter_type,
    d.waiter_owner_activity,
    d.waiter_waiter_activity,
    d.waiter_merging,
    d.waiter_spilling,
    d.waiter_waiting_to_close,
    d.client_option_1,
    d.client_option_2,
    query = CONVERT(nvarchar(max), LEFT(CONVERT(nvarchar(max), d.query), 500)),
    object_names = CONVERT(nvarchar(500), LEFT(CONVERT(nvarchar(max), d.object_names), 500))
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
    AND ({id_filter})
ORDER BY d.event_date DESC;
""",
            sort_by=[{"displayName": "event_date", "desc": True}],
        )
    )

    panels.append(row("Deadlock Graph (XDL)", 12))
    panels.append(
        table(
            "Deadlock graph (XDL)",
            0,
            13,
            24,
            12,
            f"""
SELECT TOP (1)
    d.event_date,
    d.database_name,
    d.deadlock_group,
    deadlock_graph_xdl = CONVERT(nvarchar(max), d.deadlock_graph)
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
    AND ({id_filter})
    AND d.deadlock_graph IS NOT NULL
ORDER BY d.event_date DESC;
""",
            description=(
                "Full XDL for this deadlock event. "
                "To export: click the panel menu (three dots, top-right) -> Inspect -> Data -> Download CSV. "
                "The deadlock_graph_xdl column contains the complete XML. "
                "Save the cell content with a .xdl extension and open in SSMS."
            ),
        )
    )

    return detail_dashboard(
        "perfmon-deadlock-detail",
        "PerfMon · Deadlock Detail",
        panels,
        [
            instance_var(),
            text_var("deadlock_id", "Deadlock ID"),
        ],
        time_from="now-24h",
    )
