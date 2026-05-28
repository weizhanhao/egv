import json
from pathlib import Path

from lifecycle import log_brainstorm, log_requirements, log_design_review, log_reflection
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
        },
        "known_blind_spots": [],
        "run_history": [],
        "created_at": now_iso(),
        "last_updated": now_iso(),
    }
    (egv_dir / "project-keeper.json").write_text(json.dumps(keeper))
    return tmp_path


def test_brainstorm_appends_to_keeper(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    entry = log_brainstorm(root, "Add dark mode toggle")
    assert entry["idea"] == "Add dark mode toggle"
    assert "cartographer" in entry["team_questions"]

    k = load_model_keeper(root / ".egv" / "project-keeper.json")
    assert len(k["lifecycle_artifacts"]["ideas_under_consideration"]) == 1


def test_requirements_detects_interaction_gap(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    entry = log_requirements(root, "When user clicks Save, the file is persisted.")
    cats = [g["category"] for g in entry["testability_gaps"]]
    assert "interaction" in cats


def test_design_review_records_findings(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    entry = log_design_review(root, "Breaking change: rename schema field foo to bar.")
    assert any("breaking change" in f.lower() for f in entry["findings"])


def test_reflect_with_no_history(tmp_path: Path):
    root = _seed_keeper(tmp_path)
    entry = log_reflection(root)
    assert "starting on this project" in entry["insights"][0].lower()
