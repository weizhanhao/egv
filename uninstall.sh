#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="$HOME/.claude/skills/egv-verify"
if [[ ! -d "$INSTALL_DIR" ]]; then
    echo "EGV is not installed at $INSTALL_DIR — nothing to do."
    exit 0
fi
echo "About to remove: $INSTALL_DIR"
read -p "Continue? [y/N] " yn
case "$yn" in
    [Yy]*) rm -rf "$INSTALL_DIR"; echo "✓ Uninstalled.";;
    *) echo "Aborted."; exit 1;;
esac
