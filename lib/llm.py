"""llm.py — Shared LLM infrastructure for EGV agents.

Each agent uses LLMAgent.think() to produce verdict + narrative + recommendations
on top of their deterministic data. Graceful fallback when ANTHROPIC_API_KEY
or anthropic SDK is unavailable.

Model selection:
- claude-haiku-4-5-20251001 for cheap/fast agents (Security, A11y, Perf)
- claude-sonnet-4-6 for nuanced reasoning (QA Lead, Cartographer, Auditor narrative)

Cost budget: ~8 LLM calls per full EGV run, capped at 1500 tokens output each.
At Haiku pricing that's ~$0.01-0.05 per verification run.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def llm_available() -> bool:
    """Check if LLM calls are possible — API key set AND SDK importable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class LLMResult:
    """Structured response from an LLM agent call."""
    verdict: str  # PASS | WARN | FAIL
    narrative: str
    recommendations: list[str]
    confidence_basis: str
    raw_response: str
    used_llm: bool  # True if real LLM call, False if fallback


def fallback_result(verdict: str, narrative: str = "") -> LLMResult:
    """Construct a deterministic-only result when LLM is unavailable."""
    return LLMResult(
        verdict=verdict,
        narrative=narrative or "(LLM unavailable — deterministic verdict only)",
        recommendations=[],
        confidence_basis="deterministic data only; LLM reasoning skipped",
        raw_response="",
        used_llm=False,
    )


class LLMAgent:
    """Persistent LLM-driven agent with role prompt + identity awareness.

    Each invocation:
    1. Reads agent identity from Model Keeper (recent_findings, specializations_learned, tenure)
    2. Constructs role-aware prompt + the deterministic data + identity history
    3. Calls Claude
    4. Parses structured JSON response
    5. Returns LLMResult

    This is NOT a subagent — there is no per-task spawn. The agent's identity
    persists across runs via Model Keeper. Each LLMAgent instance just borrows
    that identity for one call.
    """

    def __init__(
        self,
        role_name: str,
        role_prompt: str,
        model: str = HAIKU,
        max_tokens: int = 1500,
    ):
        self.role_name = role_name
        self.role_prompt = role_prompt
        self.model = model
        self.max_tokens = max_tokens

    def think(
        self,
        deterministic_data: dict,
        identity_record: dict,
        project_context: Optional[str] = None,
    ) -> LLMResult:
        """Produce an LLM-driven judgment on top of deterministic data.

        Returns LLMResult. If LLM unavailable, returns fallback_result based
        on the deterministic verdict.
        """
        # Extract deterministic verdict to use as fallback
        det_verdict = deterministic_data.get("verdict", "WARN")
        det_narrative = deterministic_data.get("summary_line", "")

        if not llm_available():
            return fallback_result(det_verdict, det_narrative)

        try:
            import anthropic
        except ImportError:
            return fallback_result(det_verdict, det_narrative)

        prompt = self._build_prompt(deterministic_data, identity_record, project_context)

        try:
            client = anthropic.Anthropic()
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.role_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            parsed = self._parse_response(raw)
            return LLMResult(
                verdict=parsed.get("verdict", det_verdict),
                narrative=parsed.get("narrative", det_narrative),
                recommendations=parsed.get("recommendations", []),
                confidence_basis=parsed.get("confidence_basis", ""),
                raw_response=raw,
                used_llm=True,
            )
        except Exception as e:
            print(f"[{self.role_name}-llm] API call failed: {e}", file=sys.stderr)
            return fallback_result(det_verdict, det_narrative)

    def _build_prompt(
        self,
        deterministic_data: dict,
        identity_record: dict,
        project_context: Optional[str],
    ) -> str:
        invocations = identity_record.get("total_invocations", 0)
        tenure = identity_record.get("tenure_score", 0.0)
        recent_findings = identity_record.get("recent_findings", [])[-3:]
        specializations = identity_record.get("specializations_learned", [])[-5:]

        recent_str = "\n".join(
            f"  - run {f.get('run_id', '?')[:20]}: verdict={f.get('verdict', '?')}, "
            f"observation={f.get('key_observation', '')[:120]}"
            for f in recent_findings
        ) or "  (no prior findings — first invocation)"

        specs_str = "\n".join(f"  - {s}" for s in specializations) or "  (none learned yet)"

        det_summary = json.dumps(
            {k: v for k, v in deterministic_data.items() if k != "raw_response"},
            indent=2,
            default=str,
        )[:3000]

        ctx_section = ""
        if project_context:
            ctx_section = f"\n\nProject context (excerpt):\n```\n{project_context[:2000]}\n```\n"

        return f"""You are {self.role_name}, a persistent member of this project's EGV (Evidence-Grounded Verification) team.

Your tenure on this project:
- Total prior invocations: {invocations}
- Tenure score: {tenure:.2f}
- Recent findings (last 3):
{recent_str}
- Specializations learned on this project:
{specs_str}

You have been given DETERMINISTIC data computed by your sensors (regex/coverage/typecheck/etc.). Your job is to interpret it in the context of this project, accumulated knowledge, and produce a judgment.{ctx_section}

Deterministic data from this invocation:
{det_summary}

Respond as a strict JSON object with these keys:
{{
  "verdict": "PASS" | "WARN" | "FAIL",
  "narrative": "1-3 sentences explaining what you observed in plain English. Reference specific findings.",
  "recommendations": ["1-3 actionable next steps if WARN/FAIL, empty array if PASS"],
  "confidence_basis": "1 sentence explaining why you chose this verdict — cite specific deterministic facts"
}}

Output ONLY the JSON, no preamble. Be honest — if the deterministic data is ambiguous, say WARN. If it's clearly clean, say PASS. Never claim FAIL without specific evidence in the data."""

    def _parse_response(self, raw: str) -> dict:
        """Parse JSON from response, tolerating wrapping markdown fences."""
        text = raw.strip()
        # Strip ```json fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find a JSON object in the response
            import re
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            return {"narrative": text[:500]}
