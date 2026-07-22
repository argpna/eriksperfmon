"""Shared helpers for the PerformanceMonitor Grafana dashboard builder.

Imported by each per-dashboard module via 'from ._shared import *'.
Contains all panel builders, template functions, timezone helpers, and shared
constants. No dashboard-specific code belongs here.
"""

import pathlib
import re

_DASHBOARDS_ROOT = (
    pathlib.Path(__file__).resolve().parent.parent / "grafana" / "dashboards"
)
OUT = _DASHBOARDS_ROOT / "perfmon"

DS = {"type": "mssql", "uid": "${instance}"}

# Shared status-color mappings for report.collection_health / report.running_jobs tables,
# used by both instance_overview.py and collection.py.
HEALTH_STATUS_COLORS = {
    "FAILING": "red",
    "STALE": "orange",
    "WARNING": "yellow",
    "HEALTHY": "green",
    "NEVER_RUN": "red",
}

DURATION_STATUS_COLORS = {
    "LONG RUNNING": "red",
    "ABOVE AVERAGE": "orange",
    "NO HISTORY": "yellow",
    "NORMAL": "green",
}

_id = 0


def nid() -> int:
    """Return the next sequential panel id."""
    global _id
    _id += 1
    return _id


def reset_id() -> None:
    """Reset the panel id counter to 0 before building a new dashboard."""
    global _id
    _id = 0


# collect.* cumulative-vs-per-interval reference. Mixing these up produces wrong numbers
# with no SQL error - check here before summing or rate-computing any collect.* column
# across a time range.
#
# CUMULATIVE (since restart) - always use the *_delta column for range sums/trends, never
# the raw column, and never SUM() the raw column across rows: wait_stats, query_stats,
# procedure_stats, file_io_stats, memory_clerks_stats, perfmon_stats (cntr_value),
# latch_stats, spinlock_stats, memory_grant_stats (timeout_error_count/forced_grant_count
# only - its other columns are point-in-time gauges, see below).
#
# CUMULATIVE, SPECIAL CASE - blocking_deadlock_stats.blocking_event_count/
# total_blocking_duration_ms/max_blocking_duration_ms (raw AND *_delta) are re-aggregated
# over a trailing rolling 1-hour window on every collection cycle, so summing either one
# across multiple rows double-counts the same events up to ~60x. For a genuine range
# total/trend, query the raw event tables instead (blocking_BlockedProcessReport,
# deadlocks) bucketed by event_time - see blocking.py's comment at the top of its trends
# section. deadlock_count/total_deadlock_wait_time_ms/victim_count on the same table are
# NOT rolling-window and are safe to SUM() directly.
#
# CUMULATIVE, NO DELTA COLUMN PROVIDED - index_object_stats (user_seeks/user_scans/
# user_lookups/user_updates, leaf_*_count, row_lock_*/page_lock_*/page_latch_*/
# page_io_latch_* counters): sourced from sys.dm_db_index_usage_stats/
# sys.dm_db_index_operational_stats, cumulative since an unreliable reset point (see that
# table's own header comment in 02_create_tables.sql, upstream issue #1138). Always show
# as a raw lifetime total at the latest snapshot; never SUM() across rows or diff it
# yourself.
#
# ALREADY PER-INTERVAL / POINT-IN-TIME GAUGES - no *_delta exists or is needed; the raw
# value at the latest (or any) collection_time is already the correct reading: memory_stats,
# cpu_scheduler_stats, cpu_utilization_stats, tempdb_stats, plan_cache_stats, session_stats,
# waiting_tasks, running_jobs, database_size_stats (growth is size(t2) - size(t1) in the
# read layer, not a stored delta), server_properties.
#
# EVENT/RAW LOG TABLES - not gauges or counters; each row is one discrete occurrence, so the
# only valid range operation is COUNT(*)/GROUP BY over the event's own timestamp column:
# deadlocks, blocking_BlockedProcessReport, dmv_blocking_snapshots, deadlock_xml,
# blocked_process_xml, memory_pressure_events, trace_analysis, default_trace_events.
#
# QUERY STORE - query_store_data's avg_*/min_*/max_* columns are pre-aggregated by Query
# Store's own internal interval, not by our collector; use as-is (weight by
# count_executions when combining rows), never SUM() or delta them.
#
# Dedup bookkeeping tables, not metrics: query_stats_latest_hash,
# procedure_stats_latest_hash, query_store_data_latest_hash.


