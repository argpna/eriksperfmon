# Grafana dashboards for Erik Darling's PerformanceMonitor

Grafana front-end for [erikdarlingdata/PerformanceMonitor](https://github.com/erikdarlingdata/PerformanceMonitor)
(Full Edition), plus a cross-platform installer/deployer that can scale regardless of the fleet size.

The dashboards read the same `collect.*` tables and `report.*` views as Erik's C# app. Grafana
requires a login with read access to the PerformanceMonitor database.

Screenshots of all dashboards can be viewed at: [argpna.github.io/eriksperfmon-demo](https://argpna.github.io/eriksperfmon-demo/)

## Dashboards

Dashboards are split into two groups: operational monitoring (PerfMon) and cost/efficiency
analysis (FinOps). Both groups share the `$instance` datasource variable and link to each other.

### PerfMon dashboards

| Dashboard | Description |
|---|---|
| **Fleet Overview** | **Always start here**. Health summary across all instances: CPU, thread, and memory pressure, blocking, deadlocks, and collection health. A per-category severity score highlights instances in trouble in the last 15 minutes. Click an instance to open its Overview, then navigate to the relevant dashboard to dig into the cause. |
| **Overview** | Stat bar, server info, CPU/memory/scheduler/sessions charts, daily summary, recommendations, DDL events, configuration history, collection health, running SQL Agent jobs |
| **Queries** | Query CPU trends, active query snapshots (sp_WhoIsActive), top queries by CPU/reads, procedure stats, parameter sensitivity detection, long-running patterns, Query Store |
| **Resource Metrics** | Wait stats, TempDB space and contention, file I/O latency and throughput, perfmon counters, session stats, latch and spinlock contention |
| **Locking** | Blocking and deadlock trends, current waits, blocked-process reports, blocking hierarchy, deadlock participants |
| **Memory** | Memory breakdown over time, grant queue depth, top memory clerks, plan cache bloat, memory pressure events |
| **System Events** | Corruption events, scheduler issues, severe errors, I/O issues, memory conditions from the system_health extended event session |
| **Collection Health** | Per-collector status, durations, row counts, error log, running SQL Agent jobs |
| **Query History** _(drill-down)_ | CPU, elapsed time, I/O, and execution trends for a single query plus full collection history and plan XML. Opened via data link from Queries; not in the top-level navigation. |
| **Procedure History** _(drill-down)_ | Same as Query History scoped to a stored procedure. Opened via data link from Queries; not in the top-level navigation. |
| **Deadlock Detail** _(drill-down)_ | Participants, victim, and XDL graph for a single deadlock event. Opened via data link from Locking; not in the top-level navigation. |
| **Wait Drill-Down** _(drill-down)_ | Time-series breakdown for a single wait type: trend, delta per sample, and per-session breakdown. Opened via data link from Resource Metrics; not in the top-level navigation. |

All per-instance PerfMon dashboards mirror the upstream PerformanceMonitor tab structure. Each
Grafana row maps to one upstream sub-tab so you can compare them side by side.

### FinOps dashboards

| Dashboard | Description |
|---|---|
| **Recommendations** | Cost-saving recommendations: unused indexes, idle databases, missing indexes, oversized allocations |
| **Server Inventory** | Server properties, edition, version, uptime, health score, collected metric counts |
| **Utilization** | CPU and memory utilization trends, provisioning efficiency, peak hours by day, health score |
| **Application Connections** | Connection patterns by application and login over the last 24 hours |
| **High Impact** | Queries with the highest cumulative CPU, I/O, or elapsed time across the collection window |
| **Index Analysis** | Missing indexes, duplicate indexes, and contended indexes with impact estimates ([not runnable as a panel](#features-with-no-grafana-equivalent), shows manual run instructions instead) |
| **Index Usage** | Per-index seek/scan/lookup/update counts; unused and write-only indexes flagged |
| **Locking & Contention** | Lock waits, deadlock trends, and top contended objects |
| **Database Resources** | Per-database CPU, I/O, memory, and log usage over the selected time range |
| **Database Sizes** | Current data and log file sizes per database |
| **Object Sizes & Growth** | Table and index sizes with row counts and recent growth |
| **Storage Growth** | Data and log file growth trends over time |
| **Optimization** | Idle databases (no activity in 7 days), TempDB pressure indicators, wait stats summary |

---

## Getting started - pick your path

| | Path |
|---|---|
| You have Grafana and PerformanceMonitor already installed | [Just the dashboards](#just-the-dashboards) |
| You want the installer and automation too (Requires [Ansible](https://docs.ansible.com)) | [Complete solution](#complete-solution) |
| You want to try it locally before committing | [Local demo](#local-demo) |

---

## Just the dashboards

Use this path if PerformanceMonitor is already installed and collecting on your SQL Server
instances and you have an existing Grafana deployment. The dynamic fleet works with no tooling
beyond the committed JSON files. The static fleet requires Python 3 to regenerate the fleet
dashboard with your instance list baked in.

### Prerequisites

- PerformanceMonitor Full Edition installed on each SQL Server. The SQL Agent job must be enabled
  and running. Data is collected every minute.
- Grafana 10 or later.
- A SQL/Windows authenticated login for Grafana on each instance with `SELECT` on the PerformanceMonitor
  schemas, and optionally `VIEW SERVER STATE`, `CONNECT ANY DATABASE`, `VIEW ANY DEFINITION`, and
  `SQLAgentReaderRole` in `msdb` for additional dashboard/alert coverage.

### Step 1: Create the Grafana reader login

Run this on each monitored SQL Server instance. Requires a login that can create logins/users
and grant permissions: sysadmin, or securityadmin plus `db_owner` on `PerformanceMonitor`
and `msdb`. Replace `GrafanaReaderPass` with a password of your choice.
(See [Complete solution](#complete-solution) section for automation options)

```sql
/* sql auth login and server role. grafana mssql datasource also accepts win-auth,
but require setting up krb5.conf */
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'grafana_reader')
    CREATE LOGIN grafana_reader
        WITH PASSWORD = N'GrafanaReaderPass', CHECK_POLICY = ON;

/* role based access - server role */
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'grafana_reader_role' AND type = 'R')
    CREATE SERVER ROLE grafana_reader_role;

/* for reading sys.dm_exec_requests, sys.dm_os_ring_buffers etc */
GRANT VIEW SERVER STATE TO grafana_reader_role;

/* for reading sys.tables, sys.indexes etc */
GRANT CONNECT ANY DATABASE TO grafana_reader_role;
GRANT VIEW ANY DEFINITION TO grafana_reader_role;

ALTER SERVER ROLE grafana_reader_role ADD MEMBER grafana_reader;

/* database user, database role, and schema permissions */
USE [PerformanceMonitor];

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'grafana_reader')
    CREATE USER grafana_reader FOR LOGIN grafana_reader;

/* role based access - database role */
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'grafana_reader_role' AND type = 'R')
    CREATE ROLE grafana_reader_role AUTHORIZATION dbo;

GRANT SELECT ON SCHEMA::collect TO grafana_reader_role;
GRANT SELECT ON SCHEMA::report  TO grafana_reader_role;
GRANT SELECT ON SCHEMA::config  TO grafana_reader_role;

ALTER ROLE grafana_reader_role ADD MEMBER grafana_reader;

/* to alert on collection job failures */
USE [msdb];

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'grafana_reader')
    CREATE USER grafana_reader FOR LOGIN grafana_reader;

EXEC sp_addrolemember N'SQLAgentReaderRole', N'grafana_reader';
```

### Step 2: Add Grafana datasources

Add one **Microsoft SQL Server** datasource in Grafana for each SQL Server instance.

> [!IMPORTANT]
> **Datasource naming is required.** Every dashboard uses an instance variable with the regex
> `^PerfMon-`. Datasources must be named `PerfMon-<anything>` or they will not appear in the
> instance dropdown and the Fleet Overview will show nothing.

| Grafana setting | Value |
|---|---|
| Name | `PerfMon-<your-identifier>` - for example `PerfMon-sql01` |
| Host | `hostname:port#` |
| Database | `PerformanceMonitor` |
| TLS/SSL mode | match your SQL Server configuration |
| Authentication | `SQL Server Authentication` (default), or `Windows AD: Username + password` for the AD login alternative above - requires a `krb5.conf` on the Grafana host pointing at the domain's KDC |
| User | `grafana_reader` (SQL auth), or the AD principal's UPN for Kerberos, e.g. `svc_grafana_reader@LAB.INTERNAL` - note this is the UPN form, a different string than the down-level name used in Step 1's `CREATE LOGIN` |
| Password | the password you set in Step 1 (SQL auth), or the AD principal's domain password (Kerberos) |
| Min time interval | `1m` |

Save and **Test** each datasource before importing dashboards. A red error at this stage is almost
always a hostname, port, firewall, or wrong database name issue.

### Step 3: Import the dashboards

Download the JSON files from [ansible/roles/perfmon_grafana/files/grafana/dashboards/perfmon](ansible/roles/perfmon_grafana/files/grafana/dashboards/perfmon) and import them in Grafana via **Dashboards - Import**. The dashboards
link to each other by UID and navigation links will not work unless all are present. The exception
is `fleet-overview-v2.json`, a schema-v2 resource the import page cannot read -
see [Fleet Overview](#fleet-overview) below for when and how to import it instead of the classic
fleet file.

After importing, open any dashboard and select an instance from the **Instance** dropdown at the
top. If the dropdown is empty, go back to Step 2 and confirm the datasource name starts with
`PerfMon-`.

### Fleet Overview

The Fleet Overview has two modes. The default JSON ships as **dynamic fleet**.

**Dynamic fleet (default)**

Discovers instances automatically from all datasources matching `^PerfMon-`. It works out of
the box as long as your datasources are named correctly. Each instance gets its own panel row
showing its health metrics and a color-coded severity score computed over the selected time range.

The dynamic fleet ships as two files with the same dashboard UID - import the one
matching your Grafana:

- `fleet-overview.json` - classic dashboard JSON for Grafana version < 12.4, imported like every
  other dashboard.

- `fleet-overview-v2.json` - dashboard schema v2. One health card per instance, arranged
  in the same card grid the upstream desktop app's landing page uses: every signal that
  feeds the severity score is a colored tile, with the severity score in the last tile.
  Click a card to open the instance. Requires Grafana >= 12.4 with the
  `dashboardNewLayouts` feature toggle enabled (`GF_FEATURE_TOGGLES_ENABLE=dashboardNewLayouts`).
  Import it via the dashboards HTTP API, not the UI import page:

  ```bash
  curl -X PUT -H "Authorization: Bearer $GRAFANA_API_KEY" -H "Content-Type: application/json" \
    -d @fleet-overview-v2.json \
    "$GRAFANA_URL/apis/dashboard.grafana.app/v2beta1/namespaces/default/dashboards/perfmon-fleet"
  ```
> [!NOTE]
> Schema v2 provides better UX because the name/severity filters can hide a filtered-out
> instance's card entirely. On the other hand, classic repeated panels can only empty a card's
> data, not remove/hide the panel itself.

> [!IMPORTANT]
> **Limitation**: panels are ordered alphabetically by datasource name. There is no way to sort
> the fleet by severity score in this mode - the v2 severity filter hides instances but cannot
> reorder them. Use the static fleet for severity-sorted ordering.

**Static fleet (sortable, requires extra setup)**

A single merged table with all instances sorted by severity score descending, so the most
troubled instances surface at the top. This is the recommended mode for large fleets where
scrolling through alphabetically-ordered panels is impractical.

Run the builder with your instance names to generate a static fleet JSON, then import it into
Grafana the same way as the other dashboards (replacing the existing Fleet Overview):

```bash
python3 ansible/roles/perfmon_grafana/files/build-dashboards.py \
  --output grafana/dashboards \
  --fleet-instances sql01,sql02,sql03

# for a list of instances in a text file
python3 ansible/roles/perfmon_grafana/files/build-dashboards.py \
  --output grafana/dashboards \
  --fleet-instances @instances.txt
```

The static fleet also requires datasource UIDs to follow the pattern `perfmon-ds-<hostname>`.
Grafana does not expose UIDs in the UI by default. Set them via provisioning YAML:

```yaml
# grafana/provisioning/datasources/perfmon.yaml
apiVersion: 1
datasources:
  - name: PerfMon-sql01
    uid: perfmon-ds-sql01
    type: mssql
    url: sql01:1433
    database: PerformanceMonitor
    user: grafana_reader
    secureJsonData:
      password: GrafanaReaderPass
  - name: PerfMon-sql02
    uid: perfmon-ds-sql02
    type: mssql
    url: sql02:1433
    database: PerformanceMonitor
    user: grafana_reader
    secureJsonData:
      password: GrafanaReaderPass
```

If UID management is more friction than it is worth for your setup, stick with the dynamic fleet.

### Common pitfalls

| Symptom | Likely cause | Fix |
|---|---|---|
| Instance dropdown is empty | Datasource not named `PerfMon-*` | Rename datasources to match `PerfMon-<anything>` |
| Fleet Overview shows nothing | Same as above | Same fix |
| Panels show "No data" | Collector SQL Agent job not running, or wrong database in datasource | Confirm the `PerformanceMonitor - Collection` SQL Agent job is enabled and runs to success; confirm **Database** is set to `PerformanceMonitor` in the datasource |
| Datasource test fails | Network, firewall, or wrong host/port | Confirm SQL Server port is reachable from Grafana; check `hostname:port` format |
| Data looks stale or frozen | Collection stopped | Open **Collection Health** dashboard; check the **health_status** column in the Collector health panel |
| All panels show timestamps offset by N hours | SQL Server OS timezone is misconfigured, or a stale cached dashboard JSON is in use | Panel queries compute the UTC offset live via `DATEDIFF(MINUTE, GETUTCDATE(), GETDATE())` and apply it automatically. If the offset persists, verify the SQL Server host OS timezone is set correctly, then force-reload the dashboard to clear any cached JSON. |
| Static fleet shows "data source not found" | Datasource UIDs do not match `perfmon-ds-<name>` | Set UIDs via provisioning YAML as shown above, or use the dynamic fleet |

---

## Complete solution

Use this path if you want PerformanceMonitor installed, datasources provisioned, and dashboards
deployed all from one command. Ansible handles everything including the `grafana_reader` login,
datasource UIDs, and static fleet generation.

> [!TIP]
> These are plain, idempotent `ansible-playbook` invocations - point any automation runner
> (AWX/Tower, Jenkins, Rundeck, GitHub Actions, etc.) at the same command for a one-click run
> instead of running it by hand.

### Prerequisites

- Ansible control node (runs on Linux only)
- `sqlcmd` (mssql-tools18) installed on the Ansible control node
- SQL Server instances with SQL Agent enabled (Windows or Linux)
- Grafana instance (self-hosted or cloud; the API must be reachable from the Ansible control node)
- `grafana_api_key`: a Grafana service account token with Admin role. Set via vault or group vars.

Install the required Ansible collection once:

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

### Step 1: Edit the inventory

Add your SQL Server instances to your inventory. For example, see -
[ansible/inventory/hosts.yml](ansible/inventory/hosts.yml).

If you are starting fresh, define hosts directly under `sql_servers`:

```yaml
sql_servers:
  hosts:
    sql01:
      ansible_host: sql01.example.com
      mssql_port: 1433
      mssql_instance: "MSSQLSERVER"
    sql01-reporting:
      ansible_host: sql01.example.com
      mssql_instance: "REPORTING"
```

If you already have an existing inventory with your own host groups, you do not need to
restructure it. Add `sql_servers` as a parent group that contains your existing groups:

```yaml
sql_servers:
  children:
    production:
      children:
        us_east_1:
        eu_cent_1:
    staging:
    test:
```

The roles target the `sql_servers` group; any host reachable through it (directly or via
`children`) will be included.

Set credentials in your group vars (see example:
[ansible/inventory/group_vars/sql_servers.yml](ansible/inventory/group_vars/sql_servers.yml))
or an Ansible Vault file. `perfmon_admin_sql_password` and `perfmon_reader_password` are required
for the default `sql` auth mode; `perfmon_admin_auth_mode`/`perfmon_reader_auth_mode: windows`
need a different set of variables instead - see the
[perfmon_install](ansible/roles/perfmon_install/README.md) and
[perfmon_grafana](ansible/roles/perfmon_grafana/README.md) role docs for the full list.

### Step 2: Deploy

```bash
# Install PerformanceMonitor, provision Grafana datasources, dashboards and alerts
# Note: Replace inventory and playbook paths as needed
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/main.yml
```

Or run steps separately:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/install_performance_monitor.yml # SQL Server only
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml # Grafana only
```

What this does:

- Downloads the pinned PerformanceMonitor release and runs all install scripts via `sqlcmd`
- Installs community tools: sp_WhoIsActive, DarlingData, First Responder Kit
- Creates the `grafana_reader` login with `SELECT` on the PerformanceMonitor schemas (`collect`,
  `report`, and `config`), and optionally `VIEW SERVER STATE`, `CONNECT ANY DATABASE`,
  `VIEW ANY DEFINITION`, and `SQLAgentReaderRole` in `msdb` for additional dashboard/alert coverage.
- Provisions Grafana datasources named `PerfMon-<hostname>` with UIDs `perfmon-ds-<hostname>`
- Generates and imports a static fleet dashboard with all inventory instances sorted by severity
- Provisions Grafana alert rules per instance (see [Alerting](#alerting))

All steps are safe to re-run. To add an instance later: add it to `hosts.yml` and re-run `main.yml`.

To upgrade PerformanceMonitor: see [Upgrading](#upgrading).

---

## Local demo

Requires Docker with approximately 6 GB of free RAM. SQL Server containers cap memory at 1.5 GB
each (`MSSQL_MEMORY_LIMIT_MB=1536`, container hard limit 2 GB). The workload containers are
lightweight sqlcmd scripts. The ansible-runner exits after the playbook completes.

```bash
cp .env.example .env
# Edit .env to set MSSQL_SA_PASSWORD, MSSQL_READER_PASSWORD, GRAFANA_ADMIN_PASSWORD
docker compose up -d
```

This builds an `ansible-runner` image and runs the full Ansible playbook inside the stack. The
first run takes approximately 5-10 minutes. Once all containers are started/healthy, watch ansible
playbook tasks progress using:

```bash
docker compose logs -f ansible-runner
```

What you get:

- Two SQL Server instances (2022 on port 14333, 2025 on port 14334) with SQL Agent enabled
  and PerformanceMonitor installed.

- Two workload generators:
  - `workload` against SQL Server 2022: stored procedure calls, ad-hoc queries, DDL
    events, blocking pairs, and deadlocks (the primary workload instance)
  - `workload-memory` against SQL Server 2025: memory-pressure workload generating
    RESOURCE_SEMAPHORE waits and memory grant queue activity
- Grafana at **http://localhost:3000** with all dashboards in the **PerformanceMonitor** folder

Panels show "datasource not found" for the first few minutes while the playbook runs. Once the
`ansible-runner` container exits, data starts flowing. Start at **Fleet Overview**.

> [!NOTE]
> **Demo metric quirks:** SQL Server DMVs do not have visibility into Docker container boundaries,
> so OS memory and CPU panels report host-level totals rather than per-container values. Some stats
> (such as memory utilization) will show expected-looking values for a healthy instance that may
> appear misleading out of context.

See [docker/README.md](docker/README.md) for full details on the stack and troubleshooting.

To stop: `docker compose down`. Add `-v` to also delete data volumes.

---

## Upgrading PerformanceMonitor

1. Backup the `PerformanceMonitor` database, then bump `perfmon_version` in
   [defaults](ansible/roles/perfmon_install/defaults/main.yml) or `group_vars` and re-run the
   install playbook. All scripts are safe to re-run.

2. With the stack running, smoke-test every panel query:

   ```bash
   python3 scripts/verify-panels.py <datasource-uid>
   ```

   Any panel whose SQL references a renamed or dropped column will fail with a SQL error. Fix the
   column reference in the relevant module under
   `ansible/roles/perfmon_grafana/files/dashboard_defs/`, then re-run the Grafana playbook to
   regenerate and reimport:

   ```bash
   ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags dashboards
   ```

   Column changes can come from any install script that creates or alters a view or table -
   `47`/`48` (`report.*` views), `54` (finops views) and `51`-`53` (`collect.*`
   table schemas). The verify script catches all of them in one pass.

3. Check for new tabs or panels added upstream (there is no automated way to detect these):

   ```bash
   git -C ../PerformanceMonitor diff <old-tag> <new-tag> -- Dashboard/Controls/ # new *Content.xaml = new tab
   git -C ../PerformanceMonitor diff <old-tag> <new-tag> -- install/ # new views or table columns
   git -C ../PerformanceMonitor diff <old-tag> <new-tag> -- Dashboard/schema/tables.json # new collect.* columns
   ```

> [!CAUTION]
> Pin `perfmon_version` in `defaults/main.yml` or `group_vars`, not as a one-off `--extra-vars`
> override. Extra vars has the highest variable precedence in Ansible, so if you use it to
> override-install a version once and then omit it on a later run, the playbook falls back to the lower
> value still sitting in `defaults/main.yml` or `group_vars` - which the role interprets as a downgrade
> request against the version already installed, and fails with "downgrade not supported" error.

---

## How it works

### System overview

The project has two independent layers: a **collection layer** that runs entirely on SQL Server
(Erik's PerformanceMonitor), and a **presentation layer** built with Grafana. There is no
intermediary data store, ETL pipeline, or agent process between them.

Grafana queries each instance's `PerformanceMonitor` database over a direct TCP connection.
Data never leaves the monitored server, Grafana is purely a read-only consumer.

### Metric collection pipeline

PerformanceMonitor installs a `PerformanceMonitor` database on each SQL Server. A SQL Agent job
fires `collect.scheduled_master_collector` once per minute. A dispatcher table
(`config.collection_schedule`) decides which stored procedures are due. Each procedure snapshots
one DMV (wait stats, query stats, CPU, memory, blocking, file I/O, TempDB, sessions, plan cache,
etc.) into its `collect.*` table.

Two properties make the schema easy to query in Grafana:

- **Pre-computed deltas.** Cumulative DMV counters are stored with `*_delta` columns - the
  increment since the previous sample, restart-aware.
- **Analysis lives in `report.*` views.** Contention recommendations, health classification, top-N
  rankings, config-change diffs, and critical issue detection are all implemented in SQL views
  maintained by Erik upstream. Panel SQL stays thin and inherits improvements automatically when
  the upstream version is bumped.

The following community tools are required for specific collectors to produce data:
sp_WhoIsActive, DarlingData scripts, and First Responder Kit. Without them, the collectors that depend
on these procedures error silently and leave their tables empty. The Ansible role installs all three automatically.

### Dashboard generation

The JSON files in `grafana/dashboards/perfmon/` are generated artifacts. The canonical source
is `ansible/roles/perfmon_grafana/files/build-dashboards.py`. Every panel, query, variable,
row, link, and threshold is defined in Python and serialized to JSON when you run the builder:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags generate
```

The JSON files are committed to the repository so Grafana can load them without running the
builder. They must not be hand-edited - the next builder run overwrites them completely.

Dashboards are imported into Grafana via the HTTP API by the `perfmon_grafana` Ansible role.
After regenerating JSON, re-run the role with `--tags dashboards` to push the updates.

`scripts/verify-panels.py` executes every panel's SQL against a live datasource and reports
the result. A SQL error fails; zero rows is not a failure. Run this after modifying panel SQL to
catch syntax errors and renamed columns before committing.

### Datasource naming and the instance variable

Every per-instance dashboard (Overview, Queries, Locking, etc.) declares an `$instance` variable:

| Variable property | Value |
|---|---|
| Type | datasource |
| Query | `mssql` (enumerate all Grafana MSSQL datasources) |
| Regex | `/^PerfMon-/` (filter to those matching the prefix) |

Selecting an instance re-points every panel to that datasource. Deep links carry the selection:
`/d/perfmon-instance?var-instance=PerfMon-sql01`.

The `^PerfMon-` regex is baked into the generated dashboard JSON by the builder. The Ansible role
exposes `perfmon_ds_name_prefix` (default `PerfMon`) to control the datasource name prefix, but
changing it requires regenerating the dashboards with a matching prefix so both sides stay in sync.
A datasource that does not match the regex is invisible to all dashboards. Cross-dashboard
navigation links rely on stable dashboard UIDs (`perfmon-blocking`, `perfmon-queries`, etc.);
renaming UIDs breaks those links.

### Fleet discovery

The Fleet Overview uses `$instance` in multi-value, include-all mode. Two approaches exist for
converting the datasource list into a fleet view:

**Dynamic fleet (default)** - Grafana repeats a panel block per datasource value, alphabetically.
Each block issues its own SQL against its datasource. A new `PerfMon-*` datasource appears
automatically on the next dashboard refresh with no dashboard changes required. The constraint is
ordering: Grafana's panel-repeat mechanism is alphabetical and cannot be reordered by a computed
metric value such as severity score.

The schema-v2 variant (`fleet-overview-v2.json`) renders each instance as a health card and adds a
severity filter: instances outside the selected severity levels are hidden via conditional rendering.
The Ansible role imports the v2 variant when the target Grafana supports it (version >= 12.4 and the `dashboardNewLayouts` feature toggle, probed via `/api/frontend/settings`) and falls back to the
v1 file otherwise; both share the `perfmon-fleet` UID so navigation links work in either case.
The gate is version-based because older Grafana accepts and stores the v2 resource but evaluates
the show/hide rule for all repeated copies together.

**Static fleet** - The builder's `--fleet-instances` flag generates an alternative fleet JSON
that uses Grafana's Mixed datasource. One SQL query per named instance, all results merged into a
single table by Grafana's Merge transform. The merged table can be sorted by any column, including
severity score descending, so the most stressed instances rise to the top. The trade-off: instance
names are baked into the JSON. Adding or removing instances requires regenerating and re-importing
the file.

### Severity score

Both fleet modes compute a severity score per instance over a fixed trailing 15-minute window,
independent of the dashboard's selected time range. Health signals are grouped into categories; each category is scored 0 (healthy), 1 (warning), or 2 (critical) by its **worst** signal, and the overall score is

```
severity = 10 x (critical categories) + (warning categories)
```

so any instance with a critical category (score >= 10) outranks any number of warnings.
Displayed as a color-coded badge: no color at 0, yellow from 1, red from 10.

Wait-based signals (marked *avg waiting* below) are normalized to the average number of
sessions concurrently in that wait: `SUM(wait_time_ms_delta) / 1000 / elapsed seconds` over
the trailing window. Unlike the point-in-time gauges the collectors sample once per cycle, wait
deltas integrate over the interval, so burst pressure that resolves between samples still
registers. The named wait types are upstream's poison-wait set.

| Category | Signals (warning / critical) |
|---|---|
| CPU | avg `total_cpu_utilization` > 80% / > 90% |
| Threads | active workers pct of max > 70% / > 90%; `THREADPOOL` avg waiting > 0.01 / > 0.1 |
| Memory | `RESOURCE_SEMAPHORE`(`_QUERY_COMPILE`) avg waiting > 0.01 / > 0.1 |
| Blocking | raw blocked-process-report event count in the trailing window > 1 / > 25; max block duration >= 10s / >= 60s |
| Deadlocks | total `deadlock_count` >= 1 / >= 10 |
| Collectors | any collector erroring in the last 5 minutes (warning only) |

### Ansible roles

**[`perfmon_install`](ansible/roles/perfmon_install/README.md)** installs PerformanceMonitor onto
each SQL Server inventory host:

1. Downloads the pinned release zip from Erik's GitHub (version in
   `roles/perfmon_install/defaults/main.yml` as `perfmon_version`)
2. Runs the numbered install scripts, in order, via `sqlcmd` from the Ansible control node over TCP.
3. Installs sp_WhoIsActive, DarlingData scripts, and First Responder Kit
4. Creates `grafana_reader` with SELECT on the `collect`, `report`, and `config` schemas and `VIEW SERVER STATE`

**[`perfmon_grafana`](ansible/roles/perfmon_grafana/README.md)** provisions the Grafana side via
the Grafana HTTP API:

1. Generates all dashboard JSON files from the Python builder embedded in the role
   (`files/build-dashboards.py`) and imports them into the `PerformanceMonitor` folder in Grafana
2. Creates one MSSQL datasource per inventory host, named `PerfMon-<hostname>` with UID
   `perfmon-ds-<hostname>`
3. Provisions Unified Alerting rule groups per SQL Server instance, scoped to that instance's
   datasource UID
4. Provisions contact points, mute timings, and the notification policy tree

All alerting resources are managed via the Grafana Provisioning API - no file provisioning is used.
The role replaces the full notification policy tree on every run; routes not declared via role
variables are removed.

Both roles are safe to re-run. The SQL scripts use `IF NOT EXISTS` guards so re-running will not duplicate objects or fail on an already-configured target. However, the scripts always execute via `sqlcmd`, so Ansible will always report `changed` rather than `ok`.

### Naming conventions

| Thing | Pattern | Example |
|---|---|---|
| Datasource name | `PerfMon-<name>` | `PerfMon-sql01` |
| Datasource UID | `perfmon-ds-<name>` | `perfmon-ds-sql01` |
| PerfMon dashboard UID | `perfmon-<slug>` | `perfmon-blocking` |
| FinOps dashboard UID | `finops-<slug>` | `finops-utilization` |
| Monitored database | `PerformanceMonitor` | `PerformanceMonitor` |

The datasource UID convention is required for the static fleet (each target references
`perfmon-ds-<hostname>` directly in the JSON). The Ansible role sets UIDs automatically. For
manual setups, UIDs must be configured via provisioning YAML as shown in the [Fleet
Overview](#fleet-overview) section.

### Role documentation

- [ansible/roles/perfmon_install/README.md](ansible/roles/perfmon_install/README.md) - full
  variable reference, named-instance connection strings, air-gapped install, upgrade procedure
- [ansible/roles/perfmon_grafana/README.md](ansible/roles/perfmon_grafana/README.md) - full
  variable reference, `ds_host`/`ds_port` for when Grafana reaches SQL Server via a different address than Ansible does, overriding the instance list

---

## Alerting

Grafana Unified Alerting is provisioned with alert rules that replicate the threshold-based
notifications from Erik's upstream notification engine. Rules evaluate every minute against the
`collect.*` tables on each monitored instance.

### Alert rules

| Alert | Default threshold |
|---|---|
| High CPU | most recent total CPU >= 90% |
| Blocking Detected | longest current lock wait >= 30 s |
| Deadlocks Detected | >= 1 deadlock in the last 5 minutes |
| TempDB Space | most recent used >= 80% of allocated space |
| Low Disk Space | most recent free < 10% OR < 5 GB on any volume |
| Long-Running Query | any query currently running >= 30 min |
| Poison Wait | avg ms per wait event >= 500 ms for `THREADPOOL`, `RESOURCE_SEMAPHORE`, or `RESOURCE_SEMAPHORE_QUERY_COMPILE` |
| Long-Running Collector Job | current run >= 3x average duration (jobs with avg < 60 s excluded) |
| Failed Collector Job | most recent overall run of the collection job was a failure |
| Collection Stopped | Collector Agent job is disabled, or no collector has logged a run in 30 minutes |

All thresholds are Ansible variables defined in `roles/perfmon_grafana/defaults/main.yml`. Override
per-host in `host_vars/` or per-group in `group_vars/`.

> [!NOTE]
> These rules are intentionally independent of the Fleet Overview severity score (see
> [Severity score](#severity-score)) - crossing an alert threshold does not require the fleet
> category to be red, and vice versa. The severity score is still being tuned, and coupling live
> notifications to it risks a barrage of alerts from noisy or miscalibrated categories.

### Default behavior: silent

Alerts fire and are tracked in Grafana but no notifications are sent until a contact point is
configured. Without SMTP configured (the default), evaluation runs and state is visible in the
Grafana Alerts UI, but nothing is dispatched.

### Enabling delivery

Set `perfmon_alert_contact_points` in your inventory and re-run the `perfmon_grafana` role. Each
entry is a contact point object passed to the Grafana API. All entries must share the same `name`
value - Grafana treats them as one receiver with multiple integrations.

```yaml
# e.g. in ansible/inventory/group_vars/all.yml
perfmon_alert_contact_points:
  - uid: perfmon-slack
    name: perfmon-alerts
    type: slack
    settings:
      url: "https://hooks.slack.com/services/T000000/B000000/XXXXXXXXXXXXXXXXXXXXXXXX"
      recipient: "#alerts"
      title: "PerfMon Alert"
  - uid: perfmon-pagerduty
    name: perfmon-alerts
    type: pagerduty
    settings:
      integrationKey: abc123def456abc123def456abc123de
```

The notification policy route targets `perfmon_alert_receiver_name` (default `perfmon-alerts`).
Add multiple entries to fire more than one integration per alert. Email requires `GF_SMTP_*` env
vars on the Grafana server; set `type: email` and `settings.addresses` for it.

### Per-instance rules and unreachable hosts

Alert rule groups are provisioned per SQL Server inventory host via the Grafana API. A host that
is down or unreachable appears in the Grafana Alerts UI in **Error** state. Re-run the Ansible playbook after changing inventory hosts or threshold variables.

---

## Known limitations

### Features with no Grafana equivalent

The following capabilities from Erik's C# WPF dashboard cannot be replicated in Grafana:

| Feature | Why |
|---|---|
| **Graphical query plan viewer** | Grafana has no built-in ShowPlan XML renderer. Affected panels show the XML text for copy-paste into SSMS or Erik's standalone viewer. A custom Grafana plugin could add this but one has not been built yet. |
| **AI/ML analysis engine** | The upstream `Dashboard/Analysis/` layer does inference, anomaly detection, baselines, and fact scoring. Grafana has no native equivalent for this kind of analytical ML. `report.critical_issues` covers the rule-based subset only. |
| **MCP server** | The upstream project exposes several monitoring tools to LLM-based editors. This project does not include an MCP service. Grafana's own mcp-grafana project provides MCP access to Grafana itself, but a separate service querying `report.*` views directly would be needed to expose the monitoring data. |
| **Side-by-side query comparison** | Compare a query's performance across two separate time ranges. Not currently supported. |
| **Correlated timeline lanes** | Multi-metric synchronized timeline with relationship highlighting. Grafana's synchronized tooltips are a partial substitute. |
| **FinOps cost attribution** | The upstream stores a user-supplied monthly server cost per connection in a persisted config table and uses it to compute proportional cost shares by storage, CPU, and wait time across the FinOps tabs. Grafana has no equivalent config store - each of the three FinOps dashboards that need it exposes its own `monthly_cost` variable instead, entered separately per dashboard and not persisted server-side. |
| **Index Analysis** | `sp_IndexCleanup` returns two result sets; the MSSQL datasource plugin only surfaces the first, so it can't be rendered as a live panel. The dashboard shows instructions to run the procedure manually (or use the upstream desktop application) instead. |

> [!CAUTION]
> **Monitoring blindspot under severe worker exhaustion**
>
> Grafana queries each monitored SQL Server directly over a live TCP connection, the monitoring
> queries compete for the same worker threads and memory grants as application workload. Under
> severe memory pressure, for example, when `RESOURCE_SEMAPHORE` poison waits cause the instance
> to exhaust its worker thread pool, Grafana's panel queries queue behind or are blocked by the same
> waits they are trying to surface. In the worst case, panels fail to load entirely. In moderate cases,
> panels load slowly. This is an inherent consequence of the direct-query architecture (no intermediary
> data store or push-based agent). There is no clean mitigation once the instance is exhausted,
> monitoring queries queue behind application workload like everything else. The closest preventive
> measure is classifying application workload into a Resource Governor workload group with
> `REQUEST_MAX_MEMORY_GRANT_PERCENT` capped to a reasonable value. This limits how much memory any
> single query can acquire, reduces `RESOURCE_SEMAPHORE` queue depth, and makes the worker pile-up
> less likely to occur.

### Timezone

`collection_time` is stored as `SYSDATETIME()` - the SQL Server's local wall clock, no UTC offset.
Grafana's MSSQL datasource treats returned `datetime2` values as UTC and then the browser shifts
them to display in your local timezone.

**This is handled automatically.** The dashboard builder applies `DATEDIFF(MINUTE, GETUTCDATE(),
GETDATE())` in every panel query to compute the SQL Server's UTC offset at query time. The offset
is applied in two places:

- **WHERE clause** - Grafana's UTC time-range bounds are shifted into server-local time before
  comparing against `collection_time`, so the filter always selects the right rows regardless of
  the server's timezone.
- **Time-axis column** - the stored local time is shifted to UTC before being returned, so
  Grafana's time axis and the browser-local display are correct.

This works for any UTC offset, including fractional-hour zones, and tracks DST transitions
correctly because the offset is evaluated live at query time.

> [!NOTE]
> timestamp values shown in table panels (event times, blocking times, change
> times) reflect the SQL Server's local time as stored - the same values you would see in SSMS or
> Erik's C# app. Only the time axis of time-series panels is UTC-shifted.

---

## License

[MIT License](LICENSE).

### Dependency licenses

| Dependency | License |
|---|---|
| [Erik Darling's PerformanceMonitor](https://github.com/erikdarlingdata/PerformanceMonitor) | MIT |
| [DarlingData](https://github.com/erikdarlingdata/DarlingData) | MIT |
| [First Responder Kit](https://github.com/BrentOzarULTD/SQL-Server-First-Responder-Kit) | MIT |
| [sp_WhoIsActive](https://github.com/amachanic/sp_whoisactive) | GPL v3 |

> [!NOTE]
> DarlingData, First Responder Kit, and sp_WhoIsActive are installed at runtime by the
> Ansible role and are not bundled in this repository. Erik's PerformanceMonitor install
> scripts and stored procedures are also fetched at runtime, except for a small set of local
> bug-fix patches in [`patches/`](patches/), which modify specific upstream procedures and
> retain the original MIT copyright header. The Grafana panel SQL in
> [`dashboard_defs/`](ansible/roles/perfmon_grafana/files/dashboard_defs/) is bundled in this
> repository: query logic (column expressions, aggregation, CASE branches) is copied from
> PerformanceMonitor's C# dashboard queries and reworked to run through Grafana's macros,
> permitted under PerformanceMonitor's MIT license.
