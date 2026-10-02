#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$HERE/../Scripts/reproduce_panel.py" ]]; then
  exec bash "$HERE/../reproduce.sh" "$@" --mode analysis
fi
# Generated analysis folders keep their master bundle outside this directory.
MASTER="${FSA_MASTER_ROOT:-$(cd "$HERE/.." && pwd -P)}"
OUTPUT="${1:?Supply a new analysis output directory}"
exec "${IPEDS_FSA_PYTHON:-python3}" "$HERE/Scripts/build_analysis_views.py" --root "$MASTER" --output "$OUTPUT" --decisions "$HERE/Decisions"