def tz_col(col: str) -> str:
    """Shift a server-local datetime2 column to UTC so Grafana's time axis is correct.

    collect.* timestamps use SYSDATETIME(), server-local wall clock, no offset.
    Grafana's MSSQL datasource treats returned datetime2 values as UTC.
    """
    return f"DATEADD(MINUTE, DATEDIFF(MINUTE, GETDATE(), GETUTCDATE()), {col})"


def _tz_macro(macro: str) -> str:
    """Shift a Grafana UTC time macro ($__timeFrom()/$__timeTo()) into server-local time.

    Shared building block for tz_filter/tz_from/tz_to/tz_prefilter, which all need the
    same UTC-bound-to-local-time shift, just assembled into different shapes.
    """
    shift = "DATEDIFF(MINUTE, GETUTCDATE(), GETDATE())"
    return f"DATEADD(MINUTE, {shift}, CONVERT(datetime2, {macro}))"


def tz_filter(col: str) -> str:
    """Timezone-aware replacement for $__timeFilter(col).

    $__timeFrom()/$__timeTo() expand to UTC datetime strings. The stored column holds
    server-local time. This shifts Grafana's UTC bounds into the server's local time
    before comparing. Works for any UTC offset including fractional-hour zones.
    """
    return (
        f"{col} >= {_tz_macro('$__timeFrom()')}"
        f" AND {col} < {_tz_macro('$__timeTo()')}"
    )


def tz_from() -> str:
    """Server-local-shifted $__timeFrom(), for comparisons tz_filter()'s shape doesn't fit.

    Only for columns confirmed server-local (SYSDATETIME()-sourced). Genuinely UTC
    columns (datetimeoffset-sourced) must use the bare $__timeFrom() macro instead.
    """
    return _tz_macro("$__timeFrom()")


def tz_to() -> str:
    """Server-local-shifted $__timeTo(). See tz_from() for when this does/doesn't apply."""
    return _tz_macro("$__timeTo()")


def tz_prefilter(col: str, pad_hours: int = 1) -> str:
    """Coarse range pre-filter on an indexed collection_time column.

    Mirrors upstream's BlockingPairRowQuery.cs pattern: narrow the index scan with
    collection_time padded pad_hours past the range, then filter the real column separately.
    """
    return (
        f"{col} >= DATEADD(HOUR, -{pad_hours}, {_tz_macro('$__timeFrom()')})"
        f" AND {col} <= DATEADD(HOUR, {pad_hours}, {_tz_macro('$__timeTo()')})"
    )


_TIMEGROUP_UNITS: dict[str, tuple[str, int]] = {
    "1m": ("MINUTE", 1),
    "5m": ("MINUTE", 5),
    "10m": ("MINUTE", 10),
    "15m": ("MINUTE", 15),
    "30m": ("MINUTE", 30),
    "1h": ("HOUR", 1),
    "6h": ("HOUR", 6),
    "12h": ("HOUR", 12),
    "1d": ("DAY", 1),
}


def _expand_timegroup(col: str, interval: str) -> str:
    """Expand $__timeGroup manually since its macro parser splits on the first comma
    inside the parens, which breaks when the column arg (tz_col(col)) has commas of its own.
    """
    shifted = tz_col(col)
    if interval in _TIMEGROUP_UNITS:
        unit, n = _TIMEGROUP_UNITS[interval]
        diff = f"DATEDIFF({unit}, 0, {shifted})"
        quotient = diff if n == 1 else f"{diff} / {n} * {n}"
        return f"DATEADD({unit}, {quotient}, 0)"
    return f"$__timeGroup({shifted}, '{interval}')"


# Columns that are already UTC and must never pass through the
# auto-patterns below - doing so would double-shift them.
_KNOWN_UTC_COLUMNS = frozenset({"utc_first_execution_time", "utc_last_execution_time"})


