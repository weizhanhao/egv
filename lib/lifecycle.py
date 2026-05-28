"""lifecycle.py — Lifecycle command implementations for EGV v1.

Each function appends a structured entry to the appropriate Model Keeper bucket:
- log_brainstorm → lifecycle_artifacts.ideas_under_consideration
- log_requirements → lifecycle_artifacts.ideas_under_consideration (annotated with testability gaps)
- log_design_review → lifecycle_artifacts.design_reviews_completed
- log_reflection → lifecycle_artifacts.post_merge_reflections

Each entry carries provenance (added_at, added_by_agent, added_in_run, confidence, human_reviewed).
v1 uses template-based "team interrogation" — v2 will add real LLM critique.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re

from agent_identity import read_agent_identity, write_agent_identity
from model_keeper import (
    keeper_path_for_project_root,
    load_model_keeper,
    now_iso,
    provenance_stamp,
    save_model_keeper,
)


def _make_entry_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


def log_brainstorm(project_root: Path, idea: str) -> dict[str, Any]:
    """Append an idea to ideas_under_consideration. Three team members 'speak':
    Cartographer, Auditor, Contract Sentinel. v3: questions are LLM-generated
    when ANTHROPIC_API_KEY is set; otherwise falls back to templates.
    """
    from llm import LLMAgent, HAIKU, SONNET, llm_available

    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    entry_id = _make_entry_id("idea")

    existing_flows = [f["name"] for f in keeper.get("critical_flows", [])]
    blind_spots_summary = "; ".join(
        bs.get("description", "")[:100] for bs in keeper.get("known_blind_spots", [])[:5]
    )

    # Build base prompt context for all three agents
    context_summary = f"""
