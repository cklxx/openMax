#!/usr/bin/env bash
# Publish openMax to PyPI.
# Usage: ./scripts/publish.sh [--dry-run]
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
INIT_VERSION=$(python -c "from openmax import __version__; print(__version__)")

if [[ "$VERSION" != "$INIT_VERSION" ]]; then
  echo "ERROR: version mismatch — pyproject.toml=$VERSION, __init__.py=$INIT_VERSION"
  exit 1
fi

echo "==> Publishing openMax $VERSION"

# Lint + test gate
echo "==> Running lint..."
ruff check src/ tests/
ruff format --check src/ tests/

echo "==> Running tests..."
python -m pytest tests/ -q

# Clean previous builds
rm -rf dist/ build/ src/*.egg-info

# Build
echo "==> Building..."
python -m build

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "==> Dry run — skipping upload"
  echo "    Would upload: $(ls dist/)"
  exit 0
fi

# Upload
echo "==> Uploading to PyPI..."
python -m twine upload dist/openmax-${VERSION}*

echo "==> Done! Published openMax $VERSION"
echo "    https://pypi.org/project/openmax/$VERSION/"
