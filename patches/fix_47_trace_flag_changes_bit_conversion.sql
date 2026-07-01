/*
Upstream bug fix: report.trace_flag_changes CASE expression compares bit columns
to nvarchar literals N'ON' / N'OFF'.

Root cause: config.trace_flags_history.status is bit (0/1), but the change_description
CASE expression in 47_create_reporting_views.sql compares rc.previous_status and rc.status
to the string literals N'ON' and N'OFF'. SQL Server resolves the type mismatch by attempting
to convert the nvarchar literal to bit, which fails for 'OFF':

  Conversion failed when converting the nvarchar value 'OFF' to data type bit.

This makes the entire view unqueryable the moment any trace flag has been toggled and
a second history row exists (change_number > 1).

Fix: replace the nvarchar literals with 0 and 1, which convert to bit without error.

Upstream: https://github.com/erikdarlingdata/PerformanceMonitor install/47_create_reporting_views.sql
*/

/*
Copyright 2026 Darling Data, LLC
https://www.erikdarling.com/

*/

SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
SET IMPLICIT_TRANSACTIONS OFF;
SET STATISTICS TIME, IO OFF;
GO

USE PerformanceMonitor;
GO

/*
Configuration History View - Trace Flag Changes
Shows trace flag changes over time (enabled/disabled events)
Only shows actual changes, excludes initial baseline
*/
CREATE OR ALTER VIEW
    report.trace_flag_changes
AS
WITH
    ranked_changes AS
(
    SELECT
        h.collection_time,
        h.trace_flag,
        h.status,
        h.is_global,
        h.is_session,
        previous_status =
            LAG(h.status, 1) OVER
            (
                PARTITION BY
                    h.trace_flag,
                    h.is_global
                ORDER BY
                    h.collection_time
            ),
        change_number =
            ROW_NUMBER() OVER
            (
                PARTITION BY
                    h.trace_flag,
                    h.is_global
                ORDER BY
                    h.collection_time
            )
    FROM config.trace_flags_history AS h
)
SELECT
    change_time = rc.collection_time,
    trace_flag = rc.trace_flag,
    previous_status = rc.previous_status,
    new_status = rc.status,
    scope =
        CASE
            WHEN rc.is_global = 1 THEN N'GLOBAL'
            WHEN rc.is_session = 1 THEN N'SESSION'
            ELSE N'UNKNOWN'
        END,
    change_description =
        CASE
            WHEN rc.previous_status = 0 AND rc.status = 1
            THEN N'Trace flag ' + CONVERT(nvarchar(10), rc.trace_flag) + N' ENABLED'
            WHEN rc.previous_status = 1 AND rc.status = 0
            THEN N'Trace flag ' + CONVERT(nvarchar(10), rc.trace_flag) + N' DISABLED'
            ELSE N'Status unchanged'
        END,
    is_global = rc.is_global,
    is_session = rc.is_session
FROM ranked_changes AS rc
WHERE rc.change_number > 1;
GO

PRINT 'Created report.trace_flag_changes view (shows trace flag changes over time)';
GO

PRINT 'Patch applied: fix_47_trace_flag_changes_bit_conversion - replace nvarchar ON/OFF literals with 0/1 for bit column comparison';
GO
