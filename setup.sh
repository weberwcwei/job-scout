#!/usr/bin/env bash
set -euo pipefail

echo "=== job-scout setup ==="
echo ""

# 1. Check Python 3.12+
if ! python3 -c "import sys; assert sys.version_info >= (3,12)" 2>/dev/null; then
  echo "Python 3.12 or higher is required."
  echo "  Download from: https://www.python.org/downloads/"
  exit 1
fi
echo "Python $(python3 --version | cut -d' ' -f2)"

# 2. Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv $(uv --version | cut -d' ' -f2)"

# 3. Create virtual environment and install dependencies
echo "Installing dependencies..."
uv venv .venv --python python3
uv pip install --python .venv/bin/python -r requirements.txt
echo "Dependencies installed"

# 4. Hand off to the CLI for config + DB setup
echo ""
.venv/bin/job-scout init
