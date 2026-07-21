# Upstream ref: LandingPage.xaml.cs / ServerHealthCard.xaml
# Builds the Fleet dashboards approximating upstream's live health checks
# from collect.* signals.
from ._shared import *

_FLEET_HEALTH_WINDOW_MINUTES = 15


def _fleet_window(col):
    """Trailing-window filter for a server-local collect.* timestamp column,
    fixed to _FLEET_HEALTH_WINDOW_MINUTES regardless of the dashboard's
    selected time range."""
    return f"{col} >= DATEADD(MINUTE, -{_FLEET_HEALTH_WINDOW_MINUTES}, SYSDATETIME())"


def _fleet_health_sql(identity_columns, final_select):
    """Shared severity-score CTE. identity_columns supplies the base CTE's
    leading identity columns - server_name, or instance_name/ds_uid;
    final_select supplies the trailing SELECT/WHERE against `scored`."""
    return f"""
WITH
cpu_stats AS (
    /* Avg CPU % over the trailing window. */
    SELECT avg_cpu_pct = ISNULL(AVG(ISNULL(cus.total_cpu_utilization, cus.sqlserver_cpu_utilization) * 1.0), 0)
    FROM collect.cpu_utilization_stats AS cus
    WHERE {_fleet_window('cus.sample_time')}
),
active_workers AS (
    /* Live snapshot, not time-windowed - collect.cpu_scheduler_stats only stores
       current_workers_count (idle-inclusive) */
    SELECT
        max_worker_pct = ISNULL(SUM(dos.active_workers_count) * 100.0 / NULLIF(MAX(osi.max_workers_count), 0), 0)
    FROM sys.dm_os_schedulers AS dos
    CROSS JOIN sys.dm_os_sys_info AS osi
    WHERE dos.status = N'VISIBLE ONLINE'
),
wait_agg AS (
    /* wait_stats is cumulative-since-restart; wait_time_ms_delta is per-interval.
       Wait families: poison-wait set only. */
    SELECT
        resource_sem_ms = ISNULL(SUM(CASE WHEN ws.wait_type IN (N'RESOURCE_SEMAPHORE', N'RESOURCE_SEMAPHORE_QUERY_COMPILE') THEN ws.wait_time_ms_delta END), 0),
        threadpool_ms   = ISNULL(SUM(CASE WHEN ws.wait_type = N'THREADPOOL' THEN ws.wait_time_ms_delta END), 0),
        elapsed_sec     = (SELECT CASE WHEN DATEDIFF(SECOND, MIN(w2.collection_time), MAX(w2.collection_time)) < 60 THEN 60
                                       ELSE DATEDIFF(SECOND, MIN(w2.collection_time), MAX(w2.collection_time)) END
                           FROM collect.wait_stats AS w2
                           WHERE {_fleet_window('w2.collection_time')})
    FROM collect.wait_stats AS ws
    WHERE {_fleet_window('ws.collection_time')}
      AND ws.wait_type IN (N'RESOURCE_SEMAPHORE', N'RESOURCE_SEMAPHORE_QUERY_COMPILE', N'THREADPOOL')
),
blocking_agg AS (
    /* blocking_event_count (raw+delta) double-counts, rolling window - count raw
       events instead. deadlock_count is already per-interval, not cumulative -
       SUM() raw, not delta. max_blocking_duration_ms: MAX() only. */
    SELECT
        blocking      = ISNULL(be.blocking_events, 0),
        max_block_sec = ISNULL(dl.max_block_ms, 0) / 1000.0,
        deadlocks     = ISNULL(dl.deadlocks, 0)
    FROM (
        SELECT
            max_block_ms = MAX(bds.max_blocking_duration_ms),
            deadlocks    = SUM(bds.deadlock_count)
        FROM collect.blocking_deadlock_stats AS bds
        WHERE {_fleet_window('bds.collection_time')}
    ) AS dl
    CROSS JOIN (
        SELECT blocking_events = COUNT_BIG(*)
        FROM collect.blocking_BlockedProcessReport AS bg
        WHERE {_fleet_window('bg.collection_time')} AND {_fleet_window('bg.event_time')}
    ) AS be
),
base AS (
    SELECT
        {identity_columns}
        cpu_pct         = cs.avg_cpu_pct,
        worker_pct      = aw.max_worker_pct,
        resource_sem_ws = ISNULL(wa.resource_sem_ms / 1000.0 / wa.elapsed_sec, 0),
        threadpool_ws   = ISNULL(wa.threadpool_ms / 1000.0 / wa.elapsed_sec, 0),
        blocking        = ba.blocking,
        max_block_sec   = ba.max_block_sec,
        deadlocks       = ba.deadlocks,
        /* config.collection_log uses its own shorter 5-min staleness window,
           distinct from _FLEET_HEALTH_WINDOW_MINUTES above. */
        unhealthy_cols  = (SELECT COUNT_BIG(DISTINCT cl.collector_name) FROM config.collection_log AS cl WHERE cl.collection_time >= DATEADD(MINUTE, -5, SYSDATETIME()) AND cl.collection_status = N'ERROR')
    FROM cpu_stats AS cs
    CROSS JOIN active_workers AS aw
    CROSS JOIN wait_agg AS wa
    CROSS JOIN blocking_agg AS ba
),
cats AS (
    /* Per-category severity 0/1/2, worst signal per category. _ws = avg waiting
       sessions. Collectors cap at warning, matching upstream's CollectorSeverity. */
    SELECT *,
        cpu_sev       = CASE WHEN cpu_pct > 90 THEN 2 WHEN cpu_pct > 80 THEN 1 ELSE 0 END,
        threads_sev   = CASE WHEN worker_pct > 90 OR threadpool_ws > 0.1 THEN 2
                              WHEN worker_pct > 70 OR threadpool_ws > 0.01 THEN 1 ELSE 0 END,
        memory_sev    = CASE WHEN resource_sem_ws > 0.1 THEN 2 WHEN resource_sem_ws > 0.01 THEN 1 ELSE 0 END,
        blocking_sev  = CASE WHEN blocking > 25 OR max_block_sec >= 60 THEN 2
                              WHEN blocking > 1 OR max_block_sec >= 10 THEN 1 ELSE 0 END,
        deadlock_sev  = CASE WHEN deadlocks >= 10 THEN 2 WHEN deadlocks >= 1 THEN 1 ELSE 0 END,
        collector_sev = CASE WHEN unhealthy_cols >= 1 THEN 1 ELSE 0 END
    FROM base
),
scored AS (
    /* severity_score: worst-dominates rank (10/critical + 1/warning) so one
       critical outranks any number of warnings. severity_level: worst category
       level itself (2/1/0). */
    SELECT *,
        severity_score =
            CASE cpu_sev       WHEN 2 THEN 10 ELSE cpu_sev       END
          + CASE threads_sev   WHEN 2 THEN 10 ELSE threads_sev   END
          + CASE memory_sev    WHEN 2 THEN 10 ELSE memory_sev    END
          + CASE blocking_sev  WHEN 2 THEN 10 ELSE blocking_sev  END
          + CASE deadlock_sev  WHEN 2 THEN 10 ELSE deadlock_sev  END
          + CASE collector_sev WHEN 2 THEN 10 ELSE collector_sev END,
        severity_level =
            CASE WHEN cpu_sev = 2 OR threads_sev = 2 OR memory_sev = 2
                   OR blocking_sev = 2 OR deadlock_sev = 2 THEN 2
                 WHEN cpu_sev = 1 OR threads_sev = 1 OR memory_sev = 1
                   OR blocking_sev = 1 OR deadlock_sev = 1
                   OR collector_sev = 1 THEN 1
                 ELSE 0 END
    FROM cats
)
{final_select}
"""


