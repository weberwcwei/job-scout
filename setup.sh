#!/usr/bin/env bash
set -euo pipefail

echo "=== job-scout setup ==="
echo ""

# 1. Check Python 3.12+
if ! python3 -c "import sys; assert sys.version_info >= (3,12)" 2>/dev/null; then
  echo "❌ Python 3.12 or higher is required."
  echo "   Download from: https://www.python.org/downloads/"
  exit 1
fi
echo "✅ Python $(python3 --version | cut -d' ' -f2)"

# 2. Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "📦 Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "✅ uv $(uv --version | cut -d' ' -f2)"

# 3. Create virtual environment and install dependencies
echo "📦 Installing Python dependencies..."
uv venv .venv --python python3
uv pip install --python .venv/bin/python -r requirements.txt
echo "✅ Dependencies installed"

# 4. Copy config template if config.yaml doesn't exist
if [ ! -f config.yaml ]; then
  cp config.template.yaml config.yaml
  echo ""
  echo "📝 Created config.yaml — please open it and fill in your details."
  echo "   Opening in TextEdit..."
  open -a TextEdit config.yaml
  echo ""
  echo "⚠️  Edit config.yaml, then come back here and press Enter to continue..."
  read -r
else
  echo "✅ config.yaml already exists"
fi

# 5. Install launchd schedule (runs scraper every 6 hours + daily digest at 9 AM)
echo "⏰ Installing launchd schedule..."
.venv/bin/job-scout schedule --install
echo "✅ Schedule installed"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Quick test:"
echo "  .venv/bin/job-scout scrape --dry-run"
echo ""
echo "Commands:"
echo "  .venv/bin/job-scout scrape       — run scrapers now"
echo "  .venv/bin/job-scout list         — browse matches"
echo "  .venv/bin/job-scout digest       — send email digest"
echo "  .venv/bin/job-scout stats        — summary stats"
echo "  .venv/bin/job-scout --help       — all commands"
