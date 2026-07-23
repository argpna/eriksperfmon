# perfmon_grafana

Provisions Grafana datasources, dashboards, and alert rules for PerformanceMonitor.

## What it does

1. Generates dashboard JSON files from the Python builder embedded in the role (`files/build-dashboards.py`)
   and writes them to `files/grafana/dashboards/perfmon/` inside the role.
2. Creates or updates one MSSQL datasource per host in the `sql_servers` inventory group.
   Datasources are named `PerfMon-<inventory_hostname>` with UID `perfmon-ds-<inventory_hostname>`.
   The dashboard `$instance` variable filters on `/^PerfMon-/` and cross-dashboard links rely on the UIDs.
   Datasources for instances outside the current run stay untouched unless `perfmon_prune_orphaned`
   is set.
3. Creates the `PerformanceMonitor` folder in Grafana (UID controlled by `grafana_folder_uid`),
   then imports all dashboard JSON files from `files/grafana/dashboards/perfmon/` into it. The
   dynamic fleet dashboard is imported as a v1 file here like any other, then conditionally
   replaced: when the target Grafana is >= 12.4 with the `dashboardNewLayouts` feature toggle
   enabled, the role pushes a schema v2 variant (`fleet-overview-v2.json`) over the same
   `perfmon-fleet` UID via the k8s-style dashboards API, which supports the conditional
   rendering that hides filtered-out instances. Older/toggle-off Grafana keeps the v1 import.
   `perfmon_fleet_static` skips this entirely - only the static (v1) fleet file is generated.
4. Provisions Grafana Unified Alerting rule groups per SQL Server instance via the Grafana
   Provisioning API, scoped to that instance's datasource UID. Rule groups are shared by name
   across instances, so each run merges its own instances' rules into the group rather than
   replacing it.
5. Provisions contact points (email, Slack, PagerDuty, or webhook) via the Grafana Provisioning API
   when a delivery backend is configured. Contact points not referenced by any variable are removed.
6. Provisions mute timings listed in `perfmon_alert_mute_timings` via the Grafana Provisioning API.
7. Upserts a `team=perfmon` route in the Grafana notification policy via `upsert_notification_route.py`.
   Only the perfmon route is touched; all other routes in the policy tree are left untouched.

## Requirements

- `grafana_api_key`: a Grafana service account token with Admin role. Create the service account
  in the Grafana UI (Administration -> Service accounts) and supply the token via vault.
- `perfmon_reader_password`: password for the reader SQL login (`perfmon_reader_login_name`).
  Supply via vault.
- Grafana must have Unified Alerting enabled. Add `GF_UNIFIED_ALERTING_ENABLED=true` to Grafana's
  environment.
- Ansible collection: `community.grafana`.

## Usage

### Minimal playbook

The role runs on the Grafana host:

```yaml
- name: Deploy PerformanceMonitor dashboards to Grafana
  hosts: grafana
  gather_facts: false
  tasks:
    - name: Apply perfmon_grafana role
      ansible.builtin.import_role:
        name: perfmon_grafana
```

Run it:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml
```

Or run the full end-to-end playbook:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/main.yml
```

### Tag-based targeting

Use tags to run specific operations without executing the full role:

```bash
# Regenerate dashboard JSON files only
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags generate

# Generate and import dashboards
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags dashboards

# Provision datasources only
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags datasources

# Provision alerting resources only
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags alerting
```

Available tags:

| Tag | Tasks covered |
|---|---|
| `datasources` | Create/update Grafana datasources |
| `dashboards` | Generate JSON files and import them into Grafana |
| `generate` | Generate JSON files only (subset of `dashboards`) |
| `alerting` | Alert rules, contact points, mute timings, notification policy |
| `teardown` | Remove all provisioned resources (see Removal below) |
| `teardown_alerting` | Remove alert rules, contact points, mute timings, notification policy route |
| `teardown_datasources` | Remove all datasources |
| `teardown_dashboards` | Remove the PerformanceMonitor folder and all dashboards inside it |

### Inventory

Add SQL Server instances to a `sql_servers` group and a `grafana` group for the Grafana host:

```yaml
all:
  children:
    sql_servers:
      hosts:
        sql01:
          ansible_host: pubs-dev.example.com
          mssql_port: 1433
        sql01-reporting:
          ansible_host: pubs-dev.example.com
          mssql_port: 52791
    grafana:
      hosts:
        grafana:
          ansible_host: grafana-dev.example.com
          grafana_url: http://grafana-dev.example.com:3000
```

Use `ds_host` / `ds_port` when Grafana reaches SQL Server via a different address than the
Ansible control node uses:

