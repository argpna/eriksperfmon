# perfmon_install

Installs [Erik Darling's PerformanceMonitor](https://github.com/erikdarlingdata/PerformanceMonitor)
on one or more SQL Server instances. All work runs on the Ansible control node via `sqlcmd` over
TCP; SSH/WinRM-ing onto the database host is not required.

## What it does

`perfmon_state: present` (default):

1. Downloads the upstream release zip and extracts it to `perfmon_tmp_dir`.
2. Reads the installed version from `config.installation_history` (populated after the first
   Ansible-managed install). If the installed version is older than `perfmon_version`, runs the
   applicable upgrade script directories in order and records the result in `installation_history`.
3. Runs install scripts `01`-`54` in order (skips `97`-`99` test scripts). Skipped automatically
   if the database is already at `perfmon_version` unless `perfmon_force_reinstall: true`.
4. Applies any local patches from `perfmon_local_patches_dir` (skipped if empty).
5. Installs community tools: `sp_WhoIsActive`, `DarlingData` suite, `FirstResponderKit`.
6. Creates the `grafana_reader` SQL login with SELECT on the `collect`, `report`, and `config`
   schemas, `VIEW SERVER STATE` for alert queries that read live DMVs, and `SQLAgentReaderRole`
   on `msdb` for the Failed Collector Job alert.

`perfmon_state: absent`:

Runs the upstream `00_uninstall.sql` script, which drops the PerformanceMonitor database,
SQL Agent jobs, and Extended Events sessions.

## Requirements

`sqlcmd` on the Ansible control node. The role auto-discovers common install paths
(`/opt/mssql-tools18/bin/sqlcmd`, `/opt/mssql-tools/bin/sqlcmd`) and fails with install
instructions if it cannot be found.

## Variables

| Variable | Default | Notes |
|---|---|---|
| `perfmon_state` | `present` | `present` installs or upgrades; `absent` runs the upstream uninstall script. |
| `perfmon_version` | `v3.0.0` | Release tag to install or upgrade to. Single source of truth - bump to upgrade. |
| `perfmon_force_reinstall` | `false` | Set to `true` to re-run all install scripts even when `installation_history` shows the instance is already at the target version. |
| `mssql_port` | _(unset)_ | TCP port. When set, always used in the connection string: `host,port` or `host\instance,port`. Omit only for named instances where SQL Browser is the only option. |
| `mssql_instance` | `MSSQLSERVER` | Instance name. `MSSQLSERVER` for the default instance, or a named instance. Named instances without a port fall back to `host\instance` (SQL Browser). |
| `mssql_sa_user` | `sa` | Sysadmin equivalent login used during install. |
| `mssql_sa_password` | - | Required. Supply via vault. |
| `mssql_reader_password` | - | Required. Password for the `grafana_reader` login created by this role. Supply via vault. |
| `perfmon_db` | `PerformanceMonitor` | Database name. |
| `sqlcmd_bin` | `sqlcmd` | Path to sqlcmd if not on PATH. |
| `perfmon_tmp_dir` | `/tmp/perfmon-install` | Staging directory on the control node. |
| `perfmon_local_patches_dir` | `""` | Path to a directory of `.sql` patch files to apply after the install scripts. Leave empty to skip. |
| `perfmon_community_tools` | see defaults | List of `{name, url}` entries to download and install. Pre-populate `community/<name>.sql` in the repo root for air-gapped installs. |

## Required credentials

`mssql_sa_password` and `mssql_reader_password` have no role defaults and must be supplied. The recommended way is Ansible Vault:

```yaml
# group_vars/sql_servers.yml
mssql_sa_password: "{{ vault_mssql_sa_password }}"
mssql_reader_password: "{{ vault_mssql_reader_password }}"
```

## Windows Authentication

Not currently supported. The role uses SQL Server auth (`-U`/`-P`) only.

## Air-gapped installs

Pre-populate `community/perfmon-<perfmon_version>.zip`, `community/sp_WhoIsActive.sql`,
`community/DarlingData.sql`, and `community/FirstResponderKit.sql` in the repo root.
The role checks for these files before attempting any downloads, including the core
release zip.

## Upgrading

1. Bump `perfmon_version` in `defaults/main.yml` (or in `group_vars` or pass
   `-e perfmon_version=vX.Y.Z`) and re-run the role.
2. The role reads `config.installation_history` to detect the installed version, then runs only
   the upgrade directories that fall within the version gap (e.g. `3.0.0-to-3.1.0/` for a
   3.0.0 to 3.1.0 upgrade). Upgrade results are recorded in `installation_history`.
3. Downgrade is rejected. The role fails if `perfmon_version` is older than the recorded
   installed version.
4. Run `scripts/verify-panels.py <datasource-uid>` from the `eriksperfmon` repo against a live
   Grafana datasource. Any panel query that references a renamed or dropped column will fail
   with a SQL error. Fix column references in the role's `files/dashboards/` modules and regenerate.

Any script that creates or alters a view or table can break panel queries across a version bump -
`47`/`48` (`report.*` views), `54` (finops views), and `51`-`53` (`collect.*` table schemas).
The verify script catches all of them in one pass.

There is no automated way to detect new tabs or panels added to the upstream C# dashboard.
When bumping the version, diff the upstream PerformanceMonitor repo to spot additions:

    git diff <old-tag> <new-tag> -- Dashboard/Controls/ # new *Content.xaml = new tab
    git diff <old-tag> <new-tag> -- install/ # new views or table columns
    git diff <old-tag> <new-tag> -- Dashboard/schema/tables.json # new collect.* columns

## Usage

### Playbook

Use the role directly in a playbook:

```yaml
- name: Install PerformanceMonitor
  hosts: sql_servers
  gather_facts: false
  roles:
    - role: perfmon_install
      vars:
        perfmon_version: v3.0.0
        mssql_port: 1433
        mssql_sa_password: "{{ vault_sa_password }}"
        mssql_reader_password: "{{ vault_reader_password }}"
```

Or run the included playbook directly:

```bash
ansible-playbook -i ansible/inventory ansible/playbooks/install_performance_monitor.yml
```

Install on a single host:

```bash
ansible-playbook -i ansible/inventory ansible/playbooks/install_performance_monitor.yml --limit sql2022-a
```

Uninstall from all hosts:

```bash
ansible-playbook -i ansible/inventory ansible/playbooks/install_performance_monitor.yml -e perfmon_state=absent
```

Force re-run of install scripts even when already at the target version:

```bash
ansible-playbook -i ansible/inventory ansible/playbooks/install_performance_monitor.yml -e perfmon_force_reinstall=true
```

### Inventory

Named instance via SQL Browser:

```yaml
# inventory/hosts.yml
sql-win-01:
  ansible_host: pubs-dev.example.com
  ansible_connection: local
  mssql_instance: pubs
```

Named instance with a fixed TCP port:

```yaml
sql-win-01:
  ansible_host: pubs-dev.example.com
  ansible_connection: local
  mssql_port: 52791
```
