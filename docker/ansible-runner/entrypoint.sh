#!/usr/bin/env bash
# Bootstraps the Grafana API key then runs the main Ansible playbook.
# GRAFANA_URL, GRAFANA_ADMIN_USER, and GRAFANA_ADMIN_PASSWORD must be
# available in the environment (set via docker-compose).
set -euo pipefail

echo "Bootstrapping Grafana API key..."
GRAFANA_API_KEY=$(/workspace/bootstrap/setup-grafana-api-key.sh)
export GRAFANA_API_KEY

# the AD test instance (compose profile ad) is detected by its network alias; skip its
# inventory host when the profile isn't running so the default stack stays AD-free.
LIMIT_ARGS=()
if getent hosts "mssql-ad.${AD_DOMAIN:?}" >/dev/null 2>&1; then
  echo "AD instance detected; waiting for its kerberos bootstrap..."
  _ad_ready=""
  for _ in $(seq 1 120); do
    if sqlcmd -S "mssql-ad.${AD_DOMAIN:?}" -U sa -P "$MSSQL_SA_PASSWORD" -C -b -h -1 \
        -Q "SET NOCOUNT ON; SELECT IS_SRVROLEMEMBER(N'sysadmin', N'${AD_NETBIOS:?}\\sqladmin');" 2>/dev/null \
        | grep -q '^ *1'; then
      _ad_ready=1
      break
    fi
    sleep 5
  done
  if [ -z "$_ad_ready" ]; then
    echo "AD instance never finished its kerberos bootstrap; check perfmon-mssql-ad logs" >&2
    exit 1
  fi
else
  echo "AD profile not running; skipping host sqlad (enable with: docker compose --profile ad up -d)"
  LIMIT_ARGS=(--limit '!sqlad')
fi

exec ansible-playbook \
  -i /workspace/ansible/inventory/docker \
  /workspace/ansible/playbooks/main.yml \
  "${LIMIT_ARGS[@]}"