def _reject_utc_column(col: str) -> str:
    """Raise if col is a known already-UTC column; otherwise return it unchanged."""
    if col.split(".")[-1].lower() in _KNOWN_UTC_COLUMNS:
        raise ValueError(
            f"{col!r} is a known UTC-sourced column - tz_sql()'s "
            "auto-patterns assume server-local time and would double-shift it. "
            "Use tz_from()/tz_to() directly, or leave it unwrapped."
        )
    return col


def tz_sql(sql: str) -> str:
    """Patch all timezone-sensitive patterns in a panel SQL string.

    Applied automatically inside target() so every dashboard query is correct for
    non-UTC SQL Server instances without touching each string individually.
    Three patterns are replaced:
      $__timeFilter(col)     -> DATEADD-based filter, see tz_filter
      time = col,            -> DATEADD shift so Grafana time axis shows UTC values
      $__timeGroup(col, int) -> expanded to raw DATEADD/DATEDIFF with tz shift
    """
    sql = re.sub(
        r"\$__timeFilter\((\w+(?:\.\w+)?)\)",
        lambda m: tz_filter(_reject_utc_column(m.group(1))),
        sql,
    )
    sql = re.sub(
        r"\btime = (\w+(?:\.\w+)?),",
        lambda m: f"time = {tz_col(_reject_utc_column(m.group(1)))},",
        sql,
    )
    sql = re.sub(
        r"\$__timeGroup\((\w+(?:\.\w+)?),\s*'([^']+)'\)",
        lambda m: _expand_timegroup(_reject_utc_column(m.group(1)), m.group(2)),
        sql,
    )
    return sql


def read_uncommitted(sql: str) -> str:
    """Prefix sql with SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED, matching
    upstream's convention of reading collect.*/report.* tables (written by the collector
    every minute) without taking or waiting on locks. Skipped if the query already sets
    an isolation level itself (e.g. a conditional live-DMV branch that sets it inline).
    """
    if "ISOLATION LEVEL" in sql:
        return sql
    return f"SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n{sql}"


def target(sql: str, fmt: str = "time_series", ref: str = "A") -> dict:
    """Build a panel query target, applying the read-uncommitted and timezone SQL patches."""
    return {
        "refId": ref,
        "datasource": DS,
        "format": fmt,
        "rawQuery": True,
        "rawSql": read_uncommitted(tz_sql(sql)),
    }


