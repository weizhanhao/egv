from pathlib import Path
import json
import pytest

from model_keeper import save_model_keeper, load_model_keeper, now_iso
from agent_identity import read_agent_identity, write_agent_identity, new_agent_record


def _seed_keeper(tmp_path: Path) -> Path:
    keeper = {
        "schema_version": "1.0.0",
        "project_name": "demo",
        "project_root": str(tmp_path),
        "framework": "vitest+playwright",
        "framework_config": {},
        "critical_flows": [],
        "team_identity": {"project": "demo", "team_founded_at": now_iso(), "agents": {}},
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
    p = tmp_path / "keeper.json"
    p.write_text(json.dumps(keeper))
    return p


def test_new_agent_record_has_required_fields():
    rec = new_agent_record(agent="regression-auditor", project="demo")
    assert rec["agent_id"].startswith("regression-auditor-demo-")
    assert rec["total_invocations"] == 0
    assert rec["specializations_learned"] == []
    assert rec["weight_adjustments_learned"] == {}
    assert rec["recent_findings"] == []
    assert "joined_team_at" in rec


def test_read_agent_identity_creates_record_if_missing(tmp_path: Path):
    p = _seed_keeper(tmp_path)
    keeper = load_model_keeper(p)
    record = read_agent_identity(keeper, "regression-auditor")
    assert record["total_invocations"] == 0
    assert record["agent_id"].startswith("regression-auditor-")


def test_write_agent_identity_persists(tmp_path: Path):
    p = _seed_keeper(tmp_path)
    keeper = load_model_keeper(p)

    record = read_agent_identity(keeper, "regression-auditor")
    record["total_invocations"] = 5
    record["specializations_learned"].append("math/point.ts is densely covered")
    write_agent_identity(keeper, "regression-auditor", record)
    save_model_keeper(p, keeper)

    keeper2 = load_model_keeper(p)
    record2 = read_agent_identity(keeper2, "regression-auditor")
    assert record2["total_invocations"] == 5
    assert "math/point.ts is densely covered" in record2["specializations_learned"]