def _fleet_col_ov(
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
            props.append({"id": "custom.cellOptions", "value": {"type": "color-text"}})
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


def _fleet_metric_overrides():
    """Column overrides shared by both fleet dashboards."""
    return [
        _fleet_col_ov(
            "cpu_pct",
            display_name="CPU %",
            unit="percent",
            width=140,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 80), ("red", 90)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        _fleet_col_ov(
            "worker_pct",
            display_name="Active Workers %",
            width=150,
            decimals=0,
            th=thresholds(("blue", None), ("#EAB839", 70), ("red", 90)),
            display_mode="gradient-gauge",
            min_=0,
            max_=100,
        ),
        _fleet_col_ov(
            "threadpool_ws",
            display_name="Thread Pool (avg waiting)",
            width=190,
            decimals=2,
            th=thresholds(("green", None), ("#EAB839", 0.01), ("red", 0.1)),
            display_mode="color-text",
        ),
        _fleet_col_ov(
            "resource_sem_ws",
            display_name="Res Semaphore (avg waiting)",
            width=200,
            decimals=2,
            th=thresholds(("green", None), ("#EAB839", 0.01), ("red", 0.1)),
            display_mode="color-text",
        ),
        _fleet_col_ov(
            "blocking",
            display_name="Blocking (range)",
            width=140,
            th=thresholds(("green", None), ("#EAB839", 1), ("red", 25)),
            display_mode="color-text",
        ),
        _fleet_col_ov(
            "max_block_sec",
            display_name="Max Block (sec)",
            width=140,
            decimals=1,
            th=thresholds(("green", None), ("#EAB839", 10), ("red", 60)),
            display_mode="color-text",
        ),
        _fleet_col_ov(
            "deadlocks",
            display_name="Deadlocks (range)",
            width=150,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        _fleet_col_ov(
            "unhealthy_cols",
            display_name="Collectors unhealthy",
            width=170,
            th=thresholds(("transparent", None), ("red", 1)),
            display_mode="color-background",
        ),
        _fleet_col_ov(
            "severity_score",
            display_name="Severity",
            width=100,
            th=thresholds(("transparent", None), ("#EAB839", 1), ("red", 10)),
            display_mode="color-background",
        ),
    ]


_TABLE_COLUMNS = (
    "cpu_pct",
    "worker_pct",
    "threadpool_ws",
    "resource_sem_ws",
    "blocking",
    "max_block_sec",
    "deadlocks",
    "unhealthy_cols",
    "severity_score",
)


def _table_final_select(identity_cols, where=None):
    cols = ",\n    ".join(identity_cols + _TABLE_COLUMNS)
    where_sql = f"\nWHERE {where}" if where else ""
    return f"SELECT\n    {cols}\nFROM scored{where_sql};"


_DRILLDOWN_LINK = {
    "title": "Open instance overview",
    "url": "/d/perfmon-instance?${__url_time_range}&var-instance=${instance}",
    "targetBlank": False,
}


def fleet():
    # One SQL query returns one row with all health metrics for this instance.
    # The panel repeats per instance variable value; each copy queries that datasource.
    fleet_sql = _fleet_health_sql(
        identity_columns="server_name         = (SELECT TOP 1 si.server_name FROM config.server_info AS si),",
        final_select=_table_final_select(("server_name",)),
    )

    overrides = [
        _fleet_col_ov(
            "server_name", display_name="Instance", width=220, link=_DRILLDOWN_LINK
        ),
    ] + _fleet_metric_overrides()

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

    return dashboard(
        "perfmon-fleet",
        "PerfMon · Fleet Overview",
        [panel],
        [instance_var(multi=True)],
        time_from="now-1h",
        refresh="1m",
    )


_SEVERITY_MAPPINGS = [
    {
        "type": "value",
        "options": {
            "0": {"text": "OK", "index": 0},
            "1": {"text": "Warn", "index": 1},
            "2": {"text": "Critical", "index": 2},
        },
    }
]

_CARD_TILES = (
    ("cpu_pct", "CPU", "percent", 0, (("blue", None), ("#EAB839", 80), ("red", 90))),
    (
        "worker_pct",
        "Workers (now)",
        "percent",
        0,
        (("blue", None), ("#EAB839", 70), ("red", 90)),
    ),
    (
        "threadpool_ws",
        "Thread Pool",
        "short",
        2,
        (("green", None), ("#EAB839", 0.01), ("red", 0.1)),
    ),
    (
        "resource_sem_ws",
        "Semaphore",
        "short",
        2,
        (("green", None), ("#EAB839", 0.01), ("red", 0.1)),
    ),
    (
        "blocking",
        "Blocking",
        "short",
        0,
        (("green", None), ("#EAB839", 1), ("red", 25)),
    ),
    (
        "max_block_sec",
        "Max Block (s)",
        "short",
        1,
        (("green", None), ("#EAB839", 10), ("red", 60)),
    ),
    ("deadlocks", "Deadlocks", "short", 0, (("green", None), ("red", 1))),
    ("unhealthy_cols", "Collectors", "short", 0, (("green", None), ("#EAB839", 1))),
    (
        "severity_level",
        "Severity",
        "short",
        0,
        (("green", None), ("#EAB839", 1), ("red", 2)),
        _SEVERITY_MAPPINGS,
    ),
)


def _fleet_v2_final_select():
    """Trailing SELECT for v2 cards: a severity multiselect hides non-matching
    instances. :csv expands to comma-joined levels or "*" for All; matched
    against a comma-delimited string so an empty or "*" selection never
    produces IN ()."""
    card_cols = ",\n    ".join(col for col, *_ in _CARD_TILES)
    return f"""SELECT
    {card_cols}
FROM scored
WHERE '${{severity:csv}}' = '*' OR '${{severity:csv}}' = ''
   OR ',' + '${{severity:csv}}' + ',' LIKE '%,' + CAST(severity_level AS varchar(2)) + ',%';"""


def _v2_severity_var():
    """Multi-value severity-level picker (Critical/Warn/OK), All by default.
    Values are the severity_level integers so the SQL filter needs no lookup."""
    options = [("2", "Critical"), ("1", "Warn"), ("0", "OK")]
    return {
        "kind": "CustomVariable",
        "spec": {
            "name": "severity",
            "label": "Severity",
            "query": ", ".join(f"{text} : {value}" for value, text in options),
            "current": {"text": "All", "value": "$__all"},
            "options": [{"text": text, "value": value} for value, text in options],
            "multi": True,
            "includeAll": True,
            "allValue": "*",
            "allowCustomValue": False,
            "hide": "dontHide",
            "skipUrlSync": False,
            "description": "Show only instances at the selected severity levels; the "
            "rest are hidden entirely. Critical = at least one critical category, "
            "Warn = warnings only, OK = healthy.",
        },
    }


def _v2_variables():
    iv = instance_var(multi=True)
    return [
        {
            "kind": "DatasourceVariable",
            "spec": {
                "name": iv["name"],
                "label": iv["label"],
                "pluginId": iv["query"],
                "refresh": "onDashboardLoad",
                "regex": iv["regex"],
                "current": {"text": "", "value": ""},
                "options": [],
                "multi": True,
                "includeAll": True,
                "hide": "dontHide",
                "skipUrlSync": False,
                "allowCustomValue": True,
                "description": iv["description"],
            },
        },
        _v2_severity_var(),
    ]


def _v2_show_when_no_data_hidden():
    """Hide a repeated panel copy when its query has no data, so filtered-out
    instances disappear instead of showing 'No data'. Needs Grafana >= 12.4
    with dashboardNewLayouts."""
    return {
        "kind": "ConditionalRenderingGroup",
        "spec": {
            "visibility": "show",
            "condition": "and",
            "items": [{"kind": "ConditionalRenderingData", "spec": {"value": True}}],
        },
    }


def fleet_v2():
    """Dynamic fleet as a dashboard-schema-v2 resource (dashboard.grafana.app/v2beta1).
    One health card per $instance, in alphabetical order. Import via the k8s-style
    dashboards API; the classic /api/dashboards/db endpoint can't store it. Role gates
    the import on Grafana >= 12.4 with dashboardNewLayouts
    """
    fleet_sql = _fleet_health_sql(
        identity_columns="server_name         = (SELECT TOP 1 si.server_name FROM config.server_info AS si),",
        final_select=_fleet_v2_final_select(),
    )

    def _card_override(col, label, unit, dec, steps, *mappings):
        props = [
            {"id": "displayName", "value": label},
            {"id": "unit", "value": unit},
            {"id": "decimals", "value": dec},
            {"id": "thresholds", "value": thresholds(*steps)},
        ]
        if mappings:
            props.append({"id": "mappings", "value": mappings[0]})
        return {"matcher": {"id": "byName", "options": col}, "properties": props}

    card_overrides = [_card_override(*tile) for tile in _CARD_TILES]

    elements = {
        "fleet-card": {
            "kind": "Panel",
            "spec": {
                "id": nid(),
                # title interpolates per repeat copy - card header is the instance name
                "title": "$instance",
                "description": "",
                "links": [],
                "data": {
                    "kind": "QueryGroup",
                    "spec": {
                        "queries": [
                            {
                                "kind": "PanelQuery",
                                "spec": {
                                    "refId": "A",
                                    "hidden": False,
                                    "query": {
                                        "kind": "DataQuery",
                                        "group": "mssql",
                                        "version": "v0",
                                        "datasource": {"name": "${instance}"},
                                        "spec": {
                                            "format": "table",
                                            "rawQuery": True,
                                            "rawSql": read_uncommitted(
                                                tz_sql(fleet_sql)
                                            ),
                                        },
                                    },
                                },
                            }
                        ],
                        "transformations": [],
                        "queryOptions": {},
                    },
                },
                "vizConfig": {
                    "kind": "VizConfig",
                    "group": "stat",
                    "version": "",
                    "spec": {
                        "options": {
                            "reduceOptions": {
                                "calcs": ["lastNotNull"],
                                "fields": "",
                                "values": False,
                            },
                            "colorMode": "background",
                            "graphMode": "none",
                            "justifyMode": "auto",
                            "orientation": "auto",
                            "textMode": "value_and_name",
                        },
                        "fieldConfig": {
                            "defaults": {
                                "color": {"mode": "thresholds"},
                                "decimals": 0,
                                "thresholds": thresholds(("green", None)),
                                "links": [_DRILLDOWN_LINK],
                            },
                            "overrides": card_overrides,
                        },
                    },
                },
            },
        },
    }

    layout = {
        "kind": "AutoGridLayout",
        "spec": {
            "maxColumnCount": 6,
            "columnWidthMode": "custom",
            "columnWidth": 440,
            "rowHeightMode": "custom",
            "rowHeight": 460,
            "fillScreen": False,
            "items": [
                {
                    "kind": "AutoGridLayoutItem",
                    "spec": {
                        "element": {"kind": "ElementReference", "name": "fleet-card"},
                        "repeat": {"mode": "variable", "value": "instance"},
                        "conditionalRendering": _v2_show_when_no_data_hidden(),
                    },
                },
            ],
        },
    }

    return {
        "kind": "Dashboard",
        "apiVersion": "dashboard.grafana.app/v2beta1",
        # grafana.app/folder must match the folder uid the role provisions; the
        # import task overrides it from grafana_folder_uid at push time.
        "metadata": {
            "name": "perfmon-fleet",
            "annotations": {"grafana.app/folder": "perfmon"},
        },
        "spec": {
            "title": "PerfMon · Fleet Overview",
            "tags": ["perfmon", "begin-here"],
            "cursorSync": "Crosshair",
            "editable": True,
            "liveNow": False,
            "preload": False,
            "annotations": [],
            "links": [],
            "elements": elements,
            "layout": layout,
            "variables": _v2_variables(),
            "timeSettings": {
                "timezone": "",
                "from": "now-1h",
                "to": "now",
                "autoRefresh": "1m",
                "autoRefreshIntervals": [
                    "5s",
                    "10s",
                    "30s",
                    "1m",
                    "5m",
                    "15m",
                    "30m",
                    "1h",
                    "2h",
                    "1d",
                ],
                "hideTimepicker": False,
                "fiscalYearStartMonth": 0,
            },
        },
    }


def fleet_static(instance_names):
    """Single-table fleet dashboard: Mixed datasource + Merge transform, one SQL
    target per inventory hostname, merged into a sortable/filterable table.
    Generated via --fleet-instances."""

    def instance_sql(name):
        ds_uid = f"perfmon-ds-{name}"
        name_lit = name.replace("'", "''")
        ds_uid_lit = ds_uid.replace("'", "''")
        identity_columns = f"instance_name       = N'{name_lit}',\n        ds_uid              = N'{ds_uid_lit}',"
        return _fleet_health_sql(
            identity_columns, _table_final_select(("instance_name", "ds_uid"))
        )

    targets = [
        {
            "refId": name,
            "datasource": {"type": "mssql", "uid": f"perfmon-ds-{name}"},
            "format": "table",
            "rawQuery": True,
            "rawSql": read_uncommitted(tz_sql(instance_sql(name))),
        }
        for name in instance_names
    ]

    drilldown = {
        "title": "Open instance overview",
        "url": "/d/perfmon-instance?${__url_time_range}&var-instance=${__data.fields.ds_uid}",
        "targetBlank": False,
    }

    overrides = [
        _fleet_col_ov("ds_uid", hidden=True),
        _fleet_col_ov(
            "instance_name", display_name="Instance", width=220, link=drilldown
        ),
    ] + _fleet_metric_overrides()

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
