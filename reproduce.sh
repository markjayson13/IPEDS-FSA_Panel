#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# Read-only checks/help need only the existing Python, not a new environment.
for argument in "$@"; do
  if [[ "$argument" == "--check" || "$argument" == "--help" || "$argument" == "-h" ]]; then
    exec "${IPEDS_FSA_PYTHON:-python3}" "$REPO_DIR/Scripts/reproduce_panel.py" "$@"
  fi
done
RUNTIME_DIR="${IPEDS_FSA_RUNTIME:-$REPO_DIR/.reproduction-runtime}"
if [[ -n "${IPEDS_FSA_PYTHON:-}" ]]; then
  exec "$IPEDS_FSA_PYTHON" "$REPO_DIR/Scripts/reproduce_panel.py" "$@"
fi
# Validate inputs and path separation before installing or creating anything.
python3 "$REPO_DIR/Scripts/reproduce_panel.py" "$@" --prepare-only --runtime "$RUNTIME_DIR"
[[ ! -L "$RUNTIME_DIR" ]] || { echo "Runtime directory must not be a symlink" >&2; exit 1; }
mkdir -p "$RUNTIME_DIR"
UV_BIN="$RUNTIME_DIR/uv/uv"
if [[ ! -x "$UV_BIN" ]]; then
  curl -fsSL --retry 5 https://astral.sh/uv/0.12.18/install.sh -o "$RUNTIME_DIR/install-uv.sh"
  env UV_UNMANAGED_INSTALL="$RUNTIME_DIR/uv" UV_NO_MODIFY_PATH=1 sh "$RUNTIME_DIR/install-uv.sh" </dev/null
fi
[[ "$("$UV_BIN" --version)" == "uv 0.12.18"* ]] || { echo "Unexpected uv version" >&2; exit 1; }
export UV_CACHE_DIR="$RUNTIME_DIR/cache" UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python" UV_NO_MODIFY_PATH=1
if [[ ! -x "$RUNTIME_DIR/.venv/bin/python" ]]; then
  "$UV_BIN" --no-config venv --python 3.13.0 --managed-python "$RUNTIME_DIR/.venv" </dev/null
fi
"$RUNTIME_DIR/.venv/bin/python" -c 'import sys; assert sys.version_info[:3] == (3,13,0)'
"$UV_BIN" --no-config pip install --python "$RUNTIME_DIR/.venv/bin/python" --index-url https://pypi.org/simple -r "$REPO_DIR/requirements.txt" </dev/null
exec "$RUNTIME_DIR/.venv/bin/python" "$REPO_DIR/Scripts/reproduce_panel.py" "$@"
