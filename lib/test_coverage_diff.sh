#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

cat > "$TMP/diff.patch" <<'EOF'
+++ b/src/widget.ts
@@ -1 +1,3 @@
+++ b/src/types.ts
@@ -1 +1,5 @@
EOF

cat > "$TMP/coverage.json" <<EOF
{
  "$TMP/src/widget.ts": {
    "path": "$TMP/src/widget.ts",
    "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 3}}},
    "s": {"0": 0}
  },
  "$TMP/src/types.ts": {
    "path": "$TMP/src/types.ts",
    "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 5}}},
    "s": {"0": 0}
  }
}
EOF

cat > "$TMP/weights.json" <<'EOF'
{
  "patterns": [
    {"glob": "**/types.ts", "weight": 0.0, "reason": "type-only"}
  ],
  "default": 1.0
}
EOF

OUT=$(npx --yes tsx@4 lib/coverage-diff.ts "$TMP/diff.patch" "$TMP/coverage.json" "$TMP" --weights "$TMP/weights.json")
WEIGHTED_RATIO=$(echo "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['summary'].get('weighted_coverage_ratio','MISSING'))")
WEIGHTED_PROD=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['summary'].get('weighted_production_changed_lines','MISSING'))")

echo "weighted_coverage_ratio: $WEIGHTED_RATIO"
echo "weighted_production_changed_lines: $WEIGHTED_PROD"

if [ "$WEIGHTED_RATIO" = "MISSING" ]; then
  echo "FAIL: weighted_coverage_ratio field is missing"
  exit 1
fi

# Weighted: types.ts (5 lines * 0.0) + widget.ts (3 lines * 1.0) = 3
if [ "$WEIGHTED_PROD" != "3" ]; then
  echo "FAIL: expected weighted_production_changed_lines=3, got $WEIGHTED_PROD"
  exit 1
fi

echo "PASS"
