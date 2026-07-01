#!/bin/bash
# Demo workload against $TARGET_HOST so dashboards have something to show.
# Covers: stored proc stats, DDL trace events, plan cache pressure cycles,
# blocking, and deadlocks.
set -u

SQLCMD=/opt/mssql-tools18/bin/sqlcmd
HOST=${TARGET_HOST:-mssql-2022}

q() { "$SQLCMD" -S "$HOST" -U sa -P "$MSSQL_SA_PASSWORD" -C -l 30 -t 120 "$@"; }

echo "workload: waiting for $HOST"
until q -b -Q "SELECT 1" -o /dev/null; do sleep 5; done

q -d master -Q "
IF DB_ID(N'WorkloadDemo') IS NULL CREATE DATABASE WorkloadDemo;
"

q -d WorkloadDemo -Q "
IF OBJECT_ID(N'dbo.orders') IS NULL
BEGIN
    CREATE TABLE dbo.orders (id integer PRIMARY KEY, customer integer NOT NULL, amount decimal(10,2) NOT NULL, note nvarchar(400));
    INSERT dbo.orders (id, customer, amount, note)
    SELECT TOP (50000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)), ABS(CHECKSUM(NEWID())) % 1000, ABS(CHECKSUM(NEWID())) % 10000 / 100.0, REPLICATE(N'x', 100)
    FROM sys.all_columns a CROSS JOIN sys.all_columns b;
END;
"

# Extra rows for customer 42 skew the revenue distribution visibly in proc stats.
q -d WorkloadDemo -Q "
IF NOT EXISTS (SELECT 1 FROM dbo.orders WHERE id > 50000)
    INSERT dbo.orders (id, customer, amount, note)
    SELECT TOP (5000) 50000 + ROW_NUMBER() OVER (ORDER BY (SELECT NULL)), 42, ABS(CHECKSUM(NEWID())) % 5 / 1.0, REPLICATE(N'y', 100)
    FROM sys.all_columns a CROSS JOIN sys.all_columns b;
"

q -d WorkloadDemo -Q "
CREATE OR ALTER PROCEDURE dbo.usp_customer_summary @min_amount decimal(10,2)
AS
    SELECT TOP (20) customer, total = SUM(amount), cnt = COUNT(*)
    FROM dbo.orders
    WHERE amount > @min_amount
    GROUP BY customer
    ORDER BY total DESC;
"

q -d WorkloadDemo -Q "
CREATE OR ALTER PROCEDURE dbo.usp_order_scan @lo integer, @hi integer
AS
    SELECT id, customer, amount
    FROM dbo.orders
    WHERE id BETWEEN @lo AND @hi
    ORDER BY amount DESC;
"

q -d WorkloadDemo -Q "
CREATE OR ALTER PROCEDURE dbo.usp_top_customers @top_n integer
AS
    SELECT TOP (@top_n) customer, order_count = COUNT(*), revenue = SUM(amount)
    FROM dbo.orders
    GROUP BY customer
    ORDER BY revenue DESC;
"

# Establish a known baseline for server configuration so the first workload toggle
# registers as a real delta in config.server_configuration_history.
# show advanced options must be 1 before cost threshold for parallelism can be changed.
# max server memory capped at 512 MB so memory pressure remains visible on dashboards.
q -Q "
EXEC sp_configure 'show advanced options', 1;
RECONFIGURE;
EXEC sp_configure 'max server memory (MB)', 512;
EXEC sp_configure 'cost threshold for parallelism', 5;
EXEC sp_configure 'optimize for ad hoc workloads', 0;
RECONFIGURE;
" -o /dev/null 2>&1

cycle=0
while true; do
    cycle=$((cycle + 1))
    echo "workload cycle $cycle"

    # Stored proc calls: populates collect.procedure_stats for all three procs
    q -d WorkloadDemo -Q "EXEC dbo.usp_customer_summary @min_amount = 0.01;" -o /dev/null 2>&1
    q -d WorkloadDemo -Q "EXEC dbo.usp_order_scan @lo = 1, @hi = 50000;" -o /dev/null 2>&1
    q -d WorkloadDemo -Q "EXEC dbo.usp_order_scan @lo = 1, @hi = 500;" -o /dev/null 2>&1
    q -d WorkloadDemo -Q "EXEC dbo.usp_top_customers @top_n = 50;" -o /dev/null 2>&1
    q -d WorkloadDemo -Q "EXEC dbo.usp_top_customers @top_n = 10;" -o /dev/null 2>&1

    # Steady ad-hoc read traffic
    for i in 1 2 3; do
        q -d WorkloadDemo -Q "
SELECT c = COUNT_BIG(*) FROM dbo.orders o1 JOIN dbo.orders o2 ON o1.customer = o2.customer WHERE o1.amount > $((RANDOM % 50)) OPTION (MAXDOP 1);
SELECT TOP (100) customer, total = SUM(amount) FROM dbo.orders GROUP BY customer ORDER BY total DESC;
" -o /dev/null 2>&1
    done

    # DDL events: generates Object:Created, Object:Altered, Object:Deleted in the default trace
    q -d WorkloadDemo -Q "
