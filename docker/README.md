# Docker environment

Self-contained stack: SQL Server instances covering multiple versions and auth modes,
workload generators, Grafana, and an Ansible runner that provisions everything automatically.
See the Services table below for the current set.

See `docker-compose.yml` at the repo root for the full service definition.

## Services

| Service | Container | Port (host) | Purpose |
|---|---|---|---|
| `mssql-2022` | `perfmon-mssql-2022` | 14333 | SQL Server 2022 - primary workload instance |
| `mssql-2025` | `perfmon-mssql-2025` | 14334 | SQL Server 2025 - memory pressure instance |
| `workload` | `perfmon-workload` | - | Generates realistic query load against `mssql-2022` |
| `workload-memory` | `perfmon-workload-memory` | - | Memory-pressure workload against `mssql-2025` |
| `grafana` | `perfmon-grafana` | 3000 | Grafana UI with provisioned dashboards |
| `ansible-runner` | `perfmon-ansible-runner` | - | Runs the full Ansible playbook inside the stack |
| `samba-ad` (profile `ad`) | `perfmon-samba-ad` | - | Samba AD DC for the opt-in AD-auth tests |
| `mssql-ad` (profile `ad`) | `perfmon-mssql-ad` | 14335 | SQL Server 2022 authenticated via kerberos/AD only |

All SQL Server instances run as Developer Edition with SQL Agent enabled.

## AD authentication (profile `ad`)

`docker compose --profile ad up -d` adds a Samba AD DC (`AD_REALM`, `lab.internal`/
`LAB.INTERNAL` by default) and a third SQL Server instance installed and queried entirely
over kerberos - the install role's admin connection (`perfmon_admin_auth_mode: windows`,
`kinit` + integrated-auth `sqlcmd`), the reader login (`CREATE LOGIN ... FROM WINDOWS`), and
the Grafana datasource (`Windows AD: Username + password`) all authenticate against the DC;
`sa` is unused for that instance after its bootstrap.

Domain identity is configured through `AD_DOMAIN`/`AD_REALM`/`AD_NETBIOS` in `.env`, the single
source of truth referenced by `docker-compose.yml`, every `docker/*/*.sh` script in the AD
profile, and `ansible/inventory/docker-ad/hosts.yml` (via `lookup('env', ...)`). Change the domain
there, not in any individual file.

Moving parts, in start order:

1. `samba-ad` provisions the domain on first boot, then `docker/samba-ad/provision-ad.sh`
   (re-)creates the test accounts (`sqladmin`, `svc_grafana_reader`, `svc_mssql`), the
   `MSSQLSvc` SPNs, the DNS records SQL Server's AD principal lookup requires (NetBIOS-name A
   record, reverse zone + PTRs - sqlservr resolves `AD_NETBIOS` as a plain A record and
   verifies the DC via rDNS), and exports the service keytab into the shared `ad-keytab`
   volume.
2. `mssql-ad` (`docker/mssql-ad/entrypoint.sh`) waits for the keytab, points its resolv.conf
   directly at the DC (docker's embedded DNS would derail the NetBIOS and PTR lookups with
   network-scoped names), registers the keytab via `mssql-conf`, then bootstraps
   `AD_NETBIOS\sqladmin` as sysadmin. Its healthcheck passes only after that bootstrap.
3. `ansible-runner` detects the instance via its `mssql-ad.$AD_DOMAIN` network alias, waits
   for the bootstrap, then runs the normal playbook with the `ansible/inventory/docker-ad/`
   overlay inventory added (a second `-i`) - its `sqlad` host carries the windows-mode
   variables. Without the profile the overlay is not loaded, so the default stack never
   provisions a `sqlad` datasource, fleet row, or alert rules, and a stale `sqlad`
   datasource from an earlier profile run is deleted as orphaned.

Kerberos client config for the runner, Grafana, and `mssql-ad` is generated at `docker compose
up` time from the `krb5_conf` entry in `docker-compose.yml`'s top-level `configs:` block (note
`rdns = false`), interpolating `AD_DOMAIN`/`AD_REALM` from `.env` - there is no static
`krb5.conf` file to edit. Domain passwords default to test values - override with the `AD_*`
variables in `.env`. The DC keeps state in the `samba-ad-data` volume;
`docker compose down --profile ad -v` resets the whole domain.

## Prerequisites

- Docker with Compose v2
- ~6 GB free RAM, approximately 2 GB per SQL Server instance
- Ports 14333, 14334, and 3000 free on the host

## Quick start

