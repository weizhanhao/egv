"""Tests for llm.py — REAL LLM is required for verification, tests use mocks."""

from unittest.mock import patch

import pytest

from llm import (
    LLMAgent,
    LLMResult,
    LLMUnavailableError,
    _parse_json_response,
    require_api_key,
)


def test_require_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError) as exc:
        require_api_key()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_require_api_key_returns_key_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-xyz")
    assert require_api_key() == "sk-ant-test-key-xyz"


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


def test_investigate_calls_claude_with_mocked_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def fake_call(model, sys_p, user_p, max_tokens):
        return (
            '{"verdict": "PASS", "narrative": "all clear", '
            '"recommendations": [], "confidence_basis": "clean data"}'
        )

    with patch("llm._call_claude_raw", side_effect=fake_call):
        agent = LLMAgent("Tester", "you are a tester")
        result = agent.investigate(
            deterministic_data={"verdict": "PASS"},
            identity_record={"total_invocations": 5, "tenure_score": 0.1},
        )
        assert result.verdict == "PASS"
        assert "all clear" in result.narrative
        assert result.used_llm is True


def test_team_review_uses_team_findings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    captured_user_msg: list[str] = []

    def fake_call(model, sys_p, user_p, max_tokens):
        captured_user_msg.append(user_p)
        return (
            '{"verdict": "WARN", "narrative": "Auditor flagged real risk", '
            '"recommendations": ["add tests"], "confidence_basis": "auditor"}'
        )

    with patch("llm._call_claude_raw", side_effect=fake_call):
        agent = LLMAgent("Sentinel", "you are sentinel")
        my_p1 = LLMResult("PASS", "all tests pass", [], "tests passed", "raw1")
        team = {
            "Auditor": LLMResult(
                "WARN", "12 uncovered lines", ["test them"], "low cov", "raw_a"
            ),
            "Contract": LLMResult(
                "PASS", "no type breakage", [], "tsc clean", "raw_c"
            ),
        }
        result = agent.team_review(my_p1, team, {"total_invocations": 3})
        assert result.verdict == "WARN"
        # The team's findings should appear in the prompt
        assert "Auditor" in captured_user_msg[0]
        assert "Contract" in captured_user_msg[0]