def thresholds(*steps: tuple[str, float | None]) -> dict:
    """steps: (color, value) pairs; first value should be None."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def timeseries(
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: list[dict],
    unit: str = "short",
    stacked: bool = False,
    bars: bool = False,
    max_: float | None = None,
    fill: int = 12,
    axis_label: str | None = None,
) -> dict:
    """Build a timeseries panel."""
    custom = {
        "drawStyle": "bars" if bars else "line",
        "lineInterpolation": "smooth",
        "lineWidth": 1,
        "fillOpacity": 80 if bars else fill,
        "showPoints": "never",
        "spanNulls": True,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
    }
    if axis_label:
        custom["axisLabel"] = axis_label
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "unit": unit}
    if max_ is not None:
        defaults["max"] = max_
        defaults["min"] = 0
    return {
        "id": nid(),
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
                "calcs": [],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": targets,
    }


def text_panel(title, x, y, w, h, content):
    """Build a markdown text panel."""
    return {
        "id": nid(),
        "type": "text",
        "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def stat(
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    sql: str,
    unit: str,
    th: dict,
    links: list[dict] | None = None,
    decimals: int = 0,
    mappings: dict | None = None,
    overrides: list[dict] | None = None,
    show_values: bool = False,
    fields: str = "",
) -> dict:
    """Build a single-value stat panel."""
    p = {
        "id": nid(),
        "type": "stat",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "decimals": decimals,
                "thresholds": th,
                "unit": unit,
            },
            "overrides": overrides or [],
        },
        "options": {
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": fields,
                "values": show_values,
            },
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "textMode": "auto",
        },
        "targets": [target(sql, "table")],
    }
    if mappings:
        p["fieldConfig"]["defaults"]["mappings"] = [
            {
                "type": "value",
                "options": {
                    k: {"color": c, "index": i}
                    for i, (k, c) in enumerate(mappings.items())
                },
            }
        ]
    if links:
        p["links"] = links
    return p


def table(
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    sql: str,
    overrides: list[dict] | None = None,
    sort_by: list[dict] | None = None,
    description: str | None = None,
) -> dict:
    """Build a table panel."""
    panel = {
        "id": nid(),
        "type": "table",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "filterable": True,
                },
                "thresholds": thresholds(("green", None)),
            },
            "overrides": overrides or [],
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": sort_by or [],
        },
        "targets": [target(sql, "table")],
    }
    if description:
        panel["description"] = description
    return panel


def bargauge(
    title: str, x: int, y: int, w: int, h: int, sql: str, unit: str = "s"
) -> dict:
    """Build a horizontal bar gauge panel."""
    return {
        "id": nid(),
        "type": "bargauge",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "continuous-GrYlRd"},
                "thresholds": thresholds(("green", None)),
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "orientation": "horizontal",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "/^(?!.*name|.*type).*$/",
                "values": True,
            },
            "showUnfilled": True,
            "valueMode": "color",
            "namePlacement": "left",
        },
        "targets": [target(sql, "table")],
    }


def row(title: str, y: int, repeat: str | None = None) -> dict:
    """Build a collapsible row panel used as a section header."""
    r = {
        "id": nid(),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }
    if repeat:
        r["repeat"] = repeat
    return r


def strip_whoisactive_wrapper(col_sql: str) -> str:
    """Strip sp_WhoIsActive's '<?query --' / '--?>' XML-comment wrapper from a
    text column expression."""
    return (
        f"REPLACE(REPLACE({col_sql}, "
        "N'<?query --' + CHAR(13) + CHAR(10), N''), "
        "CHAR(13) + CHAR(10) + N'--?>', N'')"
    )


def strip_blitzlock_query_wrapper(col_sql: str) -> str:
    """Strip sp_BlitzLock's '<?query ' / '?>' XML-PI wrapper from collect.deadlocks'
    query column."""
    return f"LTRIM(RTRIM(REPLACE(REPLACE({col_sql}, N'<?query', N''), N'?>', N'')))"


def strip_blitzlock_object_names_wrapper(col_sql: str) -> str:
    """Strip sp_BlitzLock's per-object '<object>'/'</object>' XML wrapper from
    collect.deadlocks' object_names column."""
    return (
        f"LTRIM(RTRIM(REPLACE(REPLACE(REPLACE({col_sql}, "
        "N'</object><object>', N', '), N'<object>', N''), N'</object>', N'')))"
    )


def plan_xml_sql(
    table_name: str, alias: str, key_col: str, key_width: int, filter_sql: str
) -> str:
    """Distinct-plan-shape execution plan XML query, shared by query_history.py
    (table_name=collect.query_stats, key_col=query_plan_hash) and proc_history.py
    (table_name=collect.procedure_stats, key_col=plan_handle) - same two-phase
    index-seek-then-LOB-lookup pattern, parameterized on the plan-identity
    column and its varchar width for the CONVERT(..., 1) hex display.

    No upstream ref: upstream (GetQueryStatsPlanXmlByCollectionIdAsync etc.,
    DatabaseService.QueryPerformance.PlanXml.cs) fetches one plan XML on demand by a
    known collection_id; this instead lists every distinct plan shape seen in the
    selected time range, a Grafana-native history view upstream doesn't have.
    """
    return f"""
WITH plan_ranges AS (
    SELECT
        {alias}.{key_col},
        plan_first_seen      = {tz_col(f'MIN({alias}.collection_time)')},
        plan_last_seen       = {tz_col(f'MAX({alias}.collection_time)')},
        latest_time          = MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN {alias}.collection_time END),
        latest_collection_id = MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN {alias}.collection_id  END)
    FROM {table_name} AS {alias}
    WHERE {filter_sql}
    GROUP BY {alias}.{key_col}
    HAVING MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN 1 ELSE 0 END) = 1
)
SELECT
    {key_col} = CONVERT(varchar({key_width}), pr.{key_col}, 1),
    pr.plan_first_seen,
    pr.plan_last_seen,
    plan_xml = CAST(DECOMPRESS({alias}.query_plan_text) AS nvarchar(max))
FROM plan_ranges AS pr
JOIN {table_name} AS {alias}
    ON {alias}.collection_time = pr.latest_time
    AND {alias}.collection_id  = pr.latest_collection_id
ORDER BY pr.plan_last_seen DESC;
"""


