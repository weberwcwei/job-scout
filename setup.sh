#!/usr/bin/env bash
set -euo pipefail

echo "=== job-scout setup ==="
echo ""

# 1. Find Python 3.12+
PYTHON=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" &>/dev/null && "$candidate" -c "import sys; assert sys.version_info >= (3,12)" 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.12 or higher is required."
  echo "  Found: $(python3 --version 2>/dev/null || echo 'none')"
  echo "  Download from: https://www.python.org/downloads/"
  exit 1
fi
echo "Python $($PYTHON --version | cut -d' ' -f2) ($PYTHON)"

# 2. Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv $(uv --version | cut -d' ' -f2)"

# 3. Create virtual environment and install dependencies
echo "Installing dependencies..."
uv venv .venv --python "$PYTHON" --clear
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python -e .
echo "Dependencies installed"

# 4. Hand off to the CLI for config + DB setup
echo ""
.venv/bin/job-scout init
