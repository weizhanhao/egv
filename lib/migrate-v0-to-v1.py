#!/usr/bin/env python3
"""migrate-v0-to-v1.py — One-shot migration of Model Keeper v0 → v1.

Adds: team_identity (empty agents), file_importance_weights (sensible defaults),
learned_patterns ([]), lifecycle_artifacts (empty buckets).

Usage: python3 migrate-v0-to-v1.py <path-to-keeper.json>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_WEIGHTS = {
    "patterns": [
        {"glob": "**/*.d.ts", "weight": 0.0, "reason": "type-only, no runtime"},
        {"glob": "**/*.snap", "weight": 0.0, "reason": "auto-generated snapshot"},
        {"glob": "**/*.test.{ts,tsx,js,jsx}", "weight": 0.0, "reason": "test code"},
        {"glob": "**/types.ts", "weight": 0.0, "reason": "type-only file (Excalidraw convention)"},
        {"glob": "**/*.config.{ts,mts,js,mjs}", "weight": 0.3, "reason": "build config"},
    ],
    "default": 1.0,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate(v0: dict) -> dict:
    project = v0.get("project_name", "unknown")
    v1 = dict(v0)
    v1["schema_version"] = "1.0.0"
    v1.setdefault("team_identity", {
        "project": project,
        "team_founded_at": v0.get("created_at", now_iso()),
        "agents": {},
    })
    v1.setdefault("file_importance_weights", DEFAULT_WEIGHTS)
    fc = v1.setdefault("framework_config", {})
    fc.setdefault("sentinel_test_command", "yarn test:app --run {test_path}")
    v1.setdefault("learned_patterns", [])
    v1.setdefault("flow_baselines", {})  # {flow_name: {p50_ms: int, n_observations: int, last_updated: iso}}
    v1.setdefault("lifecycle_artifacts", {
        "ideas_under_consideration": [],
        "design_reviews_completed": [],
        "post_merge_reflections": [],
        "synthesis_proposals": [],
    })
    # Backfill synthesis_proposals if lifecycle_artifacts existed but lacked the bucket
    v1["lifecycle_artifacts"].setdefault("synthesis_proposals", [])
    v1["last_updated"] = now_iso()
    return v1


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate-v0-to-v1.py <keeper.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    if data.get("schema_version") == "1.0.0":
        print(f"[migrate] {path} is already v1.0.0; no-op", file=sys.stderr)
        return 0
    backup = path.with_suffix(".json.v0.bak")
    backup.write_text(path.read_text())
    print(f"[migrate] backup written to {backup}", file=sys.stderr)
    migrated = migrate(data)
    path.write_text(json.dumps(migrated, indent=2))
    print(f"[migrate] {path} migrated to v1.0.0", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
