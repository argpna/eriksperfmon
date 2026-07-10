#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COLLECTIONS_PATH="${COLLECTIONS_PATH:-$PROJECT_ROOT/.ansible/collections}"

cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PATH="${VENV_PATH:-$PROJECT_ROOT/.venv}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Missing Python interpreter: $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN to a valid Python 3 executable if needed." >&2
  exit 1
fi

echo "Recreating host venv at $VENV_PATH with $("$PYTHON_BIN" --version)..."
rm -rf "$VENV_PATH"
"$PYTHON_BIN" -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip --quiet
"$VENV_PATH/bin/pip" install -r requirements.txt
mkdir -p "$COLLECTIONS_PATH"
"$VENV_PATH/bin/ansible-galaxy" collection install -r requirements.yml -p "$COLLECTIONS_PATH"

echo "Done. Run 'source $VENV_PATH/bin/activate'"
