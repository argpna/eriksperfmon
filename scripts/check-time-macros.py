#!/usr/bin/env python3
"""Fail the build if $__timeFrom()/$__timeTo() appear bare in dashboard source.

collect.* timestamps are server-local, but the macros expand to UTC bounds. A bare
macro against a server-local column is silently wrong on non-UTC instances. Correct
usage goes through _shared.py's tz_from()/tz_to()/tz_filter()/tz_prefilter() helpers.

Checks Python source (dashboard_defs/*.py), not the generated JSON: rawSql always
contains the literal macro text even after a correct shift, so only the source can
distinguish shifted from bare.

Deliberately bare call sites (e.g. a UTC output time-axis value) are exempted with an
inline `/* time-macro-allow: <reason> */` comment on the same line, not a side table
keyed by line number - a line-number allowlist silently goes stale the moment anything
above it is edited. Block-comment syntax (not `--`) because these lines live inside raw
SQL strings, per this repo's SQL comment convention.

Usage: python3 scripts/check-time-macros.py
"""

import pathlib
import re
import sys

MACRO_RE = re.compile(r"\$__time(?:From|To)\(\)")
ALLOW_COMMENT_RE = re.compile(r"/\*\s*time-macro-allow:\s*\S")

# Files where the macro literal legitimately appears in source: _shared.py assembles
# it internally inside tz_from()/tz_to()/tz_filter()/tz_prefilter()/_expand_timegroup().
EXEMPT_FILES = {"_shared.py"}


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    dashboard_defs_dir = (
        root / "ansible" / "roles" / "perfmon_grafana" / "files" / "dashboard_defs"
    )

    violations = []
    for path in sorted(dashboard_defs_dir.rglob("*.py")):
        if path.name in EXEMPT_FILES:
            continue
        rel = str(path.relative_to(dashboard_defs_dir))
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not MACRO_RE.search(line):
                continue
            if ALLOW_COMMENT_RE.search(line):
                continue
            violations.append((rel, lineno, line.strip()))

    if violations:
        print(
            "Bare $__timeFrom()/$__timeTo() found outside tz_from()/tz_to() helpers:\n"
        )
        for rel, lineno, line in violations:
            print(f"  {rel}:{lineno}: {line}")
        print(
            "\nEach of these needs either tz_from()/tz_to()/tz_filter()/tz_prefilter() "
            "if the column is server-local or a trailing '/* time-macro-allow: <reason> */' "
            "comment on the same line if the macro is deliberately meant to stay UTC."
        )
        sys.exit(1)
    print(
        f"OK - no bare time macros outside {sorted(EXEMPT_FILES)} or an allow comment"
    )


if __name__ == "__main__":
    main()