def plan_params_sql(
    table_name: str, alias: str, key_col: str, key_width: int, filter_sql: str
) -> str:
    """Compiled-parameter-values query for the same distinct-plan-shape set as
    plan_xml_sql(), shared for the same reason - see that function's docstring.
    """
    return f"""
SET QUOTED_IDENTIFIER ON;
WITH plan_ranges AS (
    SELECT
        {alias}.{key_col},
        plan_last_seen       = {tz_col(f'MAX({alias}.collection_time)')},
        latest_time          = MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN {alias}.collection_time END),
        latest_collection_id = MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN {alias}.collection_id  END)
    FROM {table_name} AS {alias}
    WHERE {filter_sql}
    GROUP BY {alias}.{key_col}
    HAVING MAX(CASE WHEN {alias}.query_plan_text IS NOT NULL THEN 1 ELSE 0 END) = 1
),
plan_xml AS (
    SELECT
        pr.{key_col},
        pr.plan_last_seen,
        plan_xml = CONVERT(xml, CAST(DECOMPRESS({alias}.query_plan_text) AS nvarchar(max)))
    FROM plan_ranges AS pr
    JOIN {table_name} AS {alias}
        ON {alias}.collection_time = pr.latest_time
        AND {alias}.collection_id  = pr.latest_collection_id
)
SELECT
    {key_col}      = CONVERT(varchar({key_width}), px.{key_col}, 1),
    px.plan_last_seen,
    param_name      = p.value('@Column', 'nvarchar(128)'),
    data_type       = p.value('@ParameterDataType', 'nvarchar(128)'),
    compiled_value  = p.value('@ParameterCompiledValue', 'nvarchar(max)')
FROM plan_xml AS px
CROSS APPLY px.plan_xml.nodes('declare namespace sp="http://schemas.microsoft.com/sqlserver/2004/07/showplan"; //sp:ParameterList/sp:ColumnReference') AS t(p)
ORDER BY px.plan_last_seen DESC;
"""


def blocking_deadlock_1m_bucket_sql() -> str:
    """SQL for the 'Blocking events & deadlocks (1m buckets)' panel, shared by
    blocking.py and instance_overview.py so the two copies can't drift apart.

    No upstream ref: upstream's GetBlockedSessionTrendAsync/GetDeadlockTrendAsync
    (DatabaseService.QueryPerformance.Blocking.cs) bucket by raw collection_time from
    collect.waiting_tasks/collect.deadlocks instead. This buckets the raw event tables
    (blocking_BlockedProcessReport, deadlocks) by event_time into true 1-minute windows,
    since collect.blocking_deadlock_stats is a rolling-window aggregate that can't be
    summed across rows for a genuine range trend.
    """
    return f"""
WITH distinct_deadlocks AS (
    SELECT DISTINCT event_date
    FROM collect.deadlocks
    WHERE $__timeFilter(collection_time)
),
blocking_buckets AS (
    SELECT
        time = $__timeGroup(bg.event_time, '1m'),
        blocking_events = COUNT_BIG(*)
    FROM collect.blocking_BlockedProcessReport AS bg
    WHERE {tz_prefilter('bg.collection_time')} AND $__timeFilter(bg.event_time)
    GROUP BY $__timeGroup(bg.event_time, '1m')
),
deadlock_buckets AS (
    SELECT
        time = $__timeGroup(dd.event_date, '1m'),
        deadlocks = COUNT_BIG(*)
    FROM distinct_deadlocks AS dd
    GROUP BY $__timeGroup(dd.event_date, '1m')
)
SELECT
    time = COALESCE(bb.time, db.time),
    blocking_events = ISNULL(bb.blocking_events, 0),
    deadlocks = ISNULL(db.deadlocks, 0)
FROM blocking_buckets AS bb
FULL OUTER JOIN deadlock_buckets AS db
    ON bb.time = db.time
ORDER BY time;
"""


