# perfmon_install

Installs [Erik Darling's PerformanceMonitor](https://github.com/erikdarlingdata/PerformanceMonitor)
on one or more SQL Server instances. All work runs on the Ansible control node via `sqlcmd`.

## What it does

`perfmon_state: present` (default):

1. Downloads the upstream release zip and extracts it to `perfmon_tmp_dir`.
2. Reads the installed version from `config.installation_history`. If the installed version is
   older than `perfmon_version`, runs the applicable upgrade script directories in order and records
   the result in `installation_history`.
3. Runs the install scripts in order, then:
   - installs community tools: `sp_WhoIsActive`, `DarlingData` suite, `FirstResponderKit`
   - applies any local patches from `perfmon_local_patches_dir`
   - runs `98_validate_installation.sql`

   Skipped entirely if the database is already at `perfmon_version`, unless
   `perfmon_force_reinstall: true`.
4. Creates the reader SQL login (`grafana_reader` by default), a dedicated server role
   (`grafana_reader_role`), and a dedicated database role in `PerformanceMonitor`. Each grant
   below is toggleable and on by default:
   - `VIEW SERVER STATE` - alert queries and the Queries dashboard's Real-Time Active Queries
     mode read live DMVs (`sys.dm_exec_requests`/`sys.dm_exec_sessions`) directly
   - `CONNECT ANY DATABASE` + `VIEW ANY DEFINITION` (granted as a pair) - lets the FinOps
     compression scan read `sys.tables`/`sys.indexes` across every database
   - SELECT on the `collect`, `report`, and `config` schemas in `PerformanceMonitor`
   - membership in `msdb`'s built-in `SQLAgentReaderRole` - needed for the Failed Collector Job
     alert

`perfmon_state: absent`:

Drops the `grafana_reader` login, its server and database roles, and its database users in
`PerformanceMonitor` and `msdb`, then runs the upstream `00_uninstall.sql` script, which drops
the PerformanceMonitor database, SQL Agent PerformanceMonitor -* jobs, and PerformanceMonitor_*
Extended Events sessions.

## Requirements

`sqlcmd` on the Ansible control node. The role auto-discovers common install paths
(`/opt/mssql-tools18/bin/sqlcmd`, `/opt/mssql-tools/bin/sqlcmd`) and fails with install
instructions if it cannot be found.

## Variables

| Variable | Default | Notes |
|---|---|---|
| `perfmon_state` | `present` | `present` installs or upgrades; `absent` runs the upstream uninstall script. |
| `perfmon_version` | `v3.2.0` | Release tag to install or upgrade to. Single source of truth - bump to upgrade. |
| `perfmon_force_reinstall` | `false` | Set to `true` to re-run all install scripts even when `installation_history` shows the instance is already at the target version. |
| `mssql_port` | _(unset)_ | TCP port. When set, always used in the connection string: `host,port` or `host\instance,port`. Omit only for named instances where SQL Browser is the only option. |
| `mssql_instance` | `MSSQLSERVER` | Instance name. `MSSQLSERVER` for the default instance, or a named instance. Named instances without a port fall back to `host\instance` (SQL Browser). |
| `perfmon_admin_sql_user` | `sa` | Sysadmin equivalent login used during install when `perfmon_admin_auth_mode` is `sql`. |
| `perfmon_admin_sql_password` | - | Required when `perfmon_admin_auth_mode` is `sql`. Supply via vault. |
| `perfmon_admin_auth_mode` | `sql` | Auth mode for the role's own admin connection. `sql` connects as `perfmon_admin_sql_user`/`perfmon_admin_sql_password`; `windows` runs `kinit` as `perfmon_admin_windows_upn` and connects with kerberos integrated auth - see Windows Authentication below. |
| `perfmon_admin_windows_upn` | - | Required when `perfmon_admin_auth_mode` is `windows`. UPN form, e.g. `sqladmin@LAB.INTERNAL`. Must already hold sysadmin on the instance. |
| `perfmon_admin_windows_password` | - | Required when `perfmon_admin_auth_mode` is `windows`. Supply via vault. |
| `perfmon_reader_password` | - | Required when `perfmon_reader_auth_mode` is `sql`. Password for the reader login. Supply via vault. |
| `perfmon_reader_login_name` | `grafana_reader` | Name of the SQL login created when `perfmon_reader_auth_mode` is `sql`. Change this if `grafana_reader` collides with an existing principal or your naming convention differs. Ignored when `perfmon_reader_auth_mode` is `windows` - see `perfmon_reader_windows_principal` below. |
| `perfmon_reader_grant_view_server_state` | `true` | Grant `VIEW SERVER STATE` to `grafana_reader_role`. Required for DMV-backed alerts and dashboards (blocking, long-running queries). Set to `false` to revoke it and run with a narrower reader login. |
| `perfmon_reader_grant_cross_db_metadata` | `true` | Grant `CONNECT ANY DATABASE` + `VIEW ANY DEFINITION` to `grafana_reader_role` (paired - `CONNECT ANY DATABASE` alone still hides catalog-view rows in databases the reader login isn't a member of). Required for the FinOps compression scan, which reads `sys.tables`/`sys.indexes` across every database. Set to `false` to revoke both and run with a narrower reader login. |
| `perfmon_reader_grant_msdb_access` | `true` | Add the reader login to `msdb`'s built-in `SQLAgentReaderRole`, needed for the Running Jobs collector and Failed Collector Job alert. Set to `false` to drop the role membership and keep it out of `msdb`. |
| `perfmon_reader_auth_mode` | `sql` | `sql` creates a SQL login (`perfmon_reader_login_name`) with `perfmon_reader_password`. `windows` creates an AD login/group via `CREATE LOGIN ... FROM WINDOWS` instead - see Windows Authentication below. |
| `perfmon_reader_windows_principal` | - | Required when `perfmon_reader_auth_mode` is `windows`. Down-level form, e.g. `LAB\svc_grafana_reader` (an individual account) or `LAB\SQLReaders` (a group). Used as the login name directly - an AD account can't be renamed to match `perfmon_reader_login_name`. |
| `perfmon_db` | `PerformanceMonitor` | Database name. |
| `perfmon_skip_validation_failures` | `false` | Treat validate installation problems as a warning instead of a failure. |
| `sqlcmd_bin` | `sqlcmd` | Path to sqlcmd if not on PATH. |
| `sqlcmd_trust_server_cert` | `true` | Pass `-C` (TrustServerCertificate) to every sqlcmd connection. The default accepts SQL Server's out-of-box self-signed certificate without validation; traffic is still encrypted. Set to `false` to validate the server certificate. |
| `sqlcmd_encryption` | `""` | Encryption level: `strict`, `mandatory`, or `optional` (sqlcmd `-Ns`/`-Nm`/`-No`). Empty uses the driver default (mandatory with mssql-tools18). |
| `sqlcmd_server_certificate` | `""` | Control-node path to the server certificate file (sqlcmd `-J`). Only valid with `sqlcmd_encryption: strict`; the role fails fast otherwise. |
| `sqlcmd_hostname_in_cert` | `""` | Hostname expected in the server certificate when it differs from the connect name (sqlcmd `-F`). |
| `perfmon_tmp_dir` | `/tmp/perfmon-install` | Staging directory on the control node for the downloaded release zip and community tool files. Download/extract tasks run once per play, not once per host, so this must resolve to the same path for every host in a play. |
| `perfmon_local_patches_dir` | `""` | Path to a directory of `.sql` patch files to apply after the install scripts. Leave empty to skip. |
| `perfmon_community_dir` | `{{ playbook_dir }}/../../community` | Directory checked for a pre-seeded release zip and community tool `.sql` files before downloading. |
| `perfmon_community_tools` | see defaults | List of `{name, url}` entries to download and install. Pre-populate `<name>.sql` in `perfmon_community_dir` for air-gapped installs. |

## Required credentials

`perfmon_admin_sql_password` is required when `perfmon_admin_auth_mode` is `sql` (the default);
`perfmon_admin_windows_upn` and `perfmon_admin_windows_password` are required when it's
`windows` instead. `perfmon_reader_password` is required when `perfmon_reader_auth_mode` is
`sql` (the default); `perfmon_reader_windows_principal` is required when it's `windows`
instead. The recommended way is Ansible Vault:

```yaml
# group_vars/sql_servers.yml
perfmon_admin_sql_password: "{{ vault_perfmon_admin_sql_password }}"
perfmon_reader_password: "{{ vault_perfmon_reader_password }}"
```

## Windows Authentication

Two independent connections can each use AD/kerberos auth:

**Reader login** (`perfmon_reader_auth_mode: windows`): the reader login is created as an
Active Directory principal instead of a SQL login - set `perfmon_reader_windows_principal` (a
down-level `DOMAIN\principal` or `DOMAIN\group` name). The role only issues `CREATE LOGIN ...
FROM WINDOWS` and role memberships; the instance must already accept AD auth.

**Admin connection** (`perfmon_admin_auth_mode: windows`): the role's own install/admin
`sqlcmd` connections authenticate via kerberos instead of sql-auth. The role runs `kinit` as
`perfmon_admin_windows_upn` into a per-host ticket cache and `sqlcmd` connects with integrated
auth. Control-node requirements: `kinit` (krb5 client tools), an `/etc/krb5.conf` pointing at
the domain's KDC (with `rdns = false` if reverse DNS doesn't resolve cleanly), `ansible_host`
set to the FQDN matching the instance's `MSSQLSvc` SPN, and the principal already granted
sysadmin. The control node does **not** need to be domain-joined - a reachable KDC and valid
credentials are enough. Note kerberos requires `mssql_port` to be present in the SPN, so keep
`mssql_port` set.

## TLS

All connections are encrypted (the driver default). By default the role also passes `-C`
(TrustServerCertificate), which skips certificate validation, and is open to MITM on an untrusted network.

To validate the server certificate instead, set `sqlcmd_trust_server_cert: false` (per host or
group). Validation then runs against the control node's OS trust store, so the CA that signed
the instance's certificate must be installed there. If the certificate's subject doesn't match the name
in `ansible_host`, set `sqlcmd_hostname_in_cert`.

For TDS 8.0 strict encryption, set `sqlcmd_encryption: strict`; there the certificate can
alternatively be pinned by file path with `sqlcmd_server_certificate` instead of going through
the trust store. `-C`/`sqlcmd_trust_server_cert` has no effect in strict mode - the server cert
must satisfy either the trust store or `-J`, no TrustServerCertificate escape hatch exists.
Strict mode's minimum SQL Server version depends on the target's OS: 2022 (16.x) or later on
Windows, 2025 (17.x) or later on Linux.

## Air-gapped installs

Pre-populate `perfmon-<perfmon_version>.zip`, `sp_WhoIsActive.sql`, `DarlingData.sql`, and
`FirstResponderKit.sql` in `perfmon_community_dir`. Defaults to `community/` in this repo;
override in group_vars or extra-vars if needed. The role checks for these files before attempting
any downloads, including the core release zip.

## Upgrading

1. Back up the `PerformanceMonitor` database before upgrading.
2. Bump `perfmon_version` in `defaults/main.yml` (or in `group_vars` or pass
   `-e perfmon_version=vX.Y.Z`) and re-run the role.
3. The role reads `config.installation_history` to detect the installed version, then runs only
   the upgrade directories that fall within the version gap (e.g. `3.0.0-to-3.1.0/` for a
   3.0.0 to 3.1.0 upgrade). Upgrade results are recorded in `installation_history`.
4. Downgrade is rejected. The role fails if `perfmon_version` is older than the recorded
   installed version.
5. Run `scripts/verify-panels.py <datasource-uid>` from the `eriksperfmon` repo against a live
   Grafana datasource. Any panel query that references a renamed or dropped column will fail
   with a SQL error. Fix column references in the role's `files/dashboard_defs/` modules and regenerate.

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
        perfmon_version: v3.2.0
        mssql_port: 1433
        perfmon_admin_sql_password: "{{ vault_sa_password }}"
        perfmon_reader_password: "{{ vault_reader_password }}"
```

Or run the included playbook directly:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/install_performance_monitor.yml
```

Install on a single host:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/install_performance_monitor.yml --limit sql01
```

Uninstall from all hosts:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/install_performance_monitor.yml -e perfmon_state=absent
```

Force re-run of install scripts even when already at the target version:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/install_performance_monitor.yml -e perfmon_force_reinstall=true
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