IF OBJECT_ID(N'dbo.demo_staging') IS NOT NULL DROP TABLE dbo.demo_staging;
CREATE TABLE dbo.demo_staging (id integer PRIMARY KEY, val decimal(10,2), loaded_at datetime2(0) DEFAULT SYSDATETIME());
ALTER TABLE dbo.demo_staging ADD note nvarchar(200) NULL;
DROP TABLE dbo.demo_staging;
" -o /dev/null 2>&1

    # Single-use ad-hoc plans build up plan cache so periodic
    # DBCC FREEPROCCACHE produces a measurable drop and regrowth
    for i in $(seq 1 20); do
        q -d WorkloadDemo -Q "SELECT id_check = $i, cnt = COUNT(*) FROM dbo.orders WHERE id = $i OR id = $((i + 10000));" -o /dev/null 2>&1
    done

    # Blocking pair: holder keeps a row locked 40s, victim waits on it
    q -d WorkloadDemo -Q "
BEGIN TRAN;
UPDATE dbo.orders SET amount = amount WHERE id = 1;
WAITFOR DELAY '00:00:40';
ROLLBACK;
" -o /dev/null 2>&1 &
    sleep 3
    q -d WorkloadDemo -Q "
SET LOCK_TIMEOUT 30000;
UPDATE dbo.orders SET note = note WHERE id = 1;
" -o /dev/null 2>&1 &

    # Deadlock attempt every 3rd cycle: two sessions updating rows in opposite order
    if [ $((cycle % 3)) = 0 ]; then
        q -d WorkloadDemo -Q "
BEGIN TRAN;
UPDATE dbo.orders SET amount = amount WHERE id = 100;
WAITFOR DELAY '00:00:05';
UPDATE dbo.orders SET amount = amount WHERE id = 200;
ROLLBACK;
" -o /dev/null 2>&1 &
        q -d WorkloadDemo -Q "
BEGIN TRAN;
UPDATE dbo.orders SET amount = amount WHERE id = 200;
WAITFOR DELAY '00:00:05';
UPDATE dbo.orders SET amount = amount WHERE id = 100;
ROLLBACK;
" -o /dev/null 2>&1 &
    fi

    # Plan cache flush every 10th cycle: causes plan_cache_state to show GROWTH
    # as the cache rebuilds from near-zero after the flush.
    if [ $((cycle % 10)) = 0 ]; then
        q -Q "DBCC FREEPROCCACHE;" -o /dev/null 2>&1
    fi

    # Server configuration changes: alternate cost threshold for parallelism (5 vs 50)
    # and optimize for ad hoc workloads (0 vs 1). The collector is scheduled daily
    # - frequency_minutes=1440, so we invoke it immediately after each change instead
    # of relying on the SQL Agent schedule to catch the transition.
    if [ $((cycle % 4)) -eq 1 ]; then
        q -Q "EXEC sp_configure 'cost threshold for parallelism', 50; EXEC sp_configure 'optimize for ad hoc workloads', 1; RECONFIGURE;" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.server_configuration_collector;" -o /dev/null 2>&1
    elif [ $((cycle % 4)) -eq 3 ]; then
        q -Q "EXEC sp_configure 'cost threshold for parallelism', 5; EXEC sp_configure 'optimize for ad hoc workloads', 0; RECONFIGURE;" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.server_configuration_collector;" -o /dev/null 2>&1
    fi

    # Database configuration changes: toggle AUTO_UPDATE_STATISTICS and the database-scoped MAXDOP override.
    # Collector also invoked directly for the same reason as above.
    if [ $((cycle % 6)) -eq 2 ]; then
        q -d master -Q "ALTER DATABASE WorkloadDemo SET AUTO_UPDATE_STATISTICS OFF;" -o /dev/null 2>&1
        q -d WorkloadDemo -Q "ALTER DATABASE SCOPED CONFIGURATION SET MAXDOP = 4;" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.database_configuration_collector;" -o /dev/null 2>&1
    elif [ $((cycle % 6)) -eq 5 ]; then
        q -d master -Q "ALTER DATABASE WorkloadDemo SET AUTO_UPDATE_STATISTICS ON;" -o /dev/null 2>&1
        q -d WorkloadDemo -Q "ALTER DATABASE SCOPED CONFIGURATION SET MAXDOP = 0;" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.database_configuration_collector;" -o /dev/null 2>&1
    fi

    # Trace flag changes: enable and disable trace flags 1222 and 3604. Trace flags
    # are collected by server_configuration_collector, so that procedure is invoked
    # immediately after each toggle.
    if [ $((cycle % 7)) -eq 1 ]; then
        q -Q "DBCC TRACEON(1222, -1); DBCC TRACEON(3604, -1);" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.server_configuration_collector;" -o /dev/null 2>&1
    elif [ $((cycle % 7)) -eq 5 ]; then
        q -Q "DBCC TRACEOFF(1222, -1); DBCC TRACEOFF(3604, -1);" -o /dev/null 2>&1
        q -d PerformanceMonitor -Q "EXEC collect.server_configuration_collector;" -o /dev/null 2>&1
    fi

    # Severe error: severity 20 WITH LOG writes to the SQL Server error log and to
    # the system_health XE session ring buffer, which sp_HealthParser reads to
    # populate collect.HealthParser_SevereErrors.
    if [ $((cycle % 9)) -eq 0 ]; then
        q -Q "RAISERROR(N'PerfMon demo: simulated severe error for dashboard validation', 20, 1) WITH LOG;" -o /dev/null 2>&1
    fi

    wait
    sleep 10
done
