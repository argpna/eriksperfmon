#!/bin/bash
# Memory-pressure demo workload against $TARGET_HOST - default: mssql-2025.
# Generates RESOURCE_SEMAPHORE waits and memory grant queue activity visible in
# PerfMon -> Memory -> Memory Grants: waiter_count, grantee_count, forced_grant_count.
#
# Mechanism: concurrent full-table sorts on a wide-row table request large
# memory grants from the resource semaphore pool. The pool is bounded at
# ~25% of committed target memory - ~100 MB with 512 MB max server memory,
# so queries beyond the first two must wait, producing waiter_count > 0.
set -u

SQLCMD=/opt/mssql-tools18/bin/sqlcmd
HOST=${TARGET_HOST:-mssql-2025}

q() { "$SQLCMD" -S "$HOST" -U sa -P "$MSSQL_SA_PASSWORD" -C -l 30 -t 120 "$@"; }

echo "workload-memory: waiting for $HOST"
until q -b -Q "SELECT 1" -o /dev/null; do sleep 5; done

q -d master -Q "
IF DB_ID(N'MemPressure') IS NULL CREATE DATABASE MemPressure;
" -o /dev/null 2>&1

# Wide-row table: 100 K rows x ~416 bytes = ~40 MB of row data.
# Sorting all rows with MAXDOP 1 triggers an optimizer memory grant of ~60 MB
# per query. Six concurrent sorts (360 MB requested) vs a ~100 MB semaphore
# pool means most queries wait on RESOURCE_SEMAPHORE, making waiter_count
# visible to the 1-minute PerfMon collector.
q -d MemPressure -Q "
IF OBJECT_ID(N'dbo.wide_rows') IS NULL
BEGIN
    CREATE TABLE dbo.wide_rows
    (
        id int       IDENTITY(1,1) PRIMARY KEY,
        c1 char(200) NOT NULL,
        c2 char(200) NOT NULL,
        sortkey uniqueidentifier NOT NULL
    );
    INSERT dbo.wide_rows (c1, c2, sortkey)
    SELECT TOP (100000)
        REPLICATE(CONVERT(char(1), ABS(CHECKSUM(NEWID())) % 26 + 65), 200),
        REPLICATE(CONVERT(char(1), ABS(CHECKSUM(NEWID())) % 26 + 65), 200),
        NEWID()
    FROM sys.all_columns a CROSS JOIN sys.all_columns b;
END;
" -o /dev/null 2>&1

# Cap server memory so the resource semaphore pool stays small.
q -Q "
EXEC sp_configure 'show advanced options', 1; RECONFIGURE;
EXEC sp_configure 'max server memory (MB)', 512; RECONFIGURE;
" -o /dev/null 2>&1

cycle=0
while true; do
    cycle=$((cycle + 1))
    echo "workload-memory cycle $cycle"

    # MAXDOP 1 forces single-threaded execution so the optimizer assigns the
    # full sort grant to one thread. Six concurrent single-threaded sorts
    # compete for the same resource semaphore, maximizing the chance that
    # waiter_count > 0 at collection time.
    for i in $(seq 1 6); do
        q -d MemPressure -Q "
SELECT id, c1, c2, sortkey
FROM dbo.wide_rows
ORDER BY sortkey OPTION (MAXDOP 1);" -o /dev/null 2>&1 &
    done
    wait

    # Hash-join pressure: join two copies of the table on a low-cardinality
    # key to force a hash build that also consumes memory grant headroom.
    for i in 1 2 3; do
        q -d MemPressure -Q "
SELECT TOP (1000)
    a.id,
    combined = a.c1 + b.c2
FROM dbo.wide_rows AS a
JOIN dbo.wide_rows AS b
    ON ABS(CHECKSUM(a.sortkey)) % 100 = ABS(CHECKSUM(b.sortkey)) % 100
ORDER BY a.sortkey OPTION (MAXDOP 1);" -o /dev/null 2>&1 &
    done
    wait

    sleep 10
done