def flow(panels, y, items):
    """Lay out (w, h, factory) items left-to-right in a 24-column grid, wrapping to a
    new line whenever the next item would overflow, and stacking each line directly
    below the previous one. factory(x, y, w, h) returns a panel dict, or a list of
    panel dicts (e.g. a nested sub-grid built by stat_grid()). Returns the y just
    below the last line, so callers can chain sections without hand-computed offsets.
    """
    x = 0
    line_h = 0
    for w, h, factory in items:
        if x + w > 24:
            y += line_h
            x = 0
            line_h = 0
        built = factory(x, y, w, h)
        if built is not None:
            panels.extend(built if isinstance(built, list) else [built])
        x += w
        line_h = max(line_h, h)
    return y + line_h


def stat_grid(specs, cols=2):
    """flow() factory that places stat() panels in an internal cols-wide sub-grid
    filling the (x, y, w, h) envelope it's handed, so a block of small stat cards can
    share a flow() line with a taller chart. specs: dicts with title/sql/th and
    optionally unit (defaults to "short")."""

    def factory(x, y, w, h):
        rows = -(-len(specs) // cols)
        cell_w, cell_h = w // cols, h // rows
        return [
            stat(
                s["title"],
                x + (i % cols) * cell_w,
                y + (i // cols) * cell_h,
                cell_w,
                cell_h,
                s["sql"],
                s.get("unit", "short"),
                s["th"],
            )
            for i, s in enumerate(specs)
        ]

    return factory


def subtab(panels, title, y, items):
    """Shared sub-tab scaffold: a row() header followed by a flow() grid of
    charts/stats/table. items: list of (w, h, factory) per flow(). Returns the y
    coordinate for the next sub-tab's row()."""
    panels.append(row(title, y))
    return flow(panels, y + 1, items)


def reflow(panel, appended=False):
    """flow() factory that positions an already-built panel dict instead of
    constructing a new one - for panels whose id must be assigned (nid() fires
    at construction time, not at flow() layout time) before another panel
    references it in a data link. Pass appended=True if the panel was already
    added to panels[] at its construction point (to preserve array order for a
    forward id reference); the factory then only updates gridPos and returns
    None so flow() doesn't add it a second time."""

    def factory(x, y, w, h):
        panel["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
        return None if appended else panel

    return factory


def instance_var(multi=False):
    """Build the $instance datasource template variable shared by every dashboard."""
    return {
        "name": "instance",
        "label": "Instance",
        "type": "datasource",
        "query": "mssql",
        "regex": "/^PerfMon-/",
        "current": {},
        "options": [],
        "refresh": 1,
        "hide": 0,
        "multi": multi,
        "includeAll": multi,
        "sort": 1,
        "description": "Monitored SQL Server instance - one MSSQL datasource per instance. Type to search.",
    }


_FINOPS_DROPDOWN = {
    "asDropdown": True,
    "icon": "external link",
    "includeVars": True,
    "keepTime": True,
    "tags": ["finops"],
    "targetBlank": False,
    "title": "All FinOps Dashboards",
    "type": "dashboards",
    "url": "",
}

_DASHBOARDS_DROPDOWN = {
    "asDropdown": True,
    "icon": "external link",
    "includeVars": True,
    "keepTime": True,
    "tags": ["perfmon"],
    "targetBlank": False,
    "title": "All PerfMon Dashboards",
    "type": "dashboards",
    "url": "",
}

_FLEET_LINK = {
    "title": "Fleet Overview",
    "icon": "dashboard",
    "type": "link",
    "url": "/d/perfmon-fleet?${__url_time_range}",
    "keepTime": True,
    "includeVars": False,
    "targetBlank": False,
}


def _dashboard_base(
    uid, title, panels, variables, tags, links, time_from, refresh, graph_tooltip=1
):
    """Assemble the common dashboard JSON envelope shared by dashboard()/finops_dashboard()/detail_dashboard()."""
    return {
        "uid": uid,
        "title": title,
        "tags": tags,
        "timezone": "",
        "schemaVersion": 39,
        "editable": True,
        "graphTooltip": graph_tooltip,
        "fiscalYearStartMonth": 0,
        "time": {"from": time_from, "to": "now"},
        "refresh": refresh,
        "weekStart": "",
        "annotations": {"list": []},
        "links": links,
        "templating": {"list": variables},
        "panels": panels,
    }


def dashboard(
    uid, title, panels, variables, time_from="now-3h", refresh="1m", graph_tooltip=1
):
    """Build a standard PerfMon dashboard, adding the fleet/dashboards nav links (except on the fleet dashboard itself)."""
    is_fleet = uid == "perfmon-fleet"
    links = [] if is_fleet else [_FLEET_LINK, _DASHBOARDS_DROPDOWN, _FINOPS_DROPDOWN]
    tags = ["perfmon", "begin-here"] if is_fleet else ["perfmon"]
    return _dashboard_base(
        uid, title, panels, variables, tags, links, time_from, refresh, graph_tooltip
    )


def finops_dashboard(uid, title, panels, variables, time_from="now-24h", refresh="5m"):
    """Build a FinOps dashboard, tagged and linked into the FinOps dropdown."""
    links = [_FLEET_LINK, _FINOPS_DROPDOWN, _DASHBOARDS_DROPDOWN]
    return _dashboard_base(
        uid, title, panels, variables, ["finops"], links, time_from, refresh
    )


def detail_dashboard(uid, title, panels, variables, time_from="now-24h"):
    """Drill-down dashboard navigated to from data links.
    Tagged 'perfmon-detail' so it is excluded from the
    'All PerfMon Dashboards' dropdown. Default refresh is off."""
    tags = ["perfmon-detail", "nav-only"]
    links = [_FLEET_LINK, _DASHBOARDS_DROPDOWN]
    return _dashboard_base(uid, title, panels, variables, tags, links, time_from, "")


def col_gauge_bar(col, min_val=0, max_val=100, unit="percent"):
    """Table override: render a column as an inline bar gauge cell."""
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {"id": "min", "value": min_val},
            {"id": "max", "value": max_val},
            {"id": "unit", "value": unit},
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}},
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
        ],
    }


