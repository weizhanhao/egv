"""agent_identity.py — Read/write persistent agent identity records.

Each agent has a record at team_identity.agents.<agent_name> tracking:
- agent_id (unique handle scoped to this project)
- joined_team_at (when this agent first ran for this project)
- total_invocations (cumulative across all sessions)
- specializations_learned (list of project-specific insights)
- weight_adjustments_learned (file glob → weight overrides discovered)
- recent_findings (last N findings, capped for size)
- tenure_score (0.0-1.0, function of invocations + age)
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from model_keeper import now_iso

RECENT_FINDINGS_CAP = 20


def new_agent_record(*, agent: str, project: str) -> dict[str, Any]:
    return {
        "agent_id": f"{agent}-{project}-{uuid.uuid4().hex[:8]}",
        "joined_team_at": now_iso(),
        "total_invocations": 0,
        "specializations_learned": [],
        "weight_adjustments_learned": {},
        "recent_findings": [],
        "tenure_score": 0.0,
    }


def read_agent_identity(keeper: dict[str, Any], agent: str) -> dict[str, Any]:
    """Return the agent's identity record. Auto-creates if missing."""
    agents = keeper.setdefault("team_identity", {}).setdefault("agents", {})
    if agent not in agents:
        project = keeper["project_name"]
        agents[agent] = new_agent_record(agent=agent, project=project)
    return agents[agent]


def write_agent_identity(
    keeper: dict[str, Any], agent: str, record: dict[str, Any]
) -> None:
    """Replace the agent's identity record in the keeper."""
    record["recent_findings"] = record.get("recent_findings", [])[-RECENT_FINDINGS_CAP:]
    record["tenure_score"] = _compute_tenure_score(record)
    keeper["team_identity"]["agents"][agent] = record


def _compute_tenure_score(record: dict[str, Any]) -> float:
    """0.0 to 1.0 — function of invocations and time on project."""
    invocations = record.get("total_invocations", 0)
    joined = record.get("joined_team_at")
    if not joined:
        return 0.0
    age_days = max(
        0,
        (datetime.now(timezone.utc) - datetime.fromisoformat(joined.replace("Z", "+00:00"))).days,
    )
    inv_component = min(1.0, invocations / 100.0)
    age_component = min(1.0, age_days / 180.0)
    return round((inv_component + age_component) / 2, 2)
