# Docker demo environment

Self-contained demo stack: two SQL Server instances (2022 and 2025),
two workload generators, Grafana, and an Ansible runner that provisions everything automatically.

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

Both SQL Server instances run as Developer Edition with SQL Agent enabled.

## Prerequisites

- Docker with Compose v2
- ~6 GB free RAM, approximately 2 GB per SQL Server instance
- Ports 14333, 14334, and 3000 free on the host

## Quick start

```bash
cp .env.example .env
# Edit .env - set MSSQL_SA_PASSWORD, GRAFANA_READER_PASSWORD, GRAFANA_ADMIN_PASSWORD
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
| `GRAFANA_READER_PASSWORD` | Password for the `grafana_reader` login created on each instance |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password |

The SA password must meet SQL Server's complexity requirements (uppercase, lowercase, digit,
symbol; minimum 8 characters).

## SQL Server version notes

Both instances use `mssql-tools18` at `/opt/mssql-tools18/bin/sqlcmd` and connect with TLS
using `tlsSkipVerify`. The Ansible role runs Erik's install scripts (01-54) against both
instances.

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
```

## Connecting directly to a SQL Server instance

From the host (using `sqlcmd` from mssql-tools18):

```bash
sqlcmd -S localhost,14333 -U sa -P "$MSSQL_SA_PASSWORD" -C # 2022
sqlcmd -S localhost,14334 -U sa -P "$MSSQL_SA_PASSWORD" -C # 2025
```

From inside the stack, use the container hostnames (`mssql-2022`, `mssql-2025`) on port 1433.

## Ansible inventory

The docker-internal inventory lives at `ansible/inventory/docker/`. It is only used by the
`ansible-runner` container; it is not intended for use from the host. The host-facing inventory
for production use is `ansible/inventory/`.

## Smoke-testing panels

After provisioning completes, run the panel smoke test against the 2025 instance:

```bash
GRAFANA_ADMIN_PASSWORD=<grafana-password> python3 scripts/verify-panels.py
# or against a specific version:
GRAFANA_ADMIN_PASSWORD=<grafana-password> python3 scripts/verify-panels.py perfmon-ds-sql2022
```

A SQL error prints `FAIL` and causes a non-zero exit. Zero rows is not a failure.