def status_colors(col, mapping):
    """Table override: colored background cell driven by value mappings."""
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {
                "id": "mappings",
                "value": [
                    {
                        "type": "value",
                        "options": {
                            k: {"color": c, "index": i}
                            for i, (k, c) in enumerate(mapping.items())
                        },
                    }
                ],
            },
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        ],
    }


def col_unit(col, unit, display_name=None):
    """Table override: set the display unit (and optionally label) of a column."""
    properties = [{"id": "unit", "value": unit}]
    if display_name:
        properties.append({"id": "displayName", "value": display_name})
    return {"matcher": {"id": "byName", "options": col}, "properties": properties}


def col_thresholds(col, *steps):
    """Table override: threshold-colored background on a numeric column.

    steps: (color, value) pairs passed to thresholds(); first value should be None.
    """
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {"id": "thresholds", "value": thresholds(*steps)},
            {"id": "color", "value": {"mode": "thresholds"}},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        ],
    }


INSTANCE_LINK = [
    {
        "title": "Open instance overview",
        "url": "/d/perfmon-instance?${__url_time_range}&var-instance=${instance}",
        "targetBlank": False,
    }
]


def text_var(name, label, default):
    """Grafana textbox variable - populated via URL parameter by data links."""
    return {
        "name": name,
        "label": label,
        "type": "textbox",
        "current": {"text": default, "value": default},
        "options": [{"text": default, "value": default}] if default else [],
        "hide": 0,
    }


def col_datalink(col, title, url):
    """Table field override that attaches a data link to a single column."""
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {
                "id": "links",
                "value": [{"title": title, "url": url, "targetBlank": False}],
            }
        ],
    }


def col_datalinks(col, links):
    """Table field override that attaches multiple data links to a single column.
    links is a list of (title, url) tuples; Grafana renders them as a menu on click."""
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [
            {
                "id": "links",
                "value": [
                    {"title": title, "url": url, "targetBlank": False}
                    for title, url in links
                ],
            }
        ],
    }
