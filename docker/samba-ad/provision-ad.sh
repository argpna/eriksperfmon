#!/bin/bash
# provisions AD objects for the perfmon AD-auth profile: accounts, SPNs, DNS
# records, and the SQL Server service keytab. runs alongside samba at container
# start and is idempotent - safe to re-run on every boot.
#
# required env: DOMAINPASS, MSSQL_AD_IP, AD_DOMAIN, AD_NETBIOS, AD_SQLADMIN_PASSWORD,
# AD_READER_PASSWORD, AD_SVC_MSSQL_PASSWORD (set in docker-compose.yml).
set -u

DNS_DOMAIN="${AD_DOMAIN:?}"
NETBIOS="${AD_NETBIOS:?}"
DC_NAME=sambadc
SQL_NAME=mssql-ad
SQL_IP="${MSSQL_AD_IP:?}"
KEYTAB_DIR=/ad-keytab

log() { echo "$(date -u +%H:%M:%S) $*"; }

dns_args=(-U administrator --password="${DOMAINPASS:?}")

# gate on the DNS RPC server (port 135), not local db access - samba-tool user/dns
# commands become usable at different times during startup
log "waiting for samba dns rpc..."
for _ in $(seq 1 120); do
    samba-tool dns zonelist 127.0.0.1 "${dns_args[@]}" >/dev/null 2>&1 && break
    sleep 5
done
if ! samba-tool dns zonelist 127.0.0.1 "${dns_args[@]}" >/dev/null 2>&1; then
    log "samba dns rpc never came up; aborting"
    exit 1
fi

DC_IP=$(hostname -I | awk '{print $1}')
log "provisioning against DC at ${DC_IP}"

ensure_user() {
    local user=$1 pass=$2
    if samba-tool user list | grep -qx "$user"; then
        # converge the password to the current env value (also bumps kvno, so the
        # keytab is always re-exported afterwards)
        samba-tool user setpassword "$user" --newpassword="$pass" >/dev/null
    else
        samba-tool user create "$user" "$pass" >/dev/null
    fi
    samba-tool user setexpiry "$user" --noexpiry
}

ensure_user svc_mssql "${AD_SVC_MSSQL_PASSWORD:?}"
ensure_user sqladmin "${AD_SQLADMIN_PASSWORD:?}"
ensure_user svc_grafana_reader "${AD_READER_PASSWORD:?}"
log "accounts ensured: svc_mssql, sqladmin, svc_grafana_reader"

for spn in "MSSQLSvc/${SQL_NAME}.${DNS_DOMAIN}" "MSSQLSvc/${SQL_NAME}.${DNS_DOMAIN}:1433"; do
    samba-tool spn list svc_mssql 2>/dev/null | grep -q "$spn" \
        || samba-tool spn add "$spn" svc_mssql
done
log "SPNs ensured on svc_mssql"

upsert_record() {
    local zone=$1 name=$2 type=$3 value=$4
    local fqdn current
    if [ "$type" = PTR ]; then
        fqdn="${name}.${zone}"
        current=$(dig +short @127.0.0.1 -x "${SUBNET_PREFIX}.${name}" | sed 's/\.$//')
    else
        fqdn="${name}.${zone}"
        current=$(dig +short @127.0.0.1 "$fqdn" "$type" | head -1)
    fi
    [ "$current" = "$value" ] && return 0
    if [ -n "$current" ]; then
        samba-tool dns delete 127.0.0.1 "$zone" "$name" "$type" "$current" "${dns_args[@]}" >/dev/null 2>&1
    fi
    if samba-tool dns add 127.0.0.1 "$zone" "$name" "$type" "$value" "${dns_args[@]}" >/dev/null 2>&1; then
        log "dns: ${name}.${zone} ${type} -> ${value}"
    else
        log "dns: FAILED to add ${name}.${zone} ${type} ${value}"
        return 1
    fi
}

SUBNET_PREFIX=$(echo "$DC_IP" | cut -d. -f1-3)
REV_ZONE="$(echo "$SUBNET_PREFIX" | awk -F. '{print $3"."$2"."$1}').in-addr.arpa"

# NetBIOS-name lookup: sqlservr resolves the bare domain prefix of DOMAIN\user as a
# plain A record to find the DC
# source: https://learn.microsoft.com/en-us/sql/linux/security/authentication/troubleshoot-active-directory
upsert_record "$DNS_DOMAIN" "$NETBIOS" A "$DC_IP"
upsert_record "$DNS_DOMAIN" "$DC_NAME" A "$DC_IP"
upsert_record "$DNS_DOMAIN" "$SQL_NAME" A "$SQL_IP"

# reverse zone + PTRs: OpenLDAP inside sqlservr verifies the DC via rDNS
if ! samba-tool dns zonelist 127.0.0.1 "${dns_args[@]}" 2>/dev/null | grep -q "$REV_ZONE"; then
    samba-tool dns zonecreate 127.0.0.1 "$REV_ZONE" "${dns_args[@]}" >/dev/null
    log "dns: created reverse zone $REV_ZONE"
fi
upsert_record "$REV_ZONE" "$(echo "$DC_IP" | cut -d. -f4)" PTR "${DC_NAME}.${DNS_DOMAIN}"
upsert_record "$REV_ZONE" "$(echo "$SQL_IP" | cut -d. -f4)" PTR "${SQL_NAME}.${DNS_DOMAIN}"

# keytab for sqlservr: SPN entries + the privileged account. re-exported every run
# because ensure_user's setpassword bumps kvno.
mkdir -p "$KEYTAB_DIR"
rm -f "$KEYTAB_DIR/mssql.keytab.tmp"
for principal in "MSSQLSvc/${SQL_NAME}.${DNS_DOMAIN}" "MSSQLSvc/${SQL_NAME}.${DNS_DOMAIN}:1433" svc_mssql; do
    samba-tool domain exportkeytab "$KEYTAB_DIR/mssql.keytab.tmp" --principal="$principal" >/dev/null
done
chmod 644 "$KEYTAB_DIR/mssql.keytab.tmp"
mv "$KEYTAB_DIR/mssql.keytab.tmp" "$KEYTAB_DIR/mssql.keytab"
log "keytab exported to $KEYTAB_DIR/mssql.keytab"
log "done"
