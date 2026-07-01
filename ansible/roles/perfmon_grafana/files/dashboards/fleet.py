from ._shared import *


def fleet():
    reset_id()

    # One SQL query returns one row with all health metrics for this instance.
    # The panel repeats per instance variable value; each copy queries that datasource.
    fleet_sql = """
WITH
cpu_stats AS (
    SELECT avg_cpu_pct = ISNULL(AVG(ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) * 1.0), 0)
    FROM collect.cpu_utilization_stats AS cus
    WHERE $__timeFilter(cus.collection_time)
),
scheduler_stats AS (
    SELECT
        avg_runnable   = ISNULL(AVG(css.avg_runnable_tasks_count * 1.0), 0),
        max_worker_pct = ISNULL(MAX(css.total_current_workers_count * 100.0 / NULLIF(css.max_workers_count, 0)), 0)
    FROM collect.cpu_scheduler_stats AS css
    WHERE $__timeFilter(css.collection_time)
),
grant_stats AS (
    SELECT max_grant_waiters = ISNULL(MAX(mgs.waiter_count), 0)
    FROM collect.memory_grant_stats AS mgs
    WHERE $__timeFilter(mgs.collection_time)
),
wait_agg AS (
    SELECT
        resource_sem_ms_min = ISNULL(
            SUM(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.collection_time END)), 0), 0),
        pageiolatch_ms_min  = ISNULL(
            SUM(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.collection_time END)), 0), 0),
        pagelatch_ms_min    = ISNULL(
            SUM(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.collection_time END)), 0), 0)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
      AND (ws.wait_type = N'RESOURCE_SEMAPHORE' OR ws.wait_type LIKE N'PAGEIOLATCH_%' OR ws.wait_type LIKE N'PAGELATCH_%')
),
file_io AS (
    SELECT io_stall_ms_min = ISNULL(
        SUM(fio.io_stall_ms_delta) * 1.0
        / NULLIF(DATEDIFF(MINUTE, MIN(fio.collection_time), MAX(fio.collection_time)), 0), 0)
    FROM collect.file_io_stats AS fio
    WHERE $__timeFilter(fio.collection_time)
),
blocking_agg AS (
    SELECT
        blocking      = ISNULL(SUM(bds.blocking_event_count_delta), 0),
        max_block_sec = ISNULL(MAX(bds.max_blocking_duration_ms_delta), 0) / 1000.0,
        deadlocks     = ISNULL(SUM(bds.deadlock_count_delta), 0)
    FROM collect.blocking_deadlock_stats AS bds
    WHERE $__timeFilter(bds.collection_time)
),
base AS (
    SELECT
        server_name         = (SELECT TOP 1 si.server_name FROM config.server_info AS si),
        cpu_pct             = cs.avg_cpu_pct,
        avg_runnable        = ss.avg_runnable,
        worker_pct          = ss.max_worker_pct,
        grant_waiters       = gs.max_grant_waiters,
        resource_sem_ms_min = wa.resource_sem_ms_min,
        io_pressure_ms_min  = fi.io_stall_ms_min + wa.pageiolatch_ms_min,
        pagelatch_ms_min    = wa.pagelatch_ms_min,
        blocking            = ba.blocking,
        max_block_sec       = ba.max_block_sec,
        deadlocks           = ba.deadlocks,
        unhealthy_cols      = (SELECT COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR'),
        age_min             = DATEDIFF(MINUTE, (SELECT MAX(cl.collection_time) FROM config.collection_log AS cl), SYSDATETIME())
    FROM cpu_stats AS cs
    CROSS JOIN scheduler_stats AS ss
    CROSS JOIN grant_stats AS gs
    CROSS JOIN wait_agg AS wa
    CROSS JOIN file_io AS fi
    CROSS JOIN blocking_agg AS ba
),
scored AS (
    SELECT *,
        severity_score =
            CASE WHEN cpu_pct > 85 THEN 3 WHEN cpu_pct > 50 THEN 1 ELSE 0 END
          + CASE WHEN worker_pct > 90 THEN 3 WHEN worker_pct > 70 THEN 1 ELSE 0 END
          + CASE WHEN grant_waiters > 5 THEN 3 WHEN grant_waiters > 1 THEN 1 ELSE 0 END
          + CASE WHEN resource_sem_ms_min > 100 THEN 3 WHEN resource_sem_ms_min > 10 THEN 1 ELSE 0 END
          + CASE WHEN io_pressure_ms_min > 500 THEN 3 WHEN io_pressure_ms_min > 50 THEN 1 ELSE 0 END
          + CASE WHEN pagelatch_ms_min > 100 THEN 2 WHEN pagelatch_ms_min > 10 THEN 1 ELSE 0 END
          + CASE WHEN avg_runnable > 20 THEN 2 WHEN avg_runnable > 5 THEN 1 ELSE 0 END
          + CASE WHEN blocking > 25 THEN 2 WHEN blocking > 1 THEN 1 ELSE 0 END
          + CASE WHEN deadlocks > 10 THEN 2 WHEN deadlocks > 1 THEN 1 ELSE 0 END
    FROM base
)
SELECT * FROM scored
WHERE (N'${filter}' = N'' OR server_name LIKE N'%' + N'${filter}' + N'%');
"""

    def col_ov(
        col,
        display_name=None,
        unit=None,
        width=None,
        th=None,
        link=None,
        decimals=None,
        display_mode=None,
        min_=None,
        max_=None,
    ):
        props = []
        if display_name:
            props.append({"id": "displayName", "value": display_name})
        if unit:
            props.append({"id": "unit", "value": unit})
        if width:
            props.append({"id": "custom.width", "value": width})
        if decimals is not None:
            props.append({"id": "decimals", "value": decimals})
        if th:
            props.append({"id": "color", "value": {"mode": "thresholds"}})
            props.append({"id": "thresholds", "value": th})
            if display_mode == "gradient-gauge":
                props.append(
                    {
                        "id": "custom.cellOptions",
                        "value": {
                            "type": "gauge",
                            "mode": "gradient",
                            "valueDisplayMode": "color",
                        },
                    }
                )
            elif display_mode == "color-text":
                props.append(
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}}
                )
            else:
                props.append(
                    {"id": "custom.cellOptions", "value": {"type": "color-background"}}
                )
        if min_ is not None:
            props.append({"id": "min", "value": min_})
        if max_ is not None:
            props.append({"id": "max", "value": max_})
        if link:
            props.append({"id": "links", "value": [link]})
        return {"matcher": {"id": "byName", "options": col}, "properties": props}

    drilldown = {
        "title": "Open instance overview",
        "url": "/d/perfmon-instance?${__url_time_range}&var-instance=${instance}",
        "targetBlank": False,
    }

    overrides = [
        col_ov("server_name", display_name="Instance", width=220, link=drilldown),
        col_ov(
            "cpu_pct",
            display_name="CPU %",
            unit="percent",
            width=140,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 50), ("red", 85)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        col_ov(
            "avg_runnable",
            display_name="Avg Runnable Tasks",
            width=170,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 1), ("red", 2)),
            display_mode="color-text",
        ),
        col_ov(
            "worker_pct",
            display_name="Workers % of max",
            width=150,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 70), ("red", 90)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        col_ov(
            "grant_waiters",
            display_name="Grant Waiters",
            width=130,
            th=thresholds(("transparent", None), ("#EAB839", 1), ("red", 5)),
            display_mode="color-background",
        ),
        col_ov(
            "resource_sem_ms_min",
            display_name="Res Sem Wait (ms/min)",
            width=180,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 10), ("red", 100)),
            display_mode="color-text",
        ),
        col_ov(
            "io_pressure_ms_min",
            display_name="IO Pressure (ms/min)",
            width=180,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 50), ("red", 500)),
            display_mode="color-text",
        ),
        col_ov(
            "pagelatch_ms_min",
            display_name="Page Contention (ms/min)",
            width=200,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 10), ("red", 100)),
            display_mode="color-text",
        ),
        col_ov(
            "blocking",
            display_name="Blocking (range)",
            width=140,
            th=thresholds(("transparent", None), ("#EAB839", 1), ("red", 10)),
            display_mode="color-text",
        ),
        col_ov(
            "max_block_sec",
            display_name="Max Block (sec)",
            width=140,
            decimals=1,
            th=thresholds(("transparent", None), ("#EAB839", 5), ("red", 30)),
            display_mode="color-text",
        ),
        col_ov(
            "deadlocks",
            display_name="Deadlocks (range)",
            width=150,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        col_ov(
            "unhealthy_cols",
            display_name="Collectors unhealthy",
            width=170,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        col_ov(
            "age_min",
            display_name="Collection age (min)",
            width=160,
            th=thresholds(("green", None), ("#EAB839", 3), ("red", 10)),
            display_mode="color-text",
        ),
        col_ov(
            "severity_score",
            display_name="Severity",
            width=100,
            th=thresholds(("transparent", None), ("#EAB839", 3), ("red", 6)),
            display_mode="color-background",
        ),
    ]

    panel = {
        "id": nid(),
        "type": "table",
        "title": "",
        "datasource": DS,
        "gridPos": {"h": 3, "w": 24, "x": 0, "y": 0},
        "repeat": "instance",
        "repeatDirection": "v",
        "maxPerRow": 1,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "left",
                    "cellOptions": {"type": "auto"},
                    "filterable": False,
                },
                "thresholds": thresholds(("green", None)),
            },
            "overrides": overrides,
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": [],
        },
        "targets": [target(fleet_sql, "table")],
    }

    filter_var = {
        "name": "filter",
        "label": "Filter instances",
        "type": "textbox",
        "query": "",
        "current": {"text": "", "value": ""},
        "hide": 0,
        "description": "Type to filter visible instances by name.",
    }
    return dashboard(
        "perfmon-fleet",
        "PerfMon · Fleet Overview",
        [panel],
        [instance_var(multi=True), filter_var],
        time_from="now-1h",
        refresh="1m",
    )


