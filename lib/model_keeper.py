"""model_keeper.py — Load/save Model Keeper JSON with v1 schema enforcement.

Schema v1 adds:
- team_identity (agent identity records)
- file_importance_weights (per-project Auditor tuning)
- learned_patterns (Principle 7 closure data)
- lifecycle_artifacts (full-lifecycle phase data)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT_SCHEMA_VERSION = "1.0.0"


class ModelKeeperSchemaError(Exception):
    """Raised when Model Keeper schema is wrong or needs migration."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_model_keeper(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ModelKeeperSchemaError(
            f"Model Keeper file not found: {path}"
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelKeeperSchemaError(
            f"Model Keeper at {path} contains invalid JSON: {exc}"
        ) from exc

    version = data.get("schema_version", "0.0.0")
    if version != CURRENT_SCHEMA_VERSION:
        raise ModelKeeperSchemaError(
            f"Model Keeper at {path} has schema_version={version!r}; "
            f"expected {CURRENT_SCHEMA_VERSION!r}. "
            f"Run `python3 lib/migrate-v0-to-v1.py {path}` to migrate."
        )
    _validate_required_keys(data, path)
    return data


def save_model_keeper(path: Path, data: dict[str, Any]) -> None:
    data = dict(data)
    data["last_updated"] = now_iso()
    _validate_required_keys(data, path)
    Path(path).write_text(json.dumps(data, indent=2))


def _validate_required_keys(data: dict[str, Any], path: Path) -> None:
    required = {
        "schema_version", "project_name", "project_root", "framework",
        "framework_config", "critical_flows", "team_identity",
        "file_importance_weights", "learned_patterns", "lifecycle_artifacts",
        "known_blind_spots", "run_history", "created_at", "last_updated",
    }
    missing = required - data.keys()
    if missing:
        raise ModelKeeperSchemaError(
            f"Model Keeper at {path} is missing required keys: {sorted(missing)}"
        )


def keeper_path_for_project_root(project_root: Path) -> Path:
    """Return the canonical Model Keeper path for a given project root.

    v1: keeper lives at <project_root>/.egv/project-keeper.json.
    """
    return Path(project_root) / ".egv" / "project-keeper.json"


def provenance_stamp(
    *,
    agent: str,
    run_id: str,
    confidence: float | None = None,
    human_reviewed: bool = False,
) -> dict[str, Any]:
    """Generate the provenance object every Model Keeper entry carries."""
    return {
        "added_at": now_iso(),
        "added_by_agent": agent,
        "added_in_run": run_id,
        "confidence": confidence,
        "human_reviewed": human_reviewed,
    }
