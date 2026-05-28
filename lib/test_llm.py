"""Tests for llm.py — uses `claude -p` CLI under the hood, tests use subprocess mocks."""

import json
import subprocess
from unittest.mock import patch

import pytest

from llm import (
    LLMAgent,
    LLMResult,
    LLMUnavailableError,
    _parse_json_response,
    claude_cli_available,
    require_claude_cli,
)


def test_claude_cli_available_when_present():
    # In the test env, `claude` may or may not be in PATH.
    # Just verify the function returns a bool without crashing.
    result = claude_cli_available()
    assert isinstance(result, bool)


def test_require_claude_cli_raises_when_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(LLMUnavailableError) as exc:
        require_claude_cli()
    assert "claude" in str(exc.value).lower()


def test_parse_response_with_fences():
    raw = '```json\n{"verdict": "PASS", "narrative": "fine"}\n```'
    p = _parse_json_response(raw)
    assert p["verdict"] == "PASS"


def test_parse_response_plain():
    raw = '{"verdict": "WARN", "narrative": "concern", "recommendations": ["x"]}'
    p = _parse_json_response(raw)
    assert p["verdict"] == "WARN"


def test_parse_response_fallback_on_garbage():
    raw = "no json here, just prose. but this seems bad. fail."
    p = _parse_json_response(raw)
    assert p["verdict"] in ("PASS", "WARN", "FAIL")


def test_investigate_calls_cli_with_mocked_subprocess(monkeypatch):
    """Verify investigate() calls subprocess and parses CLI envelope."""
    fake_envelope = {
        "is_error": False,
        "result": (
            '{"verdict": "PASS", "narrative": "all clear", '
            '"recommendations": [], "confidence_basis": "clean data"}'
        ),
        "total_cost_usd": 0.001,
        "duration_ms": 1500,
    }
    fake_proc = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps(fake_envelope),
        stderr="",
    )
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    with patch("subprocess.run", return_value=fake_proc):
        agent = LLMAgent("Tester", "you are a tester")
        result = agent.investigate(
            deterministic_data={"verdict": "PASS"},
            identity_record={"total_invocations": 5, "tenure_score": 0.1},
        )
        assert result.verdict == "PASS"
        assert "all clear" in result.narrative
        assert result.cost_usd == 0.001


def test_team_review_uses_team_findings(monkeypatch):
    fake_envelope = {
        "is_error": False,
        "result": (
            '{"verdict": "WARN", "narrative": "Auditor flagged real risk", '
            '"recommendations": ["add tests"], "confidence_basis": "auditor"}'
        ),
        "total_cost_usd": 0.002,
        "duration_ms": 2000,
    }
    captured_input: list[str] = []

    def fake_run(*args, **kwargs):
        captured_input.append(kwargs.get("input", ""))
        return subprocess.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps(fake_envelope),
            stderr="",
        )

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    with patch("subprocess.run", side_effect=fake_run):
        agent = LLMAgent("Sentinel", "you are sentinel")
        my_p1 = LLMResult("PASS", "all tests pass", [], "tests passed", "raw1", 0.0, 0)
        team = {
            "Auditor": LLMResult(
                "WARN", "12 uncovered lines", ["test them"], "low cov", "raw_a", 0.0, 0
            ),
            "Contract": LLMResult(
                "PASS", "no type breakage", [], "tsc clean", "raw_c", 0.0, 0
            ),
        }
        result = agent.team_review(my_p1, team, {"total_invocations": 3})
        assert result.verdict == "WARN"
        assert "Auditor" in captured_input[0]
        assert "Contract" in captured_input[0]


def test_cli_error_raises_unavailable(monkeypatch):
    fake_proc = subprocess.CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout="",
        stderr="auth failed",
    )
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    with patch("subprocess.run", return_value=fake_proc):
        from llm import _call_claude_cli

        with pytest.raises(LLMUnavailableError) as exc:
            _call_claude_cli("haiku", "sys", "user")
        assert "claude -p" in str(exc.value)
