"""llm.py — Real LLM agent infrastructure for EGV team (v3).

CRITICAL design decisions:
1. LLM is MANDATORY for verification runs. No graceful fallback. Missing API key = clear error.
2. No anthropic SDK required — uses raw urllib HTTPS POST to bypass PEP 668.
3. Each agent is invoked TWICE per run: once independently (phase 1) and once
   for team review (phase 2). This is the real team collaboration.
4. Identity persists across runs via Model Keeper; agent has tenure + accumulated
   memory. NOT a subagent — no ephemeral spawn.

For lifecycle commands (brainstorm/requirements/design-review/reflection), a thin
backward-compatible ``LLMAgent.think()`` wrapper is provided that gracefully falls
back when no API key is configured — those commands are advisory, not verification.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class LLMUnavailableError(Exception):
    """Raised when ANTHROPIC_API_KEY is missing. EGV v3 verification requires LLM."""


def require_api_key() -> str:
    """Return the configured API key or raise LLMUnavailableError."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMUnavailableError(
            "ANTHROPIC_API_KEY environment variable is required.\n"
            "EGV v3 is a real LLM agent team — every agent uses Claude to reason.\n"
            "Get your key at https://console.anthropic.com/ and export it:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return key


def llm_available() -> bool:
    """Lightweight check used by lifecycle commands (brainstorm/etc.)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@dataclass
class LLMResult:
    """Structured response from an LLM agent call."""

    verdict: str  # PASS | WARN | FAIL
    narrative: str
    recommendations: list[str]
    confidence_basis: str
    raw_response: str
    used_llm: bool = True  # phase-1/phase-2 are always True; lifecycle fallback may set False


def fallback_result(verdict: str, narrative: str = "") -> LLMResult:
    """Construct an advisory result when LLM is unavailable (lifecycle only)."""
    return LLMResult(
        verdict=verdict,
        narrative=narrative or "(LLM unavailable — deterministic verdict only)",
        recommendations=[],
        confidence_basis="deterministic data only; LLM reasoning skipped",
        raw_response="",
        used_llm=False,
    )


def _call_claude_raw(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1500,
) -> str:
    """Call Anthropic API via raw HTTPS — no SDK required.

    Raises LLMUnavailableError if API key is missing.
    Raises urllib.error.HTTPError on API errors.
    """
    api_key = require_api_key()
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "Anthropic-Version": API_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content_blocks = body.get("content", [])
    text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "".join(text_parts).strip()


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from response, tolerating markdown fences."""
    text = raw.strip()
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
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        text_low = text.lower()
        if "fail" in text_low:
            v = "FAIL"
        elif "warn" in text_low:
            v = "WARN"
        else:
            v = "WARN"  # safe default when parse fails
        return {
            "verdict": v,
            "narrative": raw[:500],
            "recommendations": [],
            "confidence_basis": "(parse failed, defaulted to WARN)",
        }


