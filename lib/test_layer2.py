import json
import os
from pathlib import Path
import pytest

from layer2 import synthesize_proposals
from model_keeper import load_model_keeper, now_iso


def _seed_keeper(tmp_path: Path) -> Path:
    egv_dir = tmp_path / ".egv"
    egv_dir.mkdir()
    keeper = {
        "schema_version": "1.0.0",
        "project_name": "test",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {},
        "critical_flows": [],
        "team_identity": {"project": "test", "team_founded_at": now_iso(), "agents": {}},
        "file_importance_weights": {"patterns": [], "default": 1.0},
        "learned_patterns": [],
        "lifecycle_artifacts": {
            "ideas_under_consideration": [],
            "design_reviews_completed": [],
            "post_merge_reflections": [],
            "synthesis_proposals": [],
        },
        "known_blind_spots": [],
        "run_history": [],
        "created_at": now_iso(),
        "last_updated": now_iso(),
    }
    (egv_dir / "project-keeper.json").write_text(json.dumps(keeper))
    return tmp_path


def test_synthesize_generates_stub_for_uncovered_file(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    blast = {
        "generated_at": now_iso(),
        "files": [
            {"file": "src/widget.ts", "is_test_file": False, "uncovered_lines": [42, 43, 44], "coverage_status": "uncovered"},
            {"file": "src/types.ts", "is_test_file": False, "uncovered_lines": [1], "coverage_status": "uncovered"},
        ],
    }
    blast_path = tmp_path / "blast-radius.json"
    blast_path.write_text(json.dumps(blast))

    result = synthesize_proposals(root, blast_path)
    assert result["proposals_generated"] == 2
    assert "widget.ts" in result["proposals"][0]["playwright_stub"]
    assert result["proposals"][0]["needs_human_review"] is True

    k = load_model_keeper(root / ".egv" / "project-keeper.json")
    assert len(k["lifecycle_artifacts"]["synthesis_proposals"]) == 2


def test_synthesize_skips_test_files(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    blast = {
        "generated_at": now_iso(),
        "files": [
            {"file": "src/widget.test.ts", "is_test_file": True, "uncovered_lines": [], "coverage_status": "test_file_excluded"},
        ],
    }
    blast_path = tmp_path / "blast-radius.json"
    blast_path.write_text(json.dumps(blast))

    result = synthesize_proposals(root, blast_path)
    assert result["proposals_generated"] == 0


def test_synthesize_skips_fully_covered(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    blast = {
        "generated_at": now_iso(),
        "files": [
            {"file": "src/widget.ts", "is_test_file": False, "uncovered_lines": [], "coverage_status": "fully_covered"},
        ],
    }
    blast_path = tmp_path / "blast-radius.json"
    blast_path.write_text(json.dumps(blast))

    result = synthesize_proposals(root, blast_path)
    assert result["proposals_generated"] == 0


def test_synthesize_falls_back_to_stub_when_no_api_key(tmp_path: Path, monkeypatch):
    """Verify Layer 2 emits stubs (not LLM) when ANTHROPIC_API_KEY is absent."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = _seed_keeper(tmp_path)
    blast = {
        "generated_at": now_iso(),
        "files": [
            {"file": "src/widget.ts", "is_test_file": False, "uncovered_lines": [42, 43], "coverage_status": "uncovered"},
        ],
    }
    blast_path = tmp_path / "blast-radius.json"
    blast_path.write_text(json.dumps(blast))

    result = synthesize_proposals(root, blast_path)
    assert result["proposals_generated"] == 1
    assert result["llm_completions"] == 0
    assert result["stub_fallbacks"] == 1
    assert result["proposals"][0]["synthesis_method"] == "stub-template"
