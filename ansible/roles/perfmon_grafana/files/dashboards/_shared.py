"""Shared helpers for the PerformanceMonitor Grafana dashboard builder.

Imported by each per-dashboard module via 'from ._shared import *'.
Contains all panel builders, template functions, timezone helpers, and shared
constants. No dashboard-specific code belongs here.
"""

import pathlib
import re

_DASHBOARDS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "grafana" / "dashboards"
OUT = _DASHBOARDS_ROOT / "perfmon"

DS = {"type": "mssql", "uid": "${instance}"}

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def reset_id() -> None:
    global _id
    _id = 0


def tz_col(col: str) -> str:
    """Shift a server-local datetime2 column to UTC so Grafana's time axis is correct.

    collect.* timestamps use SYSDATETIME(), server-local wall clock, no offset.
    Grafana's MSSQL datasource treats returned datetime2 values as UTC.
    """
    return f"DATEADD(MINUTE, DATEDIFF(MINUTE, GETDATE(), GETUTCDATE()), {col})"


def tz_filter(col: str) -> str:
    """Timezone-aware replacement for $__timeFilter(col).

    $__timeFrom()/$__timeTo() expand to UTC datetime strings. The stored column holds
    server-local time. This shifts Grafana's UTC bounds into the server's local time
    before comparing. Works for any UTC offset including fractional-hour zones.
    """
    shift = "DATEDIFF(MINUTE, GETUTCDATE(), GETDATE())"
    return (
        f"{col} >= DATEADD(MINUTE, {shift}, CONVERT(datetime2, $__timeFrom()))"
        f" AND {col} < DATEADD(MINUTE, {shift}, CONVERT(datetime2, $__timeTo()))"
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
    # Grafana's $__timeGroup macro parser splits on the first comma inside the
    # parens. Passing tz_col(col) which contains DATEADD(MINUTE, DATEDIFF(...),
    # col) and therefore commas as the column argument causes the parser to read
    # part of the DATEADD expression as the interval string, producing a parsing
    # error
    # Fix: expand the macro to raw DATEADD/DATEDIFF SQL here so Grafana never
    # tries to parse the timezone expression as an interval.
    shifted = tz_col(col)
    if interval in _TIMEGROUP_UNITS:
        unit, n = _TIMEGROUP_UNITS[interval]
        diff = f"DATEDIFF({unit}, 0, {shifted})"
        quotient = diff if n == 1 else f"{diff} / {n} * {n}"
        return f"DATEADD({unit}, {quotient}, 0)"
    return f"$__timeGroup({shifted}, '{interval}')"


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
        lambda m: tz_filter(m.group(1)),
        sql,
    )
    sql = re.sub(
        r"\btime = (\w+(?:\.\w+)?),",
        lambda m: f"time = {tz_col(m.group(1))},",
        sql,
    )
    sql = re.sub(
        r"\$__timeGroup\((\w+(?:\.\w+)?),\s*'([^']+)'\)",
        lambda m: _expand_timegroup(m.group(1), m.group(2)),
        sql,
    )
    return sql


def target(sql: str, fmt: str = "time_series", ref: str = "A") -> dict:
    return {
        "refId": ref,
        "datasource": DS,
        "format": fmt,
        "rawQuery": True,
        "rawSql": tz_sql(sql),
    }


def thresholds(*steps) -> dict:
    """steps: (color, value) pairs; first value should be None."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def timeseries(
    title,
    x,
    y,
    w,
    h,
    targets,
    unit="short",
    stacked=False,
    bars=False,
    max_=None,
    fill=12,
    axis_label=None,
):
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
    return {
        "id": nid(),
        "type": "text",
        "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def stat(
    title,
    x,
    y,
    w,
    h,
    sql,
    unit,
    th,
    links=None,
    decimals=0,
    mappings=None,
    overrides=None,
    show_values=False,
    fields="",
):
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


def table(title, x, y, w, h, sql, overrides=None, sort_by=None, description=None):
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


def bargauge(title, x, y, w, h, sql, unit="s"):
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


def row(title, y, repeat=None):
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


def instance_var(multi=False):
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


def dashboard(
    uid, title, panels, variables, time_from="now-3h", refresh="1m", graph_tooltip=1
):
    is_fleet = uid == "perfmon-fleet"
    links = []
    if not is_fleet:
        links.append(_FLEET_LINK)
        links.append(_DASHBOARDS_DROPDOWN)
        links.append(_FINOPS_DROPDOWN)
    tags = ["perfmon", "begin-here"] if is_fleet else ["perfmon"]
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


def finops_dashboard(uid, title, panels, variables, time_from="now-24h", refresh="5m"):
    return {
        "uid": uid,
        "title": title,
        "tags": ["finops"],
        "timezone": "",
        "schemaVersion": 39,
        "editable": True,
        "graphTooltip": 1,
        "fiscalYearStartMonth": 0,
        "time": {"from": time_from, "to": "now"},
        "refresh": refresh,
        "weekStart": "",
        "annotations": {"list": []},
        "links": [_FLEET_LINK, _FINOPS_DROPDOWN, _DASHBOARDS_DROPDOWN],
        "templating": {"list": variables},
        "panels": panels,
    }


def detail_dashboard(uid, title, panels, variables, time_from="now-24h"):
    """Drill-down dashboard navigated to from data links.
    Tagged 'perfmon-detail' so it is excluded from the
    'All PerfMon Dashboards' dropdown. Default refresh is off."""
    return {
        "uid": uid,
        "title": title,
        "tags": ["perfmon-detail", "nav-only"],
        "timezone": "",
        "schemaVersion": 39,
        "editable": True,
        "graphTooltip": 1,
        "fiscalYearStartMonth": 0,
        "time": {"from": time_from, "to": "now"},
        "refresh": "",
        "weekStart": "",
        "annotations": {"list": []},
        "links": [_FLEET_LINK, _DASHBOARDS_DROPDOWN],
        "templating": {"list": variables},
        "panels": panels,
    }


def field_unit(col, unit):
    """Override the unit for a single named field."""
    return {
        "matcher": {"id": "byName", "options": col},
        "properties": [{"id": "unit", "value": unit}],
    }


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


def text_var(name, label, default=""):
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