```yaml
sql_servers:
  hosts:
    mssql-a:
      ansible_host: localhost
      mssql_port: 14331 # port the control node uses
      ds_host: mssql-a  # hostname Grafana uses to reach SQL Server
      ds_port: 1433
```

`ds_host`/`ds_port` default to `ansible_host`/`mssql_port` when omitted - only set them when the two
addresses genuinely differ.

### Required credentials

`grafana_api_key` and `perfmon_reader_password` have no role defaults and must be supplied. The recommended way is Ansible Vault:

```yaml
# group_vars/grafana.yml
grafana_api_key: "{{ vault_grafana_api_key }}"
perfmon_reader_password: "{{ vault_perfmon_reader_password }}"
```

Create the service account in the Grafana UI (Administration -> Service accounts) with Admin role.
`perfmon_reader_password` is the password for the reader SQL login created by `perfmon_install`.

## Variables

### Core variables

| Variable | Default | Notes |
|---|---|---|
| `grafana_url` | `http://localhost:3000` | Grafana base URL. |
| `grafana_api_key` | - | Required. Grafana service account token with Admin role. |
| `grafana_folder` | `PerformanceMonitor` | Grafana folder title where dashboards are placed. |
| `grafana_folder_uid` | `perfmon` | Grafana folder UID. Must be stable across runs. If upgrading from an older role version that auto-assigned the folder UID, set this to match the existing UID. |
| `perfmon_reader_password` | - | Required. Password for the reader login (created by `perfmon_install`). |
| `perfmon_reader_login_name` | `grafana_reader` | Name of the SQL login `perfmon_install` created (same variable in that role - one inventory setting covers both). Set per-host in inventory when one instance's login differs. Ignored when the resolved auth mode is `windows`. |
| `perfmon_ds_name_prefix` | `PerfMon` | Datasource name prefix. Results in `PerfMon-<hostname>`. |
| `perfmon_ds_uid_prefix` | `perfmon-ds` | Datasource UID prefix. Results in `perfmon-ds-<hostname>`. |
| `perfmon_instances` | derived from `sql_servers` group | Override with an explicit list when your inventory group is named differently or you are running without a `sql_servers` group. See below. |
| `perfmon_fleet_static` | `false` | When true, regenerates the fleet dashboard with all inventory hostnames baked in as a single table sortable by severity score. When false, dynamic fleet is used, which auto-discovers datasources matching `/^PerfMon-/` but cannot sort across instances. |
| `perfmon_reader_auth_mode` | `sql` | Datasource authentication mode: `sql` or `windows` (Kerberos AD). Same variable `perfmon_install` uses - one inventory setting covers both roles. Set fleet-wide in `group_vars` or per-host for a mixed fleet. |
| `perfmon_reader_windows_upn` | - | Required when the resolved auth mode is `windows`. AD principal in UPN form (`user@REALM`) for the datasource login - see Windows/AD authentication. |
| `perfmon_reader_windows_password` | - | Required when the resolved auth mode is `windows`. The AD principal's domain password. Supply via vault. |
| `grafana_krb5_conf_path` | `/etc/krb5.conf` | Path *on the Grafana host* to a Kerberos client config pointing at the domain's KDC. Only used when the resolved auth mode is `windows`. This role does not author or manage `krb5.conf`. |
| `perfmon_prune_orphaned` | `false` | Delete datasources, mute timings, and alert rules that exist in Grafana but belong to an instance not in this run's `perfmon_instances`. Off by default so a run against a partial inventory never deletes another instance's resources. Only set `true` for a run whose inventory is the complete, current fleet - see Retiring an instance. |

### Alert threshold variables

All thresholds default to the values from the upstream `UserPreferences.cs`. Override per-host in
`host_vars/` or per-group in `group_vars/` without modifying provisioning files.

| Variable | Default | Alert rule |
|---|---|---|
| `perfmon_alert_cpu_pct` | `90` | High CPU - fires when max CPU utilization >= this percent |
| `perfmon_alert_blocking_s` | `30` | Blocking Detected - fires when longest chain >= this many seconds |
| `perfmon_alert_deadlock_count` | `1` | Deadlocks Detected - fires when deadlock count in window >= this value |
| `perfmon_alert_tempdb_pct` | `80` | TempDB Space - fires when used >= this percent of allocated TempDB |
| `perfmon_alert_disk_free_pct` | `10` | Low Disk Space - fires when free space on any volume < this percent (OR condition with GB floor) |
| `perfmon_alert_disk_free_gb` | `5` | Low Disk Space - fires when free space on any volume < this many GB (OR condition with pct floor) |
| `perfmon_alert_query_duration_floor_min` | `30` | Long-Running Query - fires when any currently executing query has been running >= this many minutes |
| `perfmon_alert_poison_wait_floor_ms` | `500` | Poison Wait - fires when avg ms per wait event for `THREADPOOL`, `RESOURCE_SEMAPHORE`, or `RESOURCE_SEMAPHORE_QUERY_COMPILE` >= this value |
| `perfmon_alert_long_running_job_multiplier` | `3` | Long-Running Collector Job - fires when the PerformanceMonitor - Collection job's current duration >= this multiple of its average |
| `perfmon_alert_failed_job_lookback_min` | `60` | Failed Collector Job - how far back to look for failed PerformanceMonitor - Collection runs |
| `perfmon_alert_collection_stale_min` | `30` | Collection Stopped - fires when no collector has logged a run in this many minutes (disabled collector Agent jobs fire immediately regardless of this value) |