Current project state:
- Project: {keeper.get('project_name', 'unknown')}
- Critical flows ({len(existing_flows)}): {', '.join(existing_flows[:8])}
- Known blind spots: {blind_spots_summary or '(none yet)'}
- Total verification runs so far: {len(keeper.get('run_history', []))}
"""

    # Cartographer
    cartographer_rec = read_agent_identity(keeper, "core-flow-cartographer")
    cartographer_rec["total_invocations"] += 1
    if llm_available():
        cart_agent = LLMAgent(
            role_name="Core Flow Cartographer",
            role_prompt="You are the Core Flow Cartographer. You maintain the list of user-visible behaviors that MUST work. You ask specific, pointed questions about whether a new idea introduces new critical user flows. Be concrete — name flows.",
            model=HAIKU,
        )
        result = cart_agent.think(
            deterministic_data={"verdict": "BRAINSTORM", "idea": idea, "summary_line": context_summary},
            identity_record=cartographer_rec,
        )
        cartographer_question = result.narrative if result.used_llm else (
            f"Does this idea introduce a new user-visible flow? Current critical_flows: {existing_flows}. Suggested action: name a new flow and propose its test_path."
        )
    else:
        cartographer_question = (
            f"Does this idea introduce a new user-visible flow? "
            f"Current critical_flows: {existing_flows}. "
            "Suggested action: name a new flow and propose its test_path."
        )
    cartographer_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "BRAINSTORM",
        "confidence": None, "key_observation": cartographer_question[:200],
    })
    write_agent_identity(keeper, "core-flow-cartographer", cartographer_rec)

    # Auditor
    auditor_rec = read_agent_identity(keeper, "regression-auditor")
    auditor_rec["total_invocations"] += 1
    if llm_available():
        aud_agent = LLMAgent(
            role_name="Regression Auditor",
            role_prompt="You are the Regression Auditor. You think in terms of blast radius and historical risk hotspots. Given an idea, you predict WHICH modules it will touch and which of those have low coverage. Be specific — reference real file patterns.",
            model=HAIKU,
        )
        result = aud_agent.think(
            deterministic_data={"verdict": "BRAINSTORM", "idea": idea, "summary_line": context_summary},
            identity_record=auditor_rec,
        )
        auditor_question = result.narrative if result.used_llm else (
            "Which existing modules in the project is this idea most likely to touch? "
            "Cross-check against blast-radius history — modules with low coverage become risk hotspots."
        )
    else:
        auditor_question = (
            "Which existing modules in the project is this idea most likely to touch? "
            "Cross-check against blast-radius history — modules with low coverage become risk hotspots."
        )
    auditor_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "BRAINSTORM",
        "confidence": None, "key_observation": auditor_question[:200],
    })
    write_agent_identity(keeper, "regression-auditor", auditor_rec)

    # Contract Sentinel
    contract_rec = read_agent_identity(keeper, "contract-sentinel")
    contract_rec["total_invocations"] += 1
    if llm_available():
        contract_agent = LLMAgent(
            role_name="Contract Sentinel",
            role_prompt="You are the Contract Sentinel. You think about public API surface, schema breakage, and downstream consumers. Given an idea, you anticipate which contracts might break and who depends on them.",
            model=HAIKU,
        )
        result = contract_agent.think(
            deterministic_data={"verdict": "BRAINSTORM", "idea": idea, "summary_line": context_summary},
            identity_record=contract_rec,
        )
        contract_question = result.narrative if result.used_llm else (
            "Does this idea imply any public API or data schema change? "
            "If so, list downstream consumers BEFORE coding."
        )
    else:
        contract_question = (
            "Does this idea imply any public API or data schema change? "
            "If so, list downstream consumers BEFORE coding."
        )
    contract_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "BRAINSTORM",
        "confidence": None, "key_observation": contract_question[:200],
    })
    write_agent_identity(keeper, "contract-sentinel", contract_rec)

    entry = {
        "entry_id": entry_id,
        "idea": idea,
        "team_questions": {
            "cartographer": cartographer_question,
            "auditor": auditor_question,
            "contract_sentinel": contract_question,
        },
        "llm_powered": llm_available(),
        "status": "open",
        **provenance_stamp(agent="qa-lead", run_id=entry_id),
    }
    keeper.setdefault("lifecycle_artifacts", {}).setdefault("ideas_under_consideration", []).append(entry)
    save_model_keeper(keeper_path, keeper)
    return entry


def log_requirements(project_root: Path, requirements_text: str, source: str | None = None) -> dict[str, Any]:
    """Annotate a requirements doc with testability gaps.
    Heuristic detection of testability concerns:
    - Mentions 'user' + 'click/save/submit' → needs interaction fixture
    - Mentions 'login' or 'auth' → needs auth fixture
    - Mentions 'API' or 'endpoint' → contract concern
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    entry_id = _make_entry_id("req")

    text_lower = requirements_text.lower()
    gaps = []
    if re.search(r"\buser\b.+\b(clicks?|saves?|submits?|drags?|types?)\b", text_lower):
        gaps.append({
            "category": "interaction",
            "concern": "Requirement implies user interaction. Needs a Playwright/RTL fixture. Check critical_flows for an existing fit; if none, propose a new flow.",
        })
    if re.search(r"\b(login|sign[\s-]?in|auth)\b", text_lower):
        gaps.append({
            "category": "auth",
            "concern": "Auth-aware test fixture needed. Verify Model Keeper has auth setup in critical_flows.",
        })
    if re.search(r"\b(api|endpoint|response|schema)\b", text_lower):
        gaps.append({
            "category": "contract",
            "concern": "API/schema mention — Contract Sentinel should review the design before implementation.",
        })
    if not gaps:
        gaps.append({
            "category": "no-gaps-detected",
            "concern": "v1 heuristic found no testability gaps. Manual review recommended for non-obvious risks.",
        })

    # Sentinel records the review
    sentinel_rec = read_agent_identity(keeper, "critical-path-sentinel")
    sentinel_rec["total_invocations"] += 1
    sentinel_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "REQUIREMENTS_REVIEW",
        "confidence": None, "key_observation": f"{len(gaps)} testability concern(s) raised",
    })
    write_agent_identity(keeper, "critical-path-sentinel", sentinel_rec)

    entry = {
        "entry_id": entry_id,
        "source": source,
        "requirements_excerpt": requirements_text[:500],
        "testability_gaps": gaps,
        "status": "open",
        **provenance_stamp(agent="critical-path-sentinel", run_id=entry_id),
    }
    keeper.setdefault("lifecycle_artifacts", {}).setdefault("ideas_under_consideration", []).append(entry)
    save_model_keeper(keeper_path, keeper)
    return entry


