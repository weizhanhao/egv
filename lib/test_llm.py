"""Tests for llm.py shared LLM infrastructure."""

from llm import LLMAgent, llm_available, fallback_result, LLMResult


def test_llm_available_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_available() is False


def test_fallback_result_structure():
    r = fallback_result("PASS", "all good")
    assert r.verdict == "PASS"
    assert r.narrative == "all good"
    assert r.recommendations == []
    assert r.used_llm is False


def test_llm_agent_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = LLMAgent(role_name="test", role_prompt="you are test")
    result = agent.think(
        deterministic_data={"verdict": "WARN", "summary_line": "needs check"},
        identity_record={"total_invocations": 0, "tenure_score": 0.0},
    )
    assert result.used_llm is False
    assert result.verdict == "WARN"
    assert "needs check" in result.narrative or "LLM unavailable" in result.narrative


def test_parse_response_with_markdown_fences():
    agent = LLMAgent(role_name="t", role_prompt="p")
    raw = '```json\n{"verdict": "PASS", "narrative": "fine"}\n```'
    parsed = agent._parse_response(raw)
    assert parsed["verdict"] == "PASS"


def test_parse_response_plain_json():
    agent = LLMAgent(role_name="t", role_prompt="p")
    raw = '{"verdict": "WARN", "narrative": "concern", "recommendations": ["check x"]}'
    parsed = agent._parse_response(raw)
    assert parsed["verdict"] == "WARN"
    assert parsed["recommendations"] == ["check x"]