### Alert routing variables

These control the timing behaviour of the `team=perfmon` notification policy route. The defaults
match the upstream `UserPreferences.cs` grouping cadence.

| Variable | Default | Notes |
|---|---|---|
| `perfmon_alert_group_wait` | `30s` | How long to wait before sending the first notification for a new group of alerts. |
| `perfmon_alert_group_interval` | `5m` | How long to wait before sending a notification about new alerts added to an already firing group. |
| `perfmon_alert_repeat_interval` | `4h` | How long to wait before re-sending a notification for an alert that is still firing. |
| `perfmon_alert_group_by` | `team,instance,alertname` | Comma-separated list of label names used to group alerts into notifications. |

### Mute timing variables

| Variable | Default | Notes |
|---|---|---|
| `perfmon_alert_mute_timings` | `[]` | List of Grafana mute timing objects to provision. Each entry must have a `name` (use `perfmon-` prefix) and `time_intervals`. When `perfmon_prune_orphaned` is set, timings with a `perfmon-` prefix that are no longer in this list are removed. |

### Alert contact point variables

| Variable | Default | Notes |
|---|---|---|
| `perfmon_alert_contact_points` | `[]` | List of contact point objects passed to `grafana.grafana.alert_contact_point`. Each entry needs `uid`, `name`, `type`, and `settings`. All entries should use the same `name` value (`perfmon_alert_receiver_name`) so Grafana treats them as one receiver with multiple integrations. Set `state: absent` on an entry to remove it. |
| `perfmon_alert_receiver_name` | `perfmon-alerts` | Name of the receiver the notification policy route points to. Must match the `name` field on every contact point entry. |

### Overriding the instance list

By default the role builds `perfmon_instances` by extracting the full `hostvars` dict for every
host in the `sql_servers` inventory group. Each element therefore exposes the same keys as the
host's inventory entry: `inventory_hostname`, `ansible_host`, `mssql_port`, `ds_port`, etc.

Override the variable when your group has a different name or you are running without an inventory:

```yaml
perfmon_instances:
  - inventory_hostname: pubs-dev01
    ansible_host: pubs-dev01.example.com
    mssql_port: 1433
  - inventory_hostname: pubs-dev02
    ansible_host: pubs-dev02.example.com
    mssql_port: 52791
```

Use `ds_host` / `ds_port` on an entry when the address Grafana uses to reach SQL Server differs
from `ansible_host` / `mssql_port`.

Per-instance auth settings (`perfmon_reader_auth_mode`, `perfmon_reader_login_name`,
`perfmon_reader_windows_upn`, `perfmon_reader_windows_password`) are ordinary keys on an entry,
so inventory host_vars carry through automatically; an entry-level value wins over the
fleet-wide variable for that instance. See Windows/AD authentication below.

## Windows/AD authentication

Datasources can authenticate to SQL Server as a Kerberos AD principal instead of a SQL login. Set
`perfmon_reader_auth_mode: windows` fleet-wide (group_vars) or per-host in inventory for a mixed
fleet - the same place `perfmon_install` reads it. With an explicit `perfmon_instances` list the
keys go directly on the entry:

```yaml
perfmon_instances:
  - inventory_hostname: pubs-dev01
    ansible_host: pubs-dev01.example.com
    mssql_port: 1433
    perfmon_reader_auth_mode: windows
    perfmon_reader_windows_upn: svc_grafana_reader@LAB.INTERNAL
    perfmon_reader_windows_password: "{{ vault_reader_windows_password }}"
```

`perfmon_reader_windows_upn` is the UPN form (`user@REALM`) - Grafana's Kerberos username field requires
this format, which is a different string than the down-level `DOMAIN\principal` form
`perfmon_install` uses for `CREATE LOGIN ... FROM WINDOWS`, even though both name the same AD
principal.

This requires a valid `krb5.conf` on the Grafana host (path set via `grafana_krb5_conf_path`,
authored outside this role) pointing at the domain's KDC, and a domain-joined SQL Server
instance. `perfmon_reader_windows_password` is the AD principal's domain password; like
`perfmon_reader_password` it lands in Grafana's `secureJsonData`, encrypted at rest.