```bash
cp .env.example .env
# Edit .env - set MSSQL_SA_PASSWORD, MSSQL_READER_PASSWORD, GRAFANA_ADMIN_PASSWORD
docker compose up -d
```

The `ansible-runner` container runs the full Ansible playbook automatically once all SQL Server
and Grafana health checks pass. First run: approximately 5-10 minutes for image build, PerformanceMonitor
install on both instances, first collection cycle.

```bash
docker compose logs -f ansible-runner # watch provisioning progress
```

Grafana starts at **http://localhost:3000**. Panels show "datasource not found" until
`ansible-runner` completes. Start at **Fleet Overview** once it exits.

## Environment variables

Defined in `.env`:

| Variable | Purpose |
|---|---|
| `MSSQL_SA_PASSWORD` | SA password for both SQL Server instances |
| `MSSQL_READER_PASSWORD` | Password for the `grafana_reader` login created on each instance |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password |
| `SAMBA_ADMIN_PASSWORD` | (profile `ad`, optional) Samba domain administrator password |
| `AD_SQLADMIN_PASSWORD` | (profile `ad`, optional) AD account the install role authenticates as |
| `AD_READER_PASSWORD` | (profile `ad`, optional) AD account the Grafana datasource authenticates as |
| `AD_SVC_MSSQL_PASSWORD` | (profile `ad`, optional) SQL Server service account holding the SPNs |
| `AD_DOMAIN` | (profile `ad`, optional) DNS domain, e.g. `lab.internal` - single source of truth for the domain identity |
| `AD_REALM` | (profile `ad`, optional) Kerberos realm, uppercase form of `AD_DOMAIN`, e.g. `LAB.INTERNAL` |
| `AD_NETBIOS` | (profile `ad`, optional) Down-level/NetBIOS domain name, e.g. `LAB` |

The SA password must meet SQL Server's complexity requirements (uppercase, lowercase, digit,
symbol; minimum 8 characters).

## SQL Server version notes

Both instances use `mssql-tools18` at `/opt/mssql-tools18/bin/sqlcmd` and connect with TLS
using `tlsSkipVerify`. The Ansible role runs Erik's numbered install scripts, in order,
against both instances.

## Workload generator

The `workload` container runs `scripts/workload.sh` against `mssql-2022` in a loop. Each cycle:

- Stored procedure calls with alternating parameters
- Ad-hoc query bursts
- DDL events
- Single-use plan generation then periodic `DBCC FREEPROCCACHE`
- Blocking pair (holder waits 40s, victim times out)
- Deadlock attempt every third cycle

The `workload-memory` container runs `scripts/workload-memory.sh` against `mssql-2025`, generating
memory-pressure workload, `RESOURCE_SEMAPHORE` waits, memory grant queue activity. Both instances
have active workload and populate the collector tables so dashboards have data to display.

## Re-running Ansible

To re-provision after changing Ansible roles or inventory; for example, after modifying datasource
settings:

```bash
docker compose up ansible-runner
```

The `ansible-runner` service has `restart: "no"`, so it runs once and stops. `docker compose up`
starts it again from the beginning.

## Stopping and cleanup

```bash
docker compose down # stop containers, but keep volumes
docker compose down -v # stop containers and delete all data volumes
docker compose --profile ad down -v # stop and clean up optional ad profile resources
```

## Connecting directly to a SQL Server instance

From the host (using `sqlcmd` from mssql-tools18):

```bash
sqlcmd -S localhost,14333 -U sa -P "$MSSQL_SA_PASSWORD" -C # 2022
sqlcmd -S localhost,14334 -U sa -P "$MSSQL_SA_PASSWORD" -C # 2025
```

From inside the stack, use the container hostnames (`mssql-2022`, `mssql-2025`) on port 1433.

## Ansible inventory

The docker-internal inventory lives at `ansible/inventory/docker/`, with the opt-in AD
instance in the `ansible/inventory/docker-ad/` overlay. Both are only used by the
`ansible-runner` container; they are not intended for use from the host. The host-facing
inventory is `ansible/inventory/`.

## Smoke-testing panels

After provisioning completes, run the panel smoke test against the 2025 instance:

```bash
GRAFANA_ADMIN_PASSWORD=<grafana-password> python3 scripts/verify-panels.py
# or against a specific version:
GRAFANA_ADMIN_PASSWORD=<grafana-password> python3 scripts/verify-panels.py perfmon-ds-sql2022
```

A SQL error prints `FAIL` and causes a non-zero exit. Zero rows is not a failure.
