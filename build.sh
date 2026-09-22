#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${BUSPEELER_PYTHON:-}" ]]; then
  python="$BUSPEELER_PYTHON"
elif [[ -x /mnt/d/dev-cache/buspeeler/shared/venv-linux/bin/python ]]; then
  python=/mnt/d/dev-cache/buspeeler/shared/venv-linux/bin/python
elif command -v python3.12 >/dev/null 2>&1; then
  python=python3.12
else
  python=python3
fi
exec "$python" "$root/scripts/build.py" "$@"
