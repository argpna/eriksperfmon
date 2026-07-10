#!/bin/bash
# Pre-download the PerformanceMonitor release zip into ./community for offline installs.
set -euo pipefail
cd "$(dirname "$0")/../community"

version="${1:-$(grep -oP '^perfmon_version:\s*"\K[^"]+' ../ansible/roles/perfmon_install/defaults/main.yml)}"

curl -fsSL -o "perfmon-${version}.zip" \
  "https://github.com/erikdarlingdata/PerformanceMonitor/archive/refs/tags/${version}.zip"
ls -la "perfmon-${version}.zip"
