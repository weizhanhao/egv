"""llm.py — Real LLM agent infrastructure for EGV team (v3.1).

Uses `claude -p` CLI in non-interactive mode. This means:
- NO separate API key required — uses Claude Code's existing authentication.
- NO Python SDK install — uses subprocess.
- Works as long as the user has `claude` CLI in PATH (which they do if they
  installed Claude Code).

Architecture:
- Each agent has persistent identity (Model Keeper).
- investigate(): phase-1 independent finding (1 claude -p call).
- team_review(): phase-2 commentary on team findings (1 claude -p call).
- 13 LLM calls per EGV run total (6 specialists x 2 phases + 1 QA Lead).

A thin backward-compatible ``LLMAgent.think()`` wrapper is provided for
lifecycle commands (brainstorm/requirements/design-review/reflection); those
commands are advisory and gracefully fall back when the CLI is unavailable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


HAIKU = "haiku"
SONNET = "sonnet"
DEFAULT_TIMEOUT_S = 120


class LLMUnavailableError(Exception):
    """Raised when `claude` CLI is not in PATH."""


def claude_cli_available() -> bool:
    """Check if the `claude` CLI binary is on PATH."""
    return shutil.which("claude") is not None


def require_claude_cli() -> str:
    """Verify `claude` CLI is available. Returns the binary path.

    EGV v3 requires Claude Code. If you have Claude Code installed,
    `claude` should be in your PATH automatically.
    """
    path = shutil.which("claude")
    if path is None:
        raise LLMUnavailableError(
            "The `claude` CLI was not found in PATH.\n"
            "EGV v3 is a real LLM agent team — every agent uses Claude to reason.\n"
            "Install Claude Code: https://docs.claude.com/claude-code\n"
            "After install, `claude --version` should work."
        )
    return path


def llm_available() -> bool:
    """Lightweight check used by lifecycle commands (brainstorm/etc.)."""
    return claude_cli_available()


@dataclass
class LLMResult:
    verdict: str  # PASS | WARN | FAIL
    narrative: str
    recommendations: list[str]
    confidence_basis: str
    raw_response: str
    cost_usd: float = 0.0
    duration_ms: int = 0
    used_llm: bool = True  # lifecycle fallback may set False


def fallback_result(verdict: str, narrative: str = "") -> LLMResult:
    """Construct an advisory result when LLM is unavailable (lifecycle only)."""
    return LLMResult(
        verdict=verdict,
        narrative=narrative or "(LLM unavailable — deterministic verdict only)",
        recommendations=[],
        confidence_basis="deterministic data only; LLM reasoning skipped",
        raw_response="",
        cost_usd=0.0,
        duration_ms=0,
        used_llm=False,
    )


def _call_claude_cli(
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1500,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[str, float, int]:
    """Invoke `claude -p` non-interactively. Returns (result_text, cost_usd, duration_ms).

    Combines system_prompt + user_message into a single prompt (claude -p doesn't
    take separate system/user roles — but the model handles a combined prompt well).
    """
    require_claude_cli()

    combined = f"{system_prompt}\n\n---\n\n{user_message}"

    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=combined,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMUnavailableError(
            f"`claude -p` timed out after {timeout_s}s"
        ) from exc

    if proc.returncode != 0:
        raise LLMUnavailableError(
            f"`claude -p` exited {proc.returncode}: {proc.stderr[:500]}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LLMUnavailableError(
            f"`claude -p` returned non-JSON output: {proc.stdout[:300]}"
        ) from exc

    if envelope.get("is_error"):
        raise LLMUnavailableError(
            f"`claude -p` reported error: {envelope.get('result', 'unknown')}"
        )

    result_text = envelope.get("result", "")
    cost = float(envelope.get("total_cost_usd", 0.0))
    duration = int(envelope.get("duration_ms", 0))
    return result_text, cost, duration


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from response, tolerating markdown fences and prose wrappers."""
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
            v = "WARN"
        return {
            "verdict": v,
            "narrative": raw[:500],
            "recommendations": [],
            "confidence_basis": "(parse failed, defaulted to WARN)",
        }


class LLMAgent:
    """A REAL LLM-driven agent with persistent project identity.

    Each agent has TWO methods for verification:
    - investigate(): phase-1 independent findings
    - team_review(): phase-2 commentary on the whole team's findings

    The legacy ``think()`` method is preserved for lifecycle commands
    (brainstorm/requirements/design-review/reflection) which are advisory and
    permit graceful fallback when the `claude` CLI is unavailable.

    Identity persists across runs via Model Keeper. NOT a subagent — uses
    `claude -p` subprocess with the user's existing Claude Code auth.
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
        prompt = self._build_investigate_prompt(
            deterministic_data, identity_record, project_context
        )
        raw, cost, duration = _call_claude_cli(
            self.model, self.role_prompt, prompt, self.max_tokens
        )
        parsed = _parse_json_response(raw)
        return LLMResult(
            verdict=parsed.get("verdict", "WARN"),
            narrative=parsed.get("narrative", ""),
            recommendations=parsed.get("recommendations", []),
            confidence_basis=parsed.get("confidence_basis", ""),
            raw_response=raw,
            cost_usd=cost,
            duration_ms=duration,
            used_llm=True,
        )

    def team_review(
        self,
        my_phase1: LLMResult,
        team_findings: dict,  # {role_name: LLMResult}
        identity_record: dict,
    ) -> LLMResult:
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
        raw, cost, duration = _call_claude_cli(
            self.model, review_system, prompt, self.max_tokens
        )
        parsed = _parse_json_response(raw)
        return LLMResult(
            verdict=parsed.get("verdict", my_phase1.verdict),
            narrative=parsed.get("narrative", ""),
            recommendations=parsed.get("recommendations", []),
            confidence_basis=parsed.get("confidence_basis", ""),
            raw_response=raw,
            cost_usd=cost,
            duration_ms=duration,
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
        """Advisory call — graceful fallback when CLI unavailable.

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