## Alerting

The role provisions Grafana Unified Alerting rule groups per SQL Server instance via the
Grafana Provisioning HTTP API, covering the upstream notification set (`AlertSeverity.cs`).
Rules evaluate every minute, with a pending period before firing (1 minute for most rules;
5 minutes for TempDB, Low Disk).

All alerting resources - contact points, mute timings, rule groups, and the notification policy
tree - are written via the API. No file provisioning is used, so this works from any Ansible
controller without needing filesystem access to the Grafana server.

### Enabling a contact point

```yaml
# e.g. in group_vars/all.yml
perfmon_alert_contact_points:
  - uid: perfmon-slack
    name: perfmon-alerts
    type: slack
    settings:
      url: https://hooks.slack.com/services/T000000/B000000/XXXXXXXXXXXXXXXXXXXXXXXX
      recipient: "#alerts"
      title: "PerfMon Alert"
  - uid: perfmon-pagerduty
    name: perfmon-alerts
    type: pagerduty
    settings:
      integrationKey: abc123def456abc123def456abc123de
```

Re-run `deploy_perfmon_grafana.yml`. The role provisions the contact point and the notification policy
route points to `perfmon_alert_receiver_name` (default `perfmon-alerts`). Add multiple entries
to the list to fire more than one integration for every alert.

### Configuring mute timings

```yaml
# e.g. in group_vars/all.yml
perfmon_alert_mute_timings:
  - name: perfmon-weekend-maintenance
    time_intervals:
      - weekdays: ["saturday", "sunday"]
        times:
          - start_time: "19:00"
            end_time: "07:00"
```

Name each mute timing with a `perfmon-` prefix to avoid colliding with timings owned by other teams.
With `perfmon_prune_orphaned` set, an `alerting` tag run removes any `perfmon-` timing no longer in
this list.

### Notification policy

The role upserts a single `team=perfmon` child route via `upsert_notification_route.py`. The script
reads the current policy tree, removes any prior `team=perfmon` route, appends a fresh one, and
writes it back. All other routes are left untouched, so this role is safe to use alongside other
teams managing their own routes in the same Grafana instance.

### Unreachable instances

An inventory host that Grafana cannot reach goes to **Error** state in the Alerts UI. The rule
returns to Normal automatically once the datasource becomes reachable. Hosts that are
intentionally offline should be removed from inventory to avoid persistent Error state.

## Removal

### Full teardown

Remove everything this role has provisioned from a Grafana instance:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags teardown
```

Teardown order: notification policy route, mute timings, contact points, datasources, folder
(folder deletion cascades to all dashboards and alert rule groups inside it).

Use sub-tags for selective removal:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags teardown_alerting
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags teardown_datasources
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml --tags teardown_dashboards
```

### Retiring an instance

Remove the host from inventory and re-run with `perfmon_prune_orphaned=true` (full playbook, or
`--tags datasources,alerting`), using an inventory that's the complete, current fleet - not a
partial one:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/deploy_perfmon_grafana.yml \
  --tags datasources,alerting -e perfmon_prune_orphaned=true
```

The `datasources` tag deletes any `perfmon-ds-` datasource no longer in `perfmon_instances`, so the
fleet dashboard dropdown updates on the next browser refresh. The `alerting` tag drops the retired
host's rules from each shared rule group. Without `perfmon_prune_orphaned`, both resources are left
in place - a run against a partial inventory has no way to distinguish "this instance was retired"
from "this instance just isn't in this particular run's scope".

If an inventory is accidentally omitted from a run, nothing is lost: re-run against the full fleet
and every datasource and alert rule is recreated. Data collection isn't affected either way - the
role only ever touches Grafana's own state, never the PerformanceMonitor database itself.


## Connection strings

Connection string is built from two independent parts:
- instance part: `host\instance` for named instances, `host` for `MSSQLSERVER`
- port part: `:port` appended when `mssql_port` or `ds_port` is set, omitted otherwise

This gives `host:port`, `host\instance:port`, `host\instance` (SQL Browser, last resort), or
bare `host` depending on what is configured. SQL Browser is only used when a named instance
has no port set in inventory.

`ds_host` and `ds_port` override `ansible_host` and `mssql_port` for the Grafana-side address
when the address Grafana uses to reach SQL Server differs from what the control node uses.

Per-instance TLS settings: `ds_encrypt` (default `'true'`) controls connection encryption;
`ds_tls_skip_verify` (default `true`) skips server certificate validation, matching the self-signed
certificate. Set `ds_tls_skip_verify: false` on an instance to validate the certificate - the signing
CA must then be trusted by the Grafana host.
