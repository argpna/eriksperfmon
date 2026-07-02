from .._shared import *

_CONTENT = (
    "## Index Analysis\n\n"
    "Index Analysis requires running `sp_IndexCleanup` interactively and is not "
    "supported at the moment:\n\n"
    "**To run manually:**\n"
    "```sql\n"
    "EXEC dbo.sp_IndexCleanup @database_name = N'YourDb';\n"
    "```\n\n"
    "Or use the upstream PerformanceMonitor desktop application."
)


def index_analysis():
    reset_id()
    panels = [text_panel("Index Analysis", 0, 0, 24, 8, _CONTENT)]
    return finops_dashboard(
        "finops-index-analysis",
        "FinOps · Index Analysis",
        panels,
        [instance_var()],
        time_from="now-24h",
        refresh="",
    )
