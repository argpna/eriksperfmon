#!/usr/bin/env bash
# Bootstraps a Grafana service account and API token using admin credentials.
#
# Required env vars:
#   GRAFANA_ADMIN_PASSWORD
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_ROOT}/.env"

# Source .env if present and GRAFANA_ADMIN_PASSWORD is not already set
if [ -f "$ENV_FILE" ] && [ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
: "${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD is required}"

gf_api() {
  local method=$1 path=$2
  shift 2
  curl -sf -X "$method" \
    -u "${GRAFANA_ADMIN_USER}:${GRAFANA_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    "${GRAFANA_URL}${path}" "$@"
}

echo "Checking for perfmon-ansible service account..." >&2
sa_json=$(gf_api GET "/api/serviceaccounts/search?query=perfmon-ansible")
sa_count=$(echo "$sa_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['totalCount'])")

if [ "$sa_count" -eq 0 ]; then
  echo "Creating perfmon-ansible service account..." >&2
  sa_json=$(gf_api POST "/api/serviceaccounts" -d '{"name":"perfmon-ansible","role":"Admin"}')
  sa_id=$(echo "$sa_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
else
  sa_id=$(echo "$sa_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['serviceAccounts'][0]['id'])")
fi
echo "Service account ID: ${sa_id}" >&2

tokens_json=$(gf_api GET "/api/serviceaccounts/${sa_id}/tokens")
token_ids=$(echo "$tokens_json" | python3 -c "
import sys, json
for t in json.load(sys.stdin):
    if t['name'] == 'perfmon-ansible-token':
        print(t['id'])
")
for tid in $token_ids; do
  echo "Deleting old token ${tid}..." >&2
  gf_api DELETE "/api/serviceaccounts/${sa_id}/tokens/${tid}" >/dev/null
done

echo "Creating new API token..." >&2
token_json=$(gf_api POST "/api/serviceaccounts/${sa_id}/tokens" -d '{"name":"perfmon-ansible-token"}')
api_key=$(echo "$token_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")

if [ -f "$ENV_FILE" ]; then
  if grep -q "^GRAFANA_API_KEY=" "$ENV_FILE"; then
    python3 -c "
import re, sys
path, key = sys.argv[1], sys.argv[2]
with open(path) as f:
    content = f.read()
content = re.sub(r'^GRAFANA_API_KEY=.*', 'GRAFANA_API_KEY=' + key, content, flags=re.MULTILINE)
with open(path, 'w') as f:
    f.write(content)
" "$ENV_FILE" "$api_key"
  else
    echo "GRAFANA_API_KEY=${api_key}" >> "$ENV_FILE"
  fi
  echo "GRAFANA_API_KEY written to ${ENV_FILE}" >&2
fi

echo "$api_key"
