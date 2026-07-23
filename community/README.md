# Community tools - Offline cache

The installer downloads
[sp_WhoIsActive](https://raw.githubusercontent.com/amachanic/sp_whoisactive/refs/heads/master/sp_WhoIsActive.sql),
[DarlingData](https://raw.githubusercontent.com/erikdarlingdata/DarlingData/main/Install-All/DarlingData.sql),
and the [First Responder Kit](https://raw.githubusercontent.com/BrentOzarULTD/SQL-Server-First-Responder-Kit/refs/heads/main/Install-All-Scripts.sql)
scripts from GitHub during `docker compose up`.

The core PerformanceMonitor release zip is downloaded from GitHub the same way, and can be
pre-seeded here as well.

For air-gapped runs, pre-download everything here using:
```bash
bash scripts/fetch-community-tools.sh
bash scripts/fetch-perfmon-release.sh [perfmon_version]
```

Expected files: `sp_WhoIsActive.sql`, `DarlingData.sql`, `Install-All-Scripts.sql`,
`perfmon-<perfmon_version>.zip` (e.g. `perfmon-v3.x.x.zip`)
