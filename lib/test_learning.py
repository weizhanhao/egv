import json
from pathlib import Path

from learning import detect_patterns, propose_patterns, list_pending_patterns, approve_pattern
from model_keeper import load_model_keeper, now_iso


def _seed_keeper(tmp_path: Path, run_history: list) -> Path:
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
        "run_history": run_history,
        "created_at": now_iso(),
        "last_updated": now_iso(),
    }
    (egv_dir / "project-keeper.json").write_text(json.dumps(keeper))
    return tmp_path


def test_no_proposals_when_history_short(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
    ])
    proposals = detect_patterns(root)
    assert proposals == []


def test_proposal_when_3_disagreements(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r3", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
    ])
    proposals = detect_patterns(root)
    assert len(proposals) == 1
    assert proposals[0]["pattern"] == "auditor_warn_with_sentinel_pass_disagreement"
    assert proposals[0]["human_reviewed"] is False


def test_propose_patterns_persists(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r3", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
    ])
    result = propose_patterns(root)
    assert result["proposed_count"] == 1

    keeper = load_model_keeper(root / ".egv" / "project-keeper.json")
    assert len(keeper["learned_patterns"]) == 1


def test_propose_is_idempotent(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r3", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
    ])
    propose_patterns(root)
    second = propose_patterns(root)
    assert second["proposed_count"] == 0  # already proposed


def test_approve_pattern_sets_human_reviewed(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
        {"run_id": "r3", "auditor_verdict": "WARN", "sentinel_verdict": "PASS"},
    ])
    propose_patterns(root)
    pending = list_pending_patterns(root)
    assert len(pending) == 1
    approved = approve_pattern(root, "auditor_warn_with_sentinel_pass_disagreement")
    assert approved is not None
    assert approved["human_reviewed"] is True
    assert list_pending_patterns(root) == []


def test_contract_only_disagreement_pattern(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "PASS", "sentinel_verdict": "PASS", "contract_verdict": "WARN"},
        {"run_id": "r2", "auditor_verdict": "PASS", "sentinel_verdict": "PASS", "contract_verdict": "WARN"},
        {"run_id": "r3", "auditor_verdict": "PASS", "sentinel_verdict": "PASS", "contract_verdict": "WARN"},
    ])
    proposals = detect_patterns(root)
    pattern_ids = [p["pattern"] for p in proposals]
    assert "contract_only_warn_disagreement" in pattern_ids


def test_all_three_concerning_pattern(tmp_path: Path):
    root = _seed_keeper(tmp_path, [
        {"run_id": "r1", "auditor_verdict": "WARN", "sentinel_verdict": "FAIL", "contract_verdict": "WARN"},
        {"run_id": "r2", "auditor_verdict": "WARN", "sentinel_verdict": "WARN", "contract_verdict": "WARN"},
        {"run_id": "r3", "auditor_verdict": "FAIL", "sentinel_verdict": "WARN", "contract_verdict": "WARN"},
    ])
    proposals = detect_patterns(root)
    pattern_ids = [p["pattern"] for p in proposals]
    assert "all_three_agents_concerning" in pattern_ids
