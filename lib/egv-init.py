#!/usr/bin/env python3
"""egv-init.py — Scaffold a new EGV Model Keeper for the current project.

Creates <project_root>/.egv/project-keeper.json with sensible defaults.
Run from inside your project's root directory.

Usage:
  cd <your_project>
  python3 ~/.claude/skills/egv-verify/lib/egv-init.py [--name <project_name>]
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_KEEPER_TEMPLATE = {
    "schema_version": "1.0.0",
    "project_name": "PROJECT_NAME",
    "project_root": "PROJECT_ROOT",
    "framework": "vitest+playwright",
    "framework_config": {
        "test_runner": "vitest",
        "coverage_command": "yarn test:coverage --run",
        "coverage_output_path": "coverage/coverage-final.json",
        "test_file_pattern": "**/*.test.{ts,tsx}",
        "typecheck_command": "yarn test:typecheck",
        "sentinel_test_command": "yarn test --run {test_path}"
    },
    "critical_flows": [
        {
            "name": "EXAMPLE-flow-rename-this",
            "description": "Replace with a real user-visible behavior in your project",
            "category": "core",
            "test_path": "path/to/your.test.ts",
            "owning_code_paths": ["src/your-module.ts"],
            "last_verified": None,
            "verified_at_runs": []
        }
    ],
    "team_identity": {
        "project": "PROJECT_NAME",
        "team_founded_at": "TIMESTAMP",
        "agents": {}
    },
    "file_importance_weights": {
        "patterns": [
            {"glob": "**/*.d.ts", "weight": 0.0, "reason": "type-only, no runtime"},
            {"glob": "**/*.snap", "weight": 0.0, "reason": "auto-generated"},
            {"glob": "**/*.test.{ts,tsx,js,jsx}", "weight": 0.0, "reason": "test code"},
            {"glob": "**/types.ts", "weight": 0.0, "reason": "type-only convention"},
            {"glob": "**/*.config.{ts,mts,js,mjs}", "weight": 0.3, "reason": "build config"}
        ],
        "default": 1.0
    },
    "learned_patterns": [],
    "lifecycle_artifacts": {
        "ideas_under_consideration": [],
        "design_reviews_completed": [],
        "post_merge_reflections": [],
        "synthesis_proposals": []
    },
    "known_blind_spots": [],
    "run_history": [],
    "flow_baselines": {},
    "created_at": "TIMESTAMP",
    "last_updated": "TIMESTAMP"
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    p = argparse.ArgumentParser(description="Scaffold an EGV Model Keeper for the current project")
    p.add_argument("--name", default=None, help="Project name (default: current directory name)")
    p.add_argument("--project-root", default=None, help="Project root path (default: $PWD)")
    p.add_argument("--force", action="store_true", help="Overwrite existing keeper")
    args = p.parse_args()

    project_root = Path(args.project_root or Path.cwd()).resolve()
    project_name = args.name or project_root.name
    egv_dir = project_root / ".egv"
    keeper_path = egv_dir / "project-keeper.json"

    if keeper_path.exists() and not args.force:
        print(f"FATAL: keeper already exists at {keeper_path}. Use --force to overwrite.", file=sys.stderr)
        return 2

    egv_dir.mkdir(exist_ok=True)
    ts = now_iso()
    keeper = json.loads(json.dumps(DEFAULT_KEEPER_TEMPLATE))  # deep copy
    keeper["project_name"] = project_name
    keeper["project_root"] = str(project_root)
    keeper["team_identity"]["project"] = project_name
    keeper["team_identity"]["team_founded_at"] = ts
    keeper["created_at"] = ts
    keeper["last_updated"] = ts

    keeper_path.write_text(json.dumps(keeper, indent=2))
    print(f"✓ Created {keeper_path}")
    print()
    print("Next steps:")
    print(f"  1. Edit {keeper_path} to:")
    print(f"     - Update framework_config (test_runner, coverage_command) for your project")
    print(f"     - Replace the example critical_flow with real user-visible flows in your codebase")
    print(f"  2. Commit .egv/project-keeper.json to your project's git repo")
    print(f"  3. Run a first verification:")
    print(f"     python3 ~/.claude/skills/egv-verify/lib/run-egv.py HEAD --project-root {project_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
