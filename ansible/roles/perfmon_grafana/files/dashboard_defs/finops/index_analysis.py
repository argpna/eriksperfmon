from .._shared import *

# Upstream ref: RunIndexAnalysisAsync (DatabaseService.FinOps.IndexAnalysis.cs)
_CONTENT = (
    "## Index Analysis\n\n"
    "`sp_IndexCleanup` returns two result sets; the MSSQL datasource plugin only "
    "surfaces the first, so it is not run as a Grafana panel:\n\n"
    "**To run manually:**\n"
    "```sql\n"
    "EXEC dbo.sp_IndexCleanup @database_name = N'YourDb';\n"
    "```\n\n"
    "Or use the upstream PerformanceMonitor desktop application."
)


def index_analysis():
    panels = [text_panel("Index Analysis", 0, 0, 24, 8, _CONTENT)]
    return finops_dashboard(
        "finops-index-analysis",
        "FinOps · Index Analysis",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="",
    )
