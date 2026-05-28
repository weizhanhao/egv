#!/usr/bin/env bash
#
# install.sh — Install EGV skill into ~/.claude/skills/egv-verify
#
# Usage:
#   bash install.sh           # install or update
#   bash install.sh --check   # check install state
#

set -euo pipefail

SKILL_NAME="egv-verify"
INSTALL_DIR="$HOME/.claude/skills/$SKILL_NAME"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--check" ]]; then
    if [[ -d "$INSTALL_DIR" ]]; then
        echo "✓ EGV is installed at: $INSTALL_DIR"
        if [[ -f "$INSTALL_DIR/SKILL.md" ]]; then
            grep '^name:' "$INSTALL_DIR/SKILL.md" | head -1
        fi
    else
        echo "✗ EGV is NOT installed."
    fi
    exit 0
fi

echo "[egv-install] Source: $SOURCE_DIR"
echo "[egv-install] Target: $INSTALL_DIR"

# Verify prereqs
if ! command -v python3 >/dev/null 2>&1; then
    echo "FATAL: python3 is required" >&2
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[egv-install] Python: $PY_VERSION"

# Create install dir and copy
mkdir -p "$INSTALL_DIR"

# Use rsync if available (preserves perms cleanly); fall back to cp
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='reports/' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        --exclude='.DS_Store' \
        --exclude='*.v0.bak' \
        "$SOURCE_DIR/" "$INSTALL_DIR/"
else
    # Fallback: rm -rf then cp -r
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    cp -r "$SOURCE_DIR/"* "$INSTALL_DIR/"
    rm -rf "$INSTALL_DIR/reports" 2>/dev/null
    find "$INSTALL_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    find "$INSTALL_DIR" -name '*.pyc' -delete 2>/dev/null || true
fi

echo "[egv-install] Files installed."

# Run tests to verify install
echo "[egv-install] Running test suite to verify install..."
if cd "$INSTALL_DIR/lib" && python3 -m pytest --tb=short 2>&1 | tail -5; then
    echo "[egv-install] ✓ Tests passed."
else
    echo "[egv-install] ⚠ Some tests failed — review output above." >&2
fi

echo ""
echo "✓ EGV skill installed at: $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. To use on a project: cd <your_project> && python3 $INSTALL_DIR/lib/egv-init.py"
echo "  2. To verify a diff: python3 $INSTALL_DIR/lib/run-egv.py HEAD --project-root <your_project>"
echo "  3. See README.md for full usage."