def fleet_static(instance_names):
    """Single-table fleet dashboard using Grafana's Mixed datasource + Merge transform.

    Each inventory hostname becomes one SQL target. Grafana merges all results into
    one table that can be sorted and filtered across all instances. Generated by
    passing --fleet-instances to this script.
    """
    reset_id()

    def instance_sql(name):
        ds_uid = f"perfmon-ds-{name}"
        return f"""
WITH
cpu_stats AS (
    SELECT avg_cpu_pct = ISNULL(AVG(ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) * 1.0), 0)
    FROM collect.cpu_utilization_stats AS cus
    WHERE $__timeFilter(cus.collection_time)
),
scheduler_stats AS (
    SELECT
        avg_runnable   = ISNULL(AVG(css.avg_runnable_tasks_count * 1.0), 0),
        max_worker_pct = ISNULL(MAX(css.total_current_workers_count * 100.0 / NULLIF(css.max_workers_count, 0)), 0)
    FROM collect.cpu_scheduler_stats AS css
    WHERE $__timeFilter(css.collection_time)
),
grant_stats AS (
    SELECT max_grant_waiters = ISNULL(MAX(mgs.waiter_count), 0)
    FROM collect.memory_grant_stats AS mgs
    WHERE $__timeFilter(mgs.collection_time)
),
wait_agg AS (
    SELECT
        resource_sem_ms_min = ISNULL(
            SUM(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type = N'RESOURCE_SEMAPHORE' THEN ws.collection_time END)), 0), 0),
        pageiolatch_ms_min  = ISNULL(
            SUM(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type LIKE N'PAGEIOLATCH_%' THEN ws.collection_time END)), 0), 0),
        pagelatch_ms_min    = ISNULL(
            SUM(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.wait_time_ms_delta END) * 1.0
            / NULLIF(DATEDIFF(MINUTE,
                MIN(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.collection_time END),
                MAX(CASE WHEN ws.wait_type LIKE N'PAGELATCH_%' THEN ws.collection_time END)), 0), 0)
    FROM collect.wait_stats AS ws
    WHERE $__timeFilter(ws.collection_time)
      AND (ws.wait_type = N'RESOURCE_SEMAPHORE' OR ws.wait_type LIKE N'PAGEIOLATCH_%' OR ws.wait_type LIKE N'PAGELATCH_%')
),
file_io AS (
    SELECT io_stall_ms_min = ISNULL(
        SUM(fio.io_stall_ms_delta) * 1.0
        / NULLIF(DATEDIFF(MINUTE, MIN(fio.collection_time), MAX(fio.collection_time)), 0), 0)
    FROM collect.file_io_stats AS fio
    WHERE $__timeFilter(fio.collection_time)
),
blocking_agg AS (
    SELECT
        blocking      = ISNULL(SUM(bds.blocking_event_count_delta), 0),
        max_block_sec = ISNULL(MAX(bds.max_blocking_duration_ms_delta), 0) / 1000.0,
        deadlocks     = ISNULL(SUM(bds.deadlock_count_delta), 0)
    FROM collect.blocking_deadlock_stats AS bds
    WHERE $__timeFilter(bds.collection_time)
),
base AS (
    SELECT
        instance_name       = N'{name}',
        ds_uid              = N'{ds_uid}',
        cpu_pct             = cs.avg_cpu_pct,
        avg_runnable        = ss.avg_runnable,
        worker_pct          = ss.max_worker_pct,
        grant_waiters       = gs.max_grant_waiters,
        resource_sem_ms_min = wa.resource_sem_ms_min,
        io_pressure_ms_min  = fi.io_stall_ms_min + wa.pageiolatch_ms_min,
        pagelatch_ms_min    = wa.pagelatch_ms_min,
        blocking            = ba.blocking,
        max_block_sec       = ba.max_block_sec,
        deadlocks           = ba.deadlocks,
        unhealthy_cols      = (SELECT COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR'),
        age_min             = DATEDIFF(MINUTE, (SELECT MAX(cl.collection_time) FROM config.collection_log AS cl), SYSDATETIME())
    FROM cpu_stats AS cs
    CROSS JOIN scheduler_stats AS ss
    CROSS JOIN grant_stats AS gs
    CROSS JOIN wait_agg AS wa
    CROSS JOIN file_io AS fi
    CROSS JOIN blocking_agg AS ba
),
scored AS (
    SELECT *,
        severity_score =
            CASE WHEN cpu_pct > 85 THEN 3 WHEN cpu_pct > 50 THEN 1 ELSE 0 END
          + CASE WHEN worker_pct > 90 THEN 3 WHEN worker_pct > 70 THEN 1 ELSE 0 END
          + CASE WHEN grant_waiters > 5 THEN 3 WHEN grant_waiters > 1 THEN 1 ELSE 0 END
          + CASE WHEN resource_sem_ms_min > 100 THEN 3 WHEN resource_sem_ms_min > 10 THEN 1 ELSE 0 END
          + CASE WHEN io_pressure_ms_min > 500 THEN 3 WHEN io_pressure_ms_min > 50 THEN 1 ELSE 0 END
          + CASE WHEN pagelatch_ms_min > 100 THEN 2 WHEN pagelatch_ms_min > 10 THEN 1 ELSE 0 END
          + CASE WHEN avg_runnable > 20 THEN 2 WHEN avg_runnable > 5 THEN 1 ELSE 0 END
          + CASE WHEN blocking > 25 THEN 2 WHEN blocking > 1 THEN 1 ELSE 0 END
          + CASE WHEN deadlocks > 10 THEN 2 WHEN deadlocks > 1 THEN 1 ELSE 0 END
    FROM base
)
SELECT * FROM scored;
"""

    targets = [
        {
            "refId": name,
            "datasource": {"type": "mssql", "uid": f"perfmon-ds-{name}"},
            "format": "table",
            "rawQuery": True,
            "rawSql": tz_sql(instance_sql(name)),
        }
        for name in instance_names
    ]

    def col_ov(
        col,
        display_name=None,
        unit=None,
        width=None,
        th=None,
        link=None,
        hidden=False,
        decimals=None,
        display_mode=None,
        min_=None,
        max_=None,
    ):
        props = []
        if display_name:
            props.append({"id": "displayName", "value": display_name})
        if unit:
            props.append({"id": "unit", "value": unit})
        if width:
            props.append({"id": "custom.width", "value": width})
        if hidden:
            props.append({"id": "custom.hidden", "value": True})
        if decimals is not None:
            props.append({"id": "decimals", "value": decimals})
        if th:
            props.append({"id": "color", "value": {"mode": "thresholds"}})
            props.append({"id": "thresholds", "value": th})
            if display_mode == "gradient-gauge":
                props.append(
                    {
                        "id": "custom.cellOptions",
                        "value": {
                            "type": "gauge",
                            "mode": "gradient",
                            "valueDisplayMode": "color",
                        },
                    }
                )
            elif display_mode == "color-text":
                props.append(
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}}
                )
            else:
                props.append(
                    {"id": "custom.cellOptions", "value": {"type": "color-background"}}
                )
        if min_ is not None:
            props.append({"id": "min", "value": min_})
        if max_ is not None:
            props.append({"id": "max", "value": max_})
        if link:
            props.append({"id": "links", "value": [link]})
        return {"matcher": {"id": "byName", "options": col}, "properties": props}

    drilldown = {
        "title": "Open instance overview",
        "url": "/d/perfmon-instance?${__url_time_range}&var-instance=${__data.fields.ds_uid}",
        "targetBlank": False,
    }

    overrides = [
        col_ov("ds_uid", hidden=True),
        col_ov("instance_name", display_name="Instance", width=220, link=drilldown),
        col_ov(
            "cpu_pct",
            display_name="CPU %",
            unit="percent",
            width=140,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 50), ("red", 85)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        col_ov(
            "avg_runnable",
            display_name="Avg Runnable Tasks",
            width=170,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 1), ("red", 2)),
            display_mode="color-text",
        ),
        col_ov(
            "worker_pct",
            display_name="Workers % of max",
            width=150,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 70), ("red", 90)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        col_ov(
            "grant_waiters",
            display_name="Grant Waiters",
            width=130,
            th=thresholds(("transparent", None), ("#EAB839", 1), ("red", 5)),
            display_mode="color-background",
        ),
        col_ov(
            "resource_sem_ms_min",
            display_name="Res Sem Wait (ms/min)",
            width=180,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 10), ("red", 100)),
            display_mode="color-text",
        ),
        col_ov(
            "io_pressure_ms_min",
            display_name="IO Pressure (ms/min)",
            width=180,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 50), ("red", 500)),
            display_mode="color-text",
        ),
        col_ov(
            "pagelatch_ms_min",
            display_name="Page Contention (ms/min)",
            width=200,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 10), ("red", 100)),
            display_mode="color-text",
        ),
        col_ov(
            "blocking",
            display_name="Blocking (range)",
            width=140,
            th=thresholds(("transparent", None), ("#EAB839", 1), ("red", 10)),
            display_mode="color-text",
        ),
        col_ov(
            "max_block_sec",
            display_name="Max Block (sec)",
            width=140,
            decimals=1,
            th=thresholds(("transparent", None), ("#EAB839", 5), ("red", 30)),
            display_mode="color-text",
        ),
        col_ov(
            "deadlocks",
            display_name="Deadlocks (range)",
            width=150,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        col_ov(
            "unhealthy_cols",
            display_name="Collectors unhealthy",
            width=170,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        col_ov(
            "age_min",
            display_name="Collection age (min)",
            width=160,
            th=thresholds(("green", None), ("#EAB839", 3), ("red", 10)),
            display_mode="color-text",
        ),
        col_ov(
            "severity_score",
            display_name="Severity",
            width=100,
            th=thresholds(("transparent", None), ("#EAB839", 3), ("red", 6)),
            display_mode="color-background",
        ),
    ]

    h = min(len(instance_names) + 4, 30)
    panel = {
        "id": nid(),
        "type": "table",
        "title": "Fleet health",
        "datasource": {"type": "datasource", "uid": "-- Mixed --"},
        "gridPos": {"h": h, "w": 24, "x": 0, "y": 0},
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "left",
                    "cellOptions": {"type": "auto"},
                    "filterable": True,
                },
                "thresholds": thresholds(("green", None)),
            },
            "overrides": overrides,
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": [{"displayName": "Severity", "desc": True}],
        },
        "targets": targets,
        "transformations": [{"id": "merge", "options": {}}],
    }

    return dashboard(
        "perfmon-fleet",
        "PerfMon · Fleet Overview",
        [panel],
        [],
        time_from="now-1h",
        refresh="1m",
    )
