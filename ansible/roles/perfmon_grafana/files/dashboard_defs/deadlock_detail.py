from functools import partial

from ._shared import *


# Upstream ref: OpenDeadlockGraphAsync (ServerTab.Deadlock.cs), which opens
# GraphViewerWindow/DeadlockGraphControl on demand for a right-clicked/double-clicked
# deadlock row. Navigated to from the Deadlock events table on the Locking dashboard.
# Refresh is off via detail_dashboard - XDL is fetched once on arrival, not on every tick.
def deadlock_detail():
    panels = []

    # Combination of event_date + database_name + XDL hash for unique event key
    id_filter = """
${deadlock_id:sqlstring} = '*'
OR ${deadlock_id:sqlstring} = ''
OR EXISTS (
    SELECT 1
    FROM collect.deadlocks AS dl
    WHERE dl.deadlock_id = TRY_CAST(${deadlock_id:sqlstring} AS bigint)
        AND $__timeFilter(dl.collection_time)
        AND dl.event_date = d.event_date
        AND dl.database_name = d.database_name
        AND (
            (dl.deadlock_graph IS NULL AND d.deadlock_graph IS NULL)
            OR HASHBYTES('SHA2_256', CONVERT(nvarchar(max), dl.deadlock_graph))
                = HASHBYTES('SHA2_256', CONVERT(nvarchar(max), d.deadlock_graph))
        )
)"""

    # Upstream ref: GetDeadlocksAsync (DatabaseService.QueryPerformance.Blocking.cs),
    # includeGraph=false path - deadlock_graph is deliberately excluded here too, split
    # into xdl_sql below to match upstream's lazy on-demand graph fetch.
    participants_sql = f"""
SELECT
    event_date = {tz_col('d.event_date')},
    d.database_name,
    server_name = d.ServerName,
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
    last_tran_started = {tz_col('d.last_tran_started')},
    last_batch_started = {tz_col('d.last_batch_started')},
    last_batch_completed = {tz_col('d.last_batch_completed')},
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
    query = CONVERT(nvarchar(max), d.query),
    object_names = CONVERT(nvarchar(max), d.object_names)
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
    AND ({id_filter})
ORDER BY d.event_date DESC;
"""

    # Upstream ref: GetDeadlockGraphAsync (DatabaseService.QueryPerformance.Blocking.cs)
    xdl_sql = f"""
SELECT TOP (1)
    event_date = {tz_col('d.event_date')},
    d.database_name,
    d.deadlock_group,
    deadlock_graph_xdl = CONVERT(nvarchar(max), d.deadlock_graph)
FROM collect.deadlocks AS d
WHERE $__timeFilter(d.collection_time)
    AND ({id_filter})
    AND d.deadlock_graph IS NOT NULL
ORDER BY d.event_date DESC;
"""

    # Upstream ref: GetDeadlocksAsync (DatabaseService.QueryPerformance.Blocking.cs), includeGraph=false path
    y = subtab(
        panels,
        "Participants",
        0,
        [
            (
                24,
                11,
                partial(
                    table,
                    "Deadlock participants",
                    sql=participants_sql,
                    sort_by=[{"displayName": "event_date", "desc": True}],
                ),
            )
        ],
    )
    # Upstream ref: GetDeadlockGraphAsync (DatabaseService.QueryPerformance.Blocking.cs)
    subtab(
        panels,
        "Deadlock Graph (XDL)",
        y,
        [
            (
                24,
                12,
                partial(
                    table,
                    "Deadlock graph (XDL)",
                    sql=xdl_sql,
                    description=(
                        "Full XDL for this deadlock event. "
                        "To export: click the panel menu (three dots, top-right) -> Inspect -> Data -> Download CSV. "
                        "The deadlock_graph_xdl column contains the complete XML. "
                        "Save the cell content with a .xdl extension and open in SSMS."
                    ),
                ),
            )
        ],
    )

    return detail_dashboard(
        "perfmon-deadlock-detail",
        "PerfMon · Deadlock Detail",
        panels,
        [
            instance_var(),
            text_var("deadlock_id", "Deadlock ID", "*"),
        ],
        time_from="now-24h",
    )
