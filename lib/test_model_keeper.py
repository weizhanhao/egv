import json
from pathlib import Path
import pytest

from model_keeper import load_model_keeper, save_model_keeper, ModelKeeperSchemaError
from model_keeper import provenance_stamp


def test_load_v1_keeper_returns_expected_keys(tmp_path: Path):
    keeper = {
        "schema_version": "1.0.0",
        "project_name": "test-project",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {
            "test_runner": "vitest",
            "coverage_command": "yarn test:coverage --run",
            "coverage_output_path": "coverage/coverage-final.json",
            "test_file_pattern": "**/*.test.{ts,tsx}"
        },
        "critical_flows": [],
        "team_identity": {
            "project": "test-project",
            "team_founded_at": "2026-05-28T00:00:00Z",
            "agents": {}
        },
        "file_importance_weights": {"patterns": [], "default": 1.0},
        "learned_patterns": [],
        "lifecycle_artifacts": {
            "ideas_under_consideration": [],
            "design_reviews_completed": [],
            "post_merge_reflections": []
        },
        "known_blind_spots": [],
        "run_history": [],
        "created_at": "2026-05-28T00:00:00Z",
        "last_updated": "2026-05-28T00:00:00Z"
    }
    keeper_path = tmp_path / "keeper.json"
    keeper_path.write_text(json.dumps(keeper))

    loaded = load_model_keeper(keeper_path)

    assert loaded["schema_version"] == "1.0.0"
    assert "team_identity" in loaded
    assert "file_importance_weights" in loaded
    assert loaded["team_identity"]["project"] == "test-project"


def test_load_v0_keeper_raises_schema_error(tmp_path: Path):
    v0_keeper = {
        "project_name": "old",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {},
        "critical_flows": [],
        "schema_version": "0.1.0",
    }
    keeper_path = tmp_path / "v0.json"
    keeper_path.write_text(json.dumps(v0_keeper))

    with pytest.raises(ModelKeeperSchemaError) as exc:
        load_model_keeper(keeper_path)
    assert "0.1.0" in str(exc.value)
    assert "migrate" in str(exc.value).lower()


def test_save_and_reload_roundtrip(tmp_path: Path):
    keeper = {
        "schema_version": "1.0.0",
        "project_name": "rt",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {},
        "critical_flows": [],
        "team_identity": {"project": "rt", "team_founded_at": "2026-01-01T00:00:00Z", "agents": {}},
        "file_importance_weights": {"patterns": [], "default": 1.0},
        "learned_patterns": [],
        "lifecycle_artifacts": {"ideas_under_consideration": [], "design_reviews_completed": [], "post_merge_reflections": []},
        "known_blind_spots": [],
        "run_history": [],
        "created_at": "2026-01-01T00:00:00Z",
        "last_updated": "2026-01-01T00:00:00Z",
    }
    keeper_path = tmp_path / "keeper.json"
    keeper_path.write_text(json.dumps(keeper))

    loaded = load_model_keeper(keeper_path)
    save_model_keeper(keeper_path, loaded)
    reloaded = load_model_keeper(keeper_path)

    assert reloaded["project_name"] == "rt"
    assert reloaded["last_updated"] != keeper["last_updated"]  # refreshed by save


def test_save_rejects_invalid_keeper(tmp_path: Path):
    invalid = {"schema_version": "1.0.0", "project_name": "incomplete"}  # missing many required keys
    p = tmp_path / "bad.json"
    with pytest.raises(ModelKeeperSchemaError) as exc:
        save_model_keeper(p, invalid)
    assert "missing required keys" in str(exc.value).lower() or "missing" in str(exc.value).lower()


def test_provenance_stamp_includes_all_fields():
    stamp = provenance_stamp(
        agent="regression-auditor",
        run_id="20260528T120000Z__abc123",
        confidence=0.85,
        human_reviewed=False,
    )
    assert stamp["added_by_agent"] == "regression-auditor"
    assert stamp["added_in_run"] == "20260528T120000Z__abc123"
    assert stamp["confidence"] == 0.85
    assert stamp["human_reviewed"] is False
    assert "added_at" in stamp
    assert stamp["added_at"].endswith("Z")


def test_provenance_stamp_defaults():
    stamp = provenance_stamp(agent="qa-lead", run_id="run-x")
    assert stamp["confidence"] is None
    assert stamp["human_reviewed"] is False


import subprocess
import sys


def test_migration_v0_to_v1_produces_loadable_keeper(tmp_path: Path):
    v0_keeper = {
        "project_name": "excalidraw",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {
            "test_runner": "vitest",
            "coverage_command": "yarn test:coverage --run",
            "coverage_output_path": "coverage/coverage-final.json",
            "test_file_pattern": "**/*.test.{ts,tsx}"
        },
        "critical_flows": [{"name": "draw-shape", "test_path": "tests/draw.test.tsx"}],
        "known_blind_spots": [],
        "run_history": [],
        "schema_version": "0.1.0",
        "created_at": "2026-05-28T00:00:00Z",
        "last_updated": "2026-05-28T00:00:00Z",
    }
    v0_path = tmp_path / "v0.json"
    v0_path.write_text(json.dumps(v0_keeper))

    script = Path(__file__).parent / "migrate-v0-to-v1.py"
    result = subprocess.run(
        [sys.executable, str(script), str(v0_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    migrated = load_model_keeper(v0_path)
    assert migrated["schema_version"] == "1.0.0"
    assert migrated["critical_flows"][0]["name"] == "draw-shape"
    assert "team_identity" in migrated
    assert "file_importance_weights" in migrated
    assert "learned_patterns" in migrated
    assert "lifecycle_artifacts" in migrated


from model_keeper import keeper_path_for_project_root


def test_keeper_path_for_project_root(tmp_path: Path):
    expected = tmp_path / ".egv" / "project-keeper.json"
    assert keeper_path_for_project_root(tmp_path) == expected
