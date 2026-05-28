"""learning.py — Closure of EGV Principle 7 (Accumulated Learning).

Reads run_history + per-agent recent_findings, detects patterns, proposes
auto-adjustments. All proposals are gated by human_reviewed=False until user approves.
"""

from pathlib import Path
from typing import Any

from model_keeper import (
    keeper_path_for_project_root,
    load_model_keeper,
    now_iso,
    provenance_stamp,
    save_model_keeper,
)


MIN_CONSECUTIVE_FOR_FP_PATTERN = 3
MIN_RUNS_FOR_STABLE_FLOW = 10


def detect_patterns(project_root: Path) -> list[dict[str, Any]]:
    """Scan Model Keeper, return list of newly-detected pattern proposals.
    Does NOT mutate the keeper — just returns what would be proposed.
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    proposals: list[dict[str, Any]] = []

    existing_pattern_ids = {p.get("pattern") for p in keeper.get("learned_patterns", [])}

    # Pattern A: false-positive file glob detection from run_history.
    runs = keeper.get("run_history", [])
    if len(runs) >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
        # Look at Auditor findings across runs.
        # If Auditor consistently flagged WARN with Sentinel PASS, surface as a candidate.
        recent = runs[-10:]
        disagreement_count = sum(
            1 for r in recent
            if r.get("auditor_verdict") == "WARN" and r.get("sentinel_verdict") == "PASS"
        )
        if disagreement_count >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
            pattern_id = "auditor_warn_with_sentinel_pass_disagreement"
            if pattern_id not in existing_pattern_ids:
                proposals.append({
                    "pattern": pattern_id,
                    "observed_outcomes": [{
                        "verdict_pattern": "Auditor=WARN, Sentinel=PASS",
                        "count": disagreement_count,
                        "last_seen": now_iso(),
                    }],
                    "auto_adjustment": {
                        "action": "review_file_importance_weights",
                        "reason": (
                            f"{disagreement_count} of last {len(recent)} runs showed Auditor WARN + Sentinel PASS. "
                            "This suggests Auditor is flagging non-impactful changes. "
                            "Consider widening file_importance_weights with weight 0.0 for newly-detected non-risk file patterns."
                        ),
                    },
                    "explanation": (
                        f"{disagreement_count} consecutive disagreements between Auditor and Sentinel. "
                        "User should review the per-run reports and either tune weights or accept that this is a real concern."
                    ),
                    **provenance_stamp(agent="model-keeper", run_id=f"learn-{now_iso()}", confidence=0.7),
                })

    # Pattern B: contract-only disagreement
    if len(runs) >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
        recent = runs[-10:]
        contract_only_count = sum(
            1 for r in recent
            if r.get("contract_verdict") == "WARN"
            and r.get("auditor_verdict") == "PASS"
            and r.get("sentinel_verdict") == "PASS"
        )
        if contract_only_count >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
            pattern_id = "contract_only_warn_disagreement"
            if pattern_id not in existing_pattern_ids:
                proposals.append({
                    "pattern": pattern_id,
                    "observed_outcomes": [{
                        "verdict_pattern": "Auditor=PASS, Sentinel=PASS, Contract=WARN",
                        "count": contract_only_count,
                        "last_seen": now_iso(),
                    }],
                    "auto_adjustment": {
                        "action": "investigate_typecheck_baseline",
                        "reason": (
                            f"{contract_only_count} runs showed Contract Sentinel WARN while other agents PASS. "
                            "Likely pre-existing TS errors. Consider running typecheck baseline cleanup."
                        ),
                    },
                    "explanation": "Contract is consistently the lone dissenter — may indicate baseline drift.",
                    **provenance_stamp(agent="model-keeper", run_id=f"learn-{now_iso()}", confidence=0.7),
                })

    # Pattern C: all-3-agents flagging issues (project health alert)
    if len(runs) >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
        recent = runs[-10:]
        all_concerning = sum(
            1 for r in recent
            if r.get("auditor_verdict") in ("WARN", "FAIL")
            and r.get("sentinel_verdict") in ("WARN", "FAIL")
            and r.get("contract_verdict") in ("WARN", "FAIL")
        )
        if all_concerning >= MIN_CONSECUTIVE_FOR_FP_PATTERN:
            pattern_id = "all_three_agents_concerning"
            if pattern_id not in existing_pattern_ids:
                proposals.append({
                    "pattern": pattern_id,
                    "observed_outcomes": [{
                        "verdict_pattern": "Auditor + Sentinel + Contract all WARN/FAIL",
                        "count": all_concerning,
                        "last_seen": now_iso(),
                    }],
                    "auto_adjustment": {
                        "action": "project_health_review",
                        "reason": (
                            f"{all_concerning} consecutive runs had all 3 testers flagging issues. "
                            "This signals systemic problems — recommend a project health review session."
                        ),
                    },
                    "explanation": "All-agent agreement on concerns is the strongest possible negative signal.",
                    **provenance_stamp(agent="model-keeper", run_id=f"learn-{now_iso()}", confidence=0.85),
                })

    return proposals


def propose_patterns(project_root: Path) -> dict[str, Any]:
    """Detect patterns and append them as `learned_patterns` with human_reviewed=False.
    Returns the list of new proposals appended.
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    new_proposals = detect_patterns(project_root)
    if new_proposals:
        keeper.setdefault("learned_patterns", []).extend(new_proposals)
        save_model_keeper(keeper_path, keeper)
    return {
        "proposed_count": len(new_proposals),
        "proposals": new_proposals,
    }


def approve_pattern(project_root: Path, pattern_id: str) -> dict[str, Any] | None:
    """Mark a learned_pattern as human-approved. Activates its auto_adjustment.
    Returns the activated pattern or None if not found.
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    target = None
    for p in keeper.get("learned_patterns", []):
        if p.get("pattern") == pattern_id and not p.get("human_reviewed", False):
            p["human_reviewed"] = True
            p["approved_at"] = now_iso()
            target = p
            break
    if target:
        save_model_keeper(keeper_path, keeper)
    return target


def list_pending_patterns(project_root: Path) -> list[dict[str, Any]]:
    """Return all learned_patterns that haven't been human-approved yet."""
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    return [
        p for p in keeper.get("learned_patterns", [])
        if not p.get("human_reviewed", False)
    ]