def log_design_review(project_root: Path, design_text: str, source: str | None = None) -> dict[str, Any]:
    """Contract Sentinel reviews API/schema design."""
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    entry_id = _make_entry_id("design")

    text_lower = design_text.lower()
    findings = []
    if "breaking change" in text_lower or "deprecate" in text_lower:
        findings.append("Design mentions breaking change — list downstream consumers explicitly.")
    if re.search(r"\bschema\b", text_lower):
        findings.append("Schema change — check Model Keeper's contract history for similar past changes.")
    if not findings:
        findings.append("v1 heuristic found no major contract risks. Manual review still recommended.")

    contract_rec = read_agent_identity(keeper, "contract-sentinel")
    contract_rec["total_invocations"] += 1
    contract_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "DESIGN_REVIEW",
        "confidence": None, "key_observation": findings[0][:200],
    })
    write_agent_identity(keeper, "contract-sentinel", contract_rec)

    entry = {
        "entry_id": entry_id,
        "source": source,
        "design_excerpt": design_text[:500],
        "findings": findings,
        "status": "completed",
        **provenance_stamp(agent="contract-sentinel", run_id=entry_id),
    }
    keeper.setdefault("lifecycle_artifacts", {}).setdefault("design_reviews_completed", []).append(entry)
    save_model_keeper(keeper_path, keeper)
    return entry


def log_reflection(project_root: Path) -> dict[str, Any]:
    """Post-merge reflection. Surface insights from recent run_history.
    Looks at last N runs and flags patterns worth promoting to learned_patterns.
    """
    keeper_path = keeper_path_for_project_root(project_root)
    keeper = load_model_keeper(keeper_path)
    entry_id = _make_entry_id("reflect")

    runs = keeper.get("run_history", [])[-10:]
    auditor_rec = read_agent_identity(keeper, "regression-auditor")
    sentinel_rec = read_agent_identity(keeper, "critical-path-sentinel")
    auditor_rec["total_invocations"] += 1
    sentinel_rec["total_invocations"] += 1

    insights = []
    if runs:
        disagreement_count = sum(1 for r in runs if not r.get("agreement", True))
        if disagreement_count >= 5:
            insights.append(
                f"{disagreement_count}/{len(runs)} recent runs had Auditor/Sentinel disagreement. "
                "Consider tuning Auditor weights — see learned_patterns proposals."
            )
        verdict_counts: dict[str, int] = {}
        for r in runs:
            v = r.get("final_verdict", "?")
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
        insights.append(f"Recent run verdict distribution: {verdict_counts}")
    else:
        insights.append("No prior runs to reflect on. Skill is just starting on this project.")

    auditor_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "REFLECTION",
        "confidence": None, "key_observation": insights[0][:200],
    })
    sentinel_rec["recent_findings"].append({
        "run_id": entry_id, "verdict": "REFLECTION",
        "confidence": None, "key_observation": insights[0][:200],
    })
    write_agent_identity(keeper, "regression-auditor", auditor_rec)
    write_agent_identity(keeper, "critical-path-sentinel", sentinel_rec)

    entry = {
        "entry_id": entry_id,
        "insights": insights,
        "runs_analyzed": len(runs),
        **provenance_stamp(agent="qa-lead", run_id=entry_id),
    }
    keeper.setdefault("lifecycle_artifacts", {}).setdefault("post_merge_reflections", []).append(entry)
    save_model_keeper(keeper_path, keeper)
    return entry
