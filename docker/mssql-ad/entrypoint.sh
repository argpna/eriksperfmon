#!/bin/bash
# entrypoint for the AD-authenticated tests (runs as root, then drops to mssql
# for sqlservr itself). waits for the samba DC's keytab, points DNS directly at the
# DC, configures kerberos, then bootstraps the AD admin login before handing off.
#
# DNS must bypass docker's embedded resolver here: sqlservr resolves the NetBIOS
# domain name as an A record and verifies the DC via PTR, and the embedded resolver
# answers the container's own canonical name and PTRs with network-scoped names,
# which derails both checks. other containers (grafana, ansible-runner) keep the
# embedded resolver and reach AD names through the compose network aliases.
set -euo pipefail

KEYTAB_SRC=/ad-keytab/mssql.keytab
KEYTAB=/var/opt/mssql/secrets/mssql.keytab
SQLCMD=/opt/mssql-tools18/bin/sqlcmd

echo "waiting for AD keytab from samba-ad..."
for _ in $(seq 1 120); do
    [ -f "$KEYTAB_SRC" ] && break
    sleep 5
done
[ -f "$KEYTAB_SRC" ] || { echo "keytab never appeared at $KEYTAB_SRC" >&2; exit 1; }

: "${AD_DOMAIN:?}" "${AD_NETBIOS:?}"

DC_IP=$(getent hosts "sambadc.${AD_DOMAIN}" | awk '{print $1}' | head -1)
[ -n "$DC_IP" ] || { echo "cannot resolve sambadc.${AD_DOMAIN}" >&2; exit 1; }
printf 'nameserver %s\nsearch %s\n' "$DC_IP" "$AD_DOMAIN" > /etc/resolv.conf
echo "resolv.conf -> DC at $DC_IP"

mkdir -p /var/opt/mssql/secrets
cp "$KEYTAB_SRC" "$KEYTAB"
chown mssql "$KEYTAB"
chmod 400 "$KEYTAB"
/opt/mssql/bin/mssql-conf set network.kerberoskeytabfile "$KEYTAB"
/opt/mssql/bin/mssql-conf set network.privilegedadaccount svc_mssql
/opt/mssql/bin/mssql-conf set network.enablekdcfromkrb5conf true
chown -R mssql /var/opt/mssql

su -m -s /bin/bash mssql -c /opt/mssql/bin/sqlservr &
SQLPID=$!
trap 'kill -TERM $SQLPID 2>/dev/null' TERM INT

echo "waiting for sql server..."
for _ in $(seq 1 60); do
    "$SQLCMD" -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -b -Q "SELECT 1" -o /dev/null 2>/dev/null && break
    sleep 5
done

# retried because the DC may still be settling when sql server first comes up
echo "bootstrapping ${AD_NETBIOS}\\sqladmin as sysadmin..."
for _ in $(seq 1 60); do
    if "$SQLCMD" -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -b -Q "
        IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'${AD_NETBIOS}\\sqladmin')
            CREATE LOGIN [${AD_NETBIOS}\\sqladmin] FROM WINDOWS;
        IF IS_SRVROLEMEMBER(N'sysadmin', N'${AD_NETBIOS}\\sqladmin') = 0
            ALTER SERVER ROLE sysadmin ADD MEMBER [${AD_NETBIOS}\\sqladmin];" -o /dev/null 2>/dev/null; then
        echo "AD admin bootstrap complete"
        break
    fi
    sleep 10
done

wait $SQLPID
