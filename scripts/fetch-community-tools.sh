#!/bin/bash
# Pre-download the community tool scripts into ./community for offline installs.
set -euo pipefail
cd "$(dirname "$0")/../community"

curl -fsSL -o sp_WhoIsActive.sql https://raw.githubusercontent.com/amachanic/sp_whoisactive/refs/heads/master/sp_WhoIsActive.sql
curl -fsSL -o DarlingData.sql https://raw.githubusercontent.com/erikdarlingdata/DarlingData/main/Install-All/DarlingData.sql
curl -fsSL -o FirstResponderKit.sql https://raw.githubusercontent.com/BrentOzarULTD/SQL-Server-First-Responder-Kit/refs/heads/main/Install-All-Scripts.sql
ls -la *.sql