class LLMAgent:
    """A REAL LLM-driven agent with persistent project identity.

    Each agent has TWO main methods for verification:
    - investigate(): phase-1 independent findings
    - team_review(): phase-2 commentary on the whole team's findings

    The legacy ``think()`` method is preserved for lifecycle commands
    (brainstorm/requirements/design-review/reflection) which are advisory and
    permit graceful fallback when no API key is set.

    Identity persists across runs via Model Keeper. The agent's tenure (total
    invocations, accumulated specializations, recent findings) is passed into
    every prompt so the LLM reasons WITH that history, not as a fresh subagent.
    """

    def __init__(
        self,
        role_name: str,
        role_prompt: str,
        model: str = HAIKU,
        max_tokens: int = 1500,
    ) -> None:
        self.role_name = role_name
        self.role_prompt = role_prompt
        self.model = model
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # v3 mandatory two-phase methods
    # ------------------------------------------------------------------

    def investigate(
        self,
        deterministic_data: dict,
        identity_record: dict,
        project_context: Optional[str] = None,
    ) -> LLMResult:
        """Phase 1: investigate independently. Returns structured finding."""
        prompt = self._build_investigate_prompt(
            deterministic_data, identity_record, project_context
        )
        raw = _call_claude_raw(
            self.model, self.role_prompt, prompt, self.max_tokens
        )
        parsed = _parse_json_response(raw)
        return LLMResult(
            verdict=parsed.get("verdict", "WARN"),
            narrative=parsed.get("narrative", ""),
            recommendations=parsed.get("recommendations", []),
            confidence_basis=parsed.get("confidence_basis", ""),
            raw_response=raw,
            used_llm=True,
        )

    def team_review(
        self,
        my_phase1: LLMResult,
        team_findings: dict,  # {agent_role: LLMResult}
        identity_record: dict,
    ) -> LLMResult:
        """Phase 2: review the team's findings, respond / escalate / agree."""
        prompt = self._build_review_prompt(my_phase1, team_findings, identity_record)
        review_system = (
            self.role_prompt
            + "\n\n"
            + "You are now in the TEAM REVIEW phase. The team has shared their independent "
            "findings. Your job: look at the cross-cutting picture. Do your phase-1 "
            "conclusions still hold? Did anyone find something that requires you to "
            "escalate? Did anyone find a false positive you can help reframe? Speak as "
            "a teammate — reference specific agents by role when relevant."
        )
        raw = _call_claude_raw(
            self.model, review_system, prompt, self.max_tokens
        )
        parsed = _parse_json_response(raw)
        return LLMResult(
            verdict=parsed.get("verdict", my_phase1.verdict),
            narrative=parsed.get("narrative", ""),
            recommendations=parsed.get("recommendations", []),
            confidence_basis=parsed.get("confidence_basis", ""),
            raw_response=raw,
            used_llm=True,
        )

    # ------------------------------------------------------------------
    # Backward-compatible advisory call for lifecycle commands
    # ------------------------------------------------------------------

    def think(
        self,
        deterministic_data: dict,
        identity_record: dict,
        project_context: Optional[str] = None,
    ) -> LLMResult:
        """Advisory call — graceful fallback when no API key.

        Used by lifecycle commands (brainstorm/requirements/design-review/
        reflection) which are NOT gated by mandatory LLM. Verification flows
        must call ``investigate()`` / ``team_review()`` instead.
        """
        det_verdict = deterministic_data.get("verdict", "WARN")
        det_narrative = deterministic_data.get("summary_line", "")
        if not llm_available():
            return fallback_result(det_verdict, det_narrative)
        try:
            return self.investigate(
                deterministic_data, identity_record, project_context
            )
        except Exception as exc:  # noqa: BLE001 — advisory path
            print(
                f"[{self.role_name}-llm] advisory call failed: {exc}",
                file=sys.stderr,
            )
            return fallback_result(det_verdict, det_narrative)

    # Compatibility shim for older tests
    def _parse_response(self, raw: str) -> dict:
        return _parse_json_response(raw)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_investigate_prompt(
        self,
        deterministic_data: dict,
        identity_record: dict,
        project_context: Optional[str],
    ) -> str:
        invocations = identity_record.get("total_invocations", 0)
        tenure = identity_record.get("tenure_score", 0.0)
        recent = identity_record.get("recent_findings", [])[-3:]
        specs = identity_record.get("specializations_learned", [])[-5:]

        recent_str = (
            "\n".join(
                f"  - {f.get('run_id', '?')[:24]}: verdict={f.get('verdict', '?')}, "
                f"obs={f.get('key_observation', '')[:120]}"
                for f in recent
            )
            or "  (no prior findings — first invocation on this project)"
        )
        specs_str = (
            "\n".join(f"  - {s}" for s in specs) or "  (none learned yet)"
        )

        det_summary = json.dumps(
            {k: v for k, v in deterministic_data.items()},
            indent=2,
            default=str,
        )[:3000]

        ctx_section = ""
        if project_context:
            ctx_section = (
                f"\nProject context:\n```\n{project_context[:2000]}\n```\n"
            )

        return f"""Your tenure on this project:
- Total prior invocations: {invocations} | Tenure score: {tenure:.2f}
- Recent findings:
{recent_str}
- Specializations learned:
{specs_str}
{ctx_section}
Your sensors gathered the following deterministic data this run:
{det_summary}

Investigate. Produce a structured judgment as strict JSON:
{{
  "verdict": "PASS" | "WARN" | "FAIL",
  "narrative": "2-4 sentences. What you observed. Reference specific files/numbers/symptoms. Speak in first person as your role.",
  "recommendations": ["Concrete next actions if WARN/FAIL. Empty list if PASS."],
  "confidence_basis": "1 sentence explaining how the sensor data + your historical context support this verdict."
}}

Output JSON only, no preamble."""

    def _build_review_prompt(
        self,
        my_phase1: LLMResult,
        team_findings: dict,
        identity_record: dict,
    ) -> str:
        team_summary = "\n\n".join(
            f"## {role} said:\n"
            f"  verdict: {result.verdict}\n"
            f"  narrative: {result.narrative}\n"
            f"  recommendations: {result.recommendations}"
            for role, result in team_findings.items()
        )

        return f"""TEAM REVIEW PHASE.

Your phase-1 finding (which you submitted independently):
  verdict: {my_phase1.verdict}
  narrative: {my_phase1.narrative}
  recommendations: {my_phase1.recommendations}

The full team's phase-1 findings:

{team_summary}

Now reflect. Possible responses:
- STAND BY: your verdict still holds, the team agrees, brief confirmation
- ESCALATE: another agent found something that forces you to upgrade your verdict (PASS->WARN or WARN->FAIL)
- DE-ESCALATE: your phase-1 was alarmist; another agent reframed it as a false positive
- CROSS-CONCERN: you noticed a pattern across multiple agents that nobody named explicitly

Respond as strict JSON:
{{
  "verdict": "PASS" | "WARN" | "FAIL",
  "narrative": "2-4 sentences as a teammate. Reference other agents by role. Say what changed (or didn't) and why.",
  "recommendations": ["Updated actions. Can be empty if all clear."],
  "confidence_basis": "1 sentence — what in the team's findings supports this final position."
}}

Output JSON only."""
